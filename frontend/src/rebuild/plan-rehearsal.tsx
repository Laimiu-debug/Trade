import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { sameMarketSymbol } from './market-symbols'

export type RehearsalRow = { code: string; name: string; qty: number; note: string; price?: string }
type Calendar = { revision: number; source: string | null; start_date: string | null; end_date: string | null; sha256: string | null; days: Array<{ date: string; is_open: boolean }> }
type DateInfo = { suggested_date: string | null; target_status: 'open' | 'closed' | 'unknown'; calendar: Calendar; method: string }
type Baseline = { sha256: string; source: string; cash: string | null; snapshot_revision: number | null; account_input_revision: number; positions: Array<{ code: string; name: string; qty: number }>; quality_flags: string[]; method: string; fee_settings: { version: number; config: Record<string, string> } }
type Dataset = { id: string; symbol: string; last_date: string; availability_quality: string }
type Preview = { sha256: string; remaining_cash: string | null; estimated_fees: string | null; projected_total: string | null; position_value: string | null; cash_status: string; quality_flags: string[]; method: string; rows: Array<{ code: string; current_qty: number; target_qty: number; quantity_delta: number; price: string | null; fees: string | null; cash_delta: string | null; quality_flags: string[]; price_evidence: { source: string; quote_date: string | null } | null }> }
const labels: Record<string, string> = {
  snapshot_missing: '当日无确认快照', snapshot_cash_missing: '快照现金未填写', snapshot_positions_missing: '快照缺持仓明细', initial_capital_missing: '流水缺初始资金', oversold_trade_history: '历史流水存在超卖', negative_inferred_cash: '推导现金为负', execution_price_missing: '缺预计成交价', valuation_price_missing: '缺持仓估值价', quote_unavailable_at_decision: '复盘当时没有可见价格', stale_quote: '引用此前价格', historical_availability_unknown: '历史可得时间未知', insufficient_cash: '预计现金不足', sell_proceeds_required_first: '买入依赖先卖出回款', buy_quantity_not_100_multiple: '买入差额非 100 股倍数', partial_sell_not_100_multiple: '部分卖出非 100 股倍数',
}
const flagsText = (flags: string[]) => flags.map(flag => labels[flag] || flag).join('；')
const money = (value: string | null) => value === null ? '未知' : `¥ ${value}`

export function PlanDateControl({ day, value, onChange }: { day: string; value: string | null; onChange: (day: string | null) => void }) {
  const [info, setInfo] = useState<DateInfo | null>(null)
  const [calendar, setCalendar] = useState<Calendar | null>(null)
  const [candidate, setCandidate] = useState<{ source: string; start_date: string; end_date: string; days: Calendar['days'] } | null>(null)
  const [audit, setAudit] = useState<Array<{ id: string; created_at: string; snapshot: Calendar }>>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    setInfo(null)
    api<DateInfo>(`/market/trading-calendar/plan-date?written_on=${day}${value ? `&target_date=${value}` : ''}`)
      .then(result => { if (active) { setInfo(result); setError('') } }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [day, value, refresh])
  async function loadCalendar() {
    try {
      const [current, history] = await Promise.all([api<Calendar>('/market/trading-calendar'), api<typeof audit>('/market/trading-calendar/audit')])
      setCalendar(current); setAudit(history); setError('')
    } catch (err) { setError((err as Error).message) }
  }
  async function readFile(file: File) {
    setCandidate(null); setError('')
    try {
      if (file.size > 256 * 1024) throw new Error('日历 JSON 文件最多 256 KB')
      const parsed = JSON.parse(await file.text())
      if (!parsed || typeof parsed.source !== 'string' || typeof parsed.start_date !== 'string' || typeof parsed.end_date !== 'string' || !Array.isArray(parsed.days)) throw new Error('需包含 source、start_date、end_date、days')
      setCandidate({ source: parsed.source, start_date: parsed.start_date, end_date: parsed.end_date, days: parsed.days })
      await loadCalendar()
    } catch (err) { setError((err as Error).message) }
  }
  async function saveCalendar() {
    if (!candidate || !calendar) return
    setBusy(true); setError('')
    try {
      const saved = await api<Calendar>('/market/trading-calendar', 'PUT', { ...candidate, expected_revision: calendar.revision })
      setCalendar(saved); setCandidate(null); setRefresh(value => value + 1)
      setAudit(await api<typeof audit>('/market/trading-calendar/audit'))
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  return <div className="market-detail">
    <label className="field">计划执行日<input type="date" min={day} value={value || ''} onChange={event => onChange(event.target.value || null)} /></label><small className="muted">须晚于复盘日期且在 30 个自然日内；可先留空保存正文。</small>
    <p className="muted">{!value ? '尚未指定执行日，计划不会自动指向周一。' : info?.target_status === 'open' ? '本地日历标记为开市日。' : info?.target_status === 'closed' ? '本地日历标记为休市日，请核对执行日。' : '交易日未知，请核对后手动填写。'}</p>
    {info?.suggested_date && <button type="button" className="button secondary" onClick={() => onChange(info.suggested_date)}>采用本地日历下一交易日 {info.suggested_date}</button>}
    {info?.calendar.source && <p className="muted">日历来源：{info.calendar.source} · v{info.calendar.revision} · {info.calendar.start_date} 至 {info.calendar.end_date}</p>}
    {error && <p className="alert error" role="alert">{error}</p>}
    <details><summary onClick={() => { if (!calendar) loadCalendar().catch(() => {}) }}>导入或核对本地交易日历</summary><p className="muted">仅按完整本地日历顺延休市日，来源内容请自行核对；不联网。每个自然日都需明确 is_open，最多 1096 天。更新不会改写已保存的计划日期。</p>
      <code style={{ overflowWrap: 'anywhere' }}>{'{"source":"来源说明","start_date":"2025-01-01","end_date":"2025-01-02","days":[{"date":"2025-01-01","is_open":false},{"date":"2025-01-02","is_open":true}]}'}</code>
      <label className="field">选择日历 JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) readFile(file).catch(() => {}); event.target.value = '' }} /></label>
      {candidate && <div className="alert"><p>待导入：{candidate.source} · {candidate.start_date} 至 {candidate.end_date} · {candidate.days.length} 个自然日。将替换当前 v{calendar?.revision ?? '…'} 日历。</p><button type="button" className="button secondary" disabled={busy || !calendar} onClick={() => saveCalendar().catch(() => {})}>确认导入本地日历</button></div>}
      {audit.length > 0 && <ul>{audit.slice(0, 5).map(item => <li key={item.id}>v{item.snapshot.revision} · {item.snapshot.source} · {item.snapshot.start_date} 至 {item.snapshot.end_date} · {item.created_at}</li>)}</ul>}
    </details>
  </div>
}

export function PositionRehearsal({ accountId, day, targetDate, rows, onChange }: { accountId: string; day: string; targetDate: string | null; rows: RehearsalRow[]; onChange: (rows: RehearsalRow[]) => void }) {
  const [source, setSource] = useState<'snapshot' | 'ledger'>('snapshot')
  const [base, setBase] = useState<Baseline | null>(null)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const requestVersion = useRef(0)
  useEffect(() => {
    let active = true
    setBase(null); setPreview(null); setSelected({}); setError('')
    Promise.all([api<Baseline>(`/accounts/${accountId}/planning/${day}/baseline?source=${source}`), api<Dataset[]>('/market/datasets')])
      .then(([baseline, items]) => { if (active) { setBase(baseline); setDatasets(items) } }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [accountId, day, source, refresh])
  useEffect(() => { requestVersion.current += 1; setPreview(null) }, [accountId, day, source, refresh, rows, selected, targetDate])
  function edit(index: number, change: Partial<RehearsalRow>) { onChange(rows.map((row, i) => i === index ? { ...row, ...change } : row)) }
  async function calculate() {
    if (!base) return
    const version = ++requestVersion.current
    setBusy(true); setError('')
    try {
      const value = await api<Preview>(`/accounts/${accountId}/planning/${day}/preview`, 'POST', {
        baseline_source: source, expected_baseline_sha256: base.sha256, rows,
        dataset_ids: [...new Set(Object.values(selected).filter(Boolean))], target_date: targetDate,
      })
      if (version === requestVersion.current) setPreview(value)
    } catch (err) { if (version === requestVersion.current) setError((err as Error).message) }
    finally { setBusy(false) }
  }
  const symbols = [...new Set([...(base?.positions.map(row => row.code) || []), ...rows.map(row => row.code)].filter(Boolean))]
    .filter((symbol, index, all) => !all.slice(0, index).some(other => sameMarketSymbol(symbol, other)))
  return <div className="market-detail"><div className="section-heading"><h3>持仓预演</h3><button className="button secondary" type="button" disabled={rows.length >= 100} onClick={() => onChange([...rows, { code: '', name: '', qty: 0, note: '', price: '' }])}>添加预演持仓</button></div>
    <p className="muted">目标是计划持有的股数；未列出的原持仓继续保留，清仓请显式填 0。预计价格是计算假设，预演不会下单或修改账户。</p>
    <div className="form-grid"><label className="field">持仓与现金基准<select value={source} onChange={event => setSource(event.target.value as typeof source)}><option value="snapshot">复盘当日确认快照</option><option value="ledger">截至复盘日的已入账流水</option></select></label><button type="button" className="button secondary" onClick={() => setRefresh(value => value + 1)}>刷新基准</button></div>
    {base && <><p>{base.method}</p><p>基准现金：{money(base.cash)} · 持仓 {base.positions.length} 个 · 费用规则 v{base.fee_settings.version}</p>{base.quality_flags.length > 0 && <p className="alert">{flagsText(base.quality_flags)}</p>}
      <p className="muted">佣金率 {base.fee_settings.config.commission_rate}；最低佣金 {base.fee_settings.config.minimum_commission}；卖出印花税率 {base.fee_settings.config.sell_stamp_rate}；过户费率 {base.fee_settings.config.transfer_rate}。取账户当前费用设置。</p>
      <button type="button" className="button secondary" disabled={!base.positions.length || base.positions.length > 100} onClick={() => { if (!rows.length || window.confirm('用当前基准持仓替换预演行？已有文字和价格会被替换。')) onChange(base.positions.map(item => ({ code: item.code, name: item.name, qty: item.qty, note: '', price: '' }))) }}>复制当前基准持仓到草稿</button>
    </>}
    {rows.map((row, index) => <div className="form-grid" key={index}><label className="field">代码<input value={row.code} onChange={event => edit(index, { code: event.target.value })} /></label><label className="field">名称<input value={row.name} onChange={event => edit(index, { name: event.target.value })} /></label><label className="field">预演数量<input type="number" min="0" max="100000000" step="1" value={row.qty} onChange={event => edit(index, { qty: Number(event.target.value) })} /></label><label className="field">预计价格<input type="number" min="0.0001" step="0.0001" placeholder="未填写则用选定行情" value={row.price || ''} onChange={event => edit(index, { price: event.target.value })} /></label><label className="field">说明<input value={row.note} onChange={event => edit(index, { note: event.target.value })} /></label><button className="link-button danger" type="button" onClick={() => onChange(rows.filter((_, i) => i !== index))}>移除</button></div>)}
    <details><summary>选择冻结行情作为价格假设</summary><p className="muted">人工预计价优先；行情仅取复盘当日结束前已可见的本地价格。没有合适价格时保持未知。</p>{symbols.map(symbol => <label className="field" key={symbol}>{symbol} 行情<select value={selected[symbol] || ''} onChange={event => setSelected(current => ({ ...current, [symbol]: event.target.value }))}><option value="">未选择</option>{datasets.filter(item => sameMarketSymbol(item.symbol, symbol)).map(item => <option key={item.id} value={item.id}>{item.symbol} · 至 {item.last_date} · {item.id.slice(0, 8)}</option>)}</select></label>)}</details>
    <button className="button secondary" type="button" disabled={!base || busy || rows.some(row => !row.code.trim())} onClick={() => calculate().catch(() => {})}>{busy ? '正在计算…' : '检查预演现金与费用'}</button>
    {error && <p className="alert error" role="alert">{error}</p>}
    {preview && <section aria-label="预演检查结果"><h4>现金检查：{({ sufficient: '预计足够', insufficient: '预计不足', unknown: '信息不足' })[preview.cash_status] || preview.cash_status}</h4><p>预计剩余现金 {money(preview.remaining_cash)} · 预计费用 {money(preview.estimated_fees)} · 预计持仓市值 {money(preview.position_value)} · 预计总资产 {money(preview.projected_total)}</p><p className="muted">{preview.method}</p>{preview.quality_flags.length > 0 && <p className="alert">{flagsText(preview.quality_flags)}</p>}<div className="table-wrap"><table><thead><tr><th>标的</th><th>基准 → 目标</th><th>价格假设</th><th>预计费用</th><th>现金变动</th><th>提示</th></tr></thead><tbody>{preview.rows.map(row => <tr key={row.code}><td>{row.code}</td><td>{row.current_qty} → {row.target_qty}</td><td>{row.price ?? '未知'}{row.price_evidence && <small> · {row.price_evidence.source === 'manual_assumption' ? '人工' : row.price_evidence.quote_date || '无可见行情'}</small>}</td><td>{money(row.fees)}</td><td>{money(row.cash_delta)}</td><td>{flagsText(row.quality_flags)}</td></tr>)}</tbody></table></div></section>}
  </div>
}
