import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from './api'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
type Signal = { id: string; dataset_id: string; strategy_id: string; decision_at: string; result: { status: string; signal: boolean | null; source_date: string | null; candidate?: { symbol?: string } | null; quality_flags?: string[] } }
type Fees = { commission_rate: string; minimum_commission: string; sell_stamp_rate: string; transfer_rate: string }
type BasketConfig = { holding_bars: number; holding_mode: 'legacy_signal_anchor' | 'complete_overnights'; quantity: number; fees: Fees }
type Member = { symbol: string; source_date: string; decision_date: string; anchor_date: string; run_ids: string[]; dataset_id: string; signals: Signal[]; quality_flags: string[] }
type Basket = { id: string; name: string; notes: string; revision: number; deleted: boolean; created_at: string; updated_at: string; strategy_ids?: string[]; source: { kind: 'manual' | 'scan'; scan_id?: string; selection?: 'union' | 'intersection'; scan_date?: string }; config: BasketConfig; constituents?: Member[]; total_constituents: number }
type Scan = { id: string; created_at: string; as_of_date: string | null; date_from: string | null; date_to: string | null; union_count: number; intersection_count: number; result?: { union: Array<{ symbol: string; decision_date: string }>; intersection: Array<{ symbol: string; decision_date: string }> } }
type Case = { status: string; entry_date: string | null; entry_price: string | null; target_date: string | null; exit_price: string | null; raw_return: number | null; after_cost_return: number | null; mark_to_market_return: number | null; buy_fees: string | null; sell_fees: string | null }
type Summary = { total_constituents: number; completed_count: number; raw_count: number; pending_count: number; untradeable_count: number; raw_return: number | null; after_cost_return: number | null; stock_win_rate: number | null; after_cost_win_rate: number | null; mark_to_market_return: number | null; mark_to_market_count: number }
type Evaluation = { id: string; basket_id: string; basket_revision: number; created_at: string; as_of_date?: string; summary?: { t1: Summary; t2: Summary }; request?: { basket_snapshot: Basket; as_of_date: string; strict: boolean }; result?: { constituents: Array<{ symbol: string; run_ids: string[]; anchor_date: string; dataset_id: string; current_date: string | null; current_price: string | null; quality_flags?: string[]; t1: Case; t2: Case }>; summary: { t1: Summary; t2: Summary }; curve: Array<{ date: string; t1?: { raw_return: number; priced_count: number }; t2?: { raw_return: number; priced_count: number } }>; notes: string[] } }
type Audit = { id: string; action: string; revision: number; snapshot: Partial<Basket> & { evaluation_id?: string; basket_revision?: number; as_of_date?: string }; created_at: string }
type Draft = { name: string; notes: string; holding_bars: string; holding_mode: BasketConfig['holding_mode']; quantity: string; fees: Fees }
const fees: Fees = { commission_rate: '0.0003', minimum_commission: '5.00', sell_stamp_rate: '0.001', transfer_rate: '0.00001' }
const feeNames: Record<keyof Fees, string> = { commission_rate: '佣金比例', minimum_commission: '最低佣金（元）', sell_stamp_rate: '卖出印花税比例', transfer_rate: '过户费比例' }
const statuses: Record<string, string> = { pending_entry: '等待入场行情', pending_exit: '等待退出行情', target_before_entry: '目标日早于入场日', untradeable_same_day: '同日买卖，不满足 T+1', completed: '已完成费用后测算' }
const emptyDraft = (): Draft => ({ name: '', notes: '', holding_bars: '5', holding_mode: 'legacy_signal_anchor', quantity: '100', fees: { ...fees } })
const pct = (value: number | null | undefined) => value == null ? '待数据' : `${(value * 100).toFixed(2)}%`
const today = () => new Date().toLocaleDateString('sv-SE')
const stockKey = (symbol: string) => /^(?:sh|sz|bj)?([0-9]{6})(?:\.(?:sh|sz|bj))?$/i.exec(symbol.trim())?.[1] || symbol.toLowerCase()

function CaseResult({ value, label }: { value: Case; label: string }) {
  const untradeable = value.status === 'untradeable_same_day' || value.status === 'target_before_entry'
  return <div><strong>{label} · {statuses[value.status] || value.status}</strong><p>原始收益 {value.raw_return == null && untradeable ? '不可计算' : pct(value.raw_return)} · 费用后 {untradeable ? '不可计算' : pct(value.after_cost_return)}</p><p className="muted">入场：{value.entry_date || '待行情'} / ¥ {value.entry_price ?? '—'}；目标日：{value.target_date || '待行情'} / ¥ {value.exit_price ?? '—'}</p><details><summary>价格观察与费用</summary><p>最新价格观察收益：{pct(value.mark_to_market_return)}</p><p>买入费用 ¥ {value.buy_fees ?? '—'} · 卖出费用 ¥ {value.sell_fees ?? '—'}</p></details></div>
}

export function SignalBasketEditor({ datasets, onOpenMarket, onOpenResearch }: { datasets: Dataset[]; onOpenMarket: (datasetId: string) => void; onOpenResearch: (runId: string) => void }) {
  const [baskets, setBaskets] = useState<Basket[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [scans, setScans] = useState<Scan[]>([])
  const [scan, setScan] = useState<Scan | null>(null)
  const [scanId, setScanId] = useState('')
  const [selection, setSelection] = useState<'union' | 'intersection'>('union')
  const [scanDate, setScanDate] = useState('')
  const [source, setSource] = useState<'manual' | 'scan'>('manual')
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [editing, setEditing] = useState<Basket | null>(null)
  const [runIds, setRunIds] = useState<string[]>([])
  const [signalSearch, setSignalSearch] = useState('')
  const [selected, setSelected] = useState<Basket | null>(null)
  const [checked, setChecked] = useState<string[]>([])
  const [deletePending, setDeletePending] = useState<Array<{ id: string; expected_revision: number }> | null>(null)
  const [asOf, setAsOf] = useState(today)
  const [strict, setStrict] = useState(true)
  const [forward, setForward] = useState<Record<string, string>>({})
  const [evaluations, setEvaluations] = useState<Evaluation[]>([])
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [audits, setAudits] = useState<Audit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [strategyNames, setStrategyNames] = useState<Record<string, string>>({})
  const [listSearch, setListSearch] = useState('')
  const [strategyFilter, setStrategyFilter] = useState('')
  const [includeDeleted, setIncludeDeleted] = useState(false)

  useEffect(() => {
    let live = true
    Promise.allSettled([api<Basket[]>('/research/baskets'), api<Signal[]>('/research/runs'), api<Scan[]>('/research/scans'), api<Array<{ id: string; name: string }>>('/research/strategies')]).then(results => {
      if (!live) return
      const [basketResult, signalResult, scanResult, namesResult] = results
      if (basketResult.status === 'fulfilled') setBaskets(basketResult.value)
      if (signalResult.status === 'fulfilled') setSignals(signalResult.value)
      if (scanResult.status === 'fulfilled') setScans(scanResult.value)
      if (namesResult.status === 'fulfilled') setStrategyNames(Object.fromEntries(namesResult.value.map(item => [item.id, item.name])))
      const failures = results.filter(item => item.status === 'rejected').map(item => item.reason instanceof Error ? item.reason.message : '篮子数据读取失败')
      if (failures.length) setError(failures.join('；'))
    })
    return () => { live = false }
  }, [])

  const availableSignals = useMemo(() => {
    const all = new Map(signals.map(item => [item.id, item]))
    for (const member of editing?.constituents || []) for (const signal of member.signals) all.set(signal.id, signal)
    return [...all.values()].filter(item => item.result.status === 'computed' && item.result.signal === true)
  }, [signals, editing])
  const shownSignals = availableSignals.filter(item => `${item.result.candidate?.symbol || ''} ${strategyNames[item.strategy_id] || item.strategy_id} ${item.result.source_date || ''}`.toLowerCase().includes(signalSearch.toLowerCase()))
  const scanDates = [...new Set((scan?.result?.[selection] || []).map(item => item.decision_date))].sort()
  const automaticCount = (scan?.result?.[selection] || []).filter(item => item.decision_date === scanDate).length
  const latestRevision = baskets.find(item => item.id === editing?.id)?.revision
  const conflict = Boolean(editing && latestRevision !== undefined && latestRevision !== editing.revision)
  const shownBaskets = baskets.filter(item => item.name.toLowerCase().includes(listSearch.toLowerCase()) && (!strategyFilter || item.strategy_ids?.includes(strategyFilter)))
  const sourceLabel = (basket: Basket) => basket.source.kind === 'scan' ? `策略扫描${basket.source.selection === 'intersection' ? '交集' : '并集'} · ${basket.source.scan_date || '—'}` : '手动选择研究信号'

  async function refresh() {
    setBusy(true); setError('')
    try {
      const [nextBaskets, nextSignals, nextScans] = await Promise.all([api<Basket[]>(`/research/baskets?include_deleted=${includeDeleted}`), api<Signal[]>('/research/runs'), api<Scan[]>('/research/scans')])
      setBaskets(nextBaskets); setSignals(nextSignals); setScans(nextScans)
      setNotice('列表已刷新，编辑内容保留。')
    } catch (err) { setError(err instanceof Error ? err.message : '篮子列表读取失败') }
    finally { setBusy(false) }
  }

  async function fail(err: unknown) {
    const caught = err as Error & { code?: string }
    setError(caught.message || '操作失败')
    if (caught.code?.includes('REVISION') || caught.code?.includes('CONFLICT')) {
      try { setBaskets(await api<Basket[]>(`/research/baskets?include_deleted=${includeDeleted}`)) } catch { /* Preserve draft and original error. */ }
      setNotice('编辑内容已保留，请重新载入最新篮子，或另存为新篮子。')
    }
  }

  function reset() { setEditing(null); setDraft(emptyDraft()); setRunIds([]); setSource('manual'); setError(''); setNotice('') }

  function edit(basket: Basket) {
    setEditing(basket); setSource('manual'); setRunIds([...new Set((basket.constituents || []).flatMap(item => item.run_ids))])
    setDraft({ name: basket.name, notes: basket.notes, holding_bars: String(basket.config.holding_bars), holding_mode: basket.config.holding_mode, quantity: String(basket.config.quantity), fees: { ...basket.config.fees } })
    setError(''); setNotice(`正在编辑“${basket.name}”版本 ${basket.revision}。`)
  }

  async function openBasket(id: string, forEdit = false) {
    setBusy(true); setError('')
    try {
      const [basket, history] = await Promise.all([api<Basket>(`/research/baskets/${encodeURIComponent(id)}?include_deleted=true`), api<Evaluation[]>(`/research/baskets/${encodeURIComponent(id)}/evaluations`)])
      setSelected(basket); setEvaluations(history); setEvaluation(null); setAudits(null)
      setForward(Object.fromEntries((basket.constituents || []).map(item => [item.symbol, item.dataset_id])))
      if (forEdit) edit(basket)
    } catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  async function showDeleted(value: boolean) {
    setBusy(true); setError('')
    try {
      setBaskets(await api<Basket[]>(`/research/baskets?include_deleted=${value}`))
      setIncludeDeleted(value); setChecked([])
    } catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  function toggleRun(item: Signal, checked: boolean) {
    if (!checked) { setRunIds(current => current.filter(id => id !== item.id)); return }
    const symbol = stockKey(item.result.candidate?.symbol || datasets.find(dataset => dataset.id === item.dataset_id)?.symbol || '')
    if (availableSignals.some(existing => runIds.includes(existing.id) && stockKey(existing.result.candidate?.symbol || datasets.find(dataset => dataset.id === existing.dataset_id)?.symbol || '') === symbol)) {
      setError('手动篮子每只证券只能选择一条研究信号，请先取消该证券的另一条信号。')
      return
    }
    setRunIds(current => [...current, item.id]); setError('')
  }

  async function loadScan(id: string) {
    setScanId(id); setScan(null); setScanDate(''); setError('')
    if (!id) return
    setBusy(true)
    try {
      const next = await api<Scan>(`/research/scans/${encodeURIComponent(id)}`)
      setScan(next)
      const dates = [...new Set((next.result?.[selection] || []).map(item => item.decision_date))].sort()
      if (dates.length === 1) setScanDate(dates[0])
    } catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      if (!draft.name.trim()) throw new Error('请填写篮子名称')
      const holding = Number(draft.holding_bars), quantity = Number(draft.quantity)
      if (!Number.isInteger(holding) || holding < 1 || holding > 60) throw new Error('持有期须为 1 至 60 根 K 线')
      if (!Number.isInteger(quantity) || quantity < 100 || quantity > 1_000_000 || quantity % 100) throw new Error('每只股数须为 100 至 1000000 且为 100 的倍数')
      const body: Record<string, unknown> = { name: draft.name, notes: draft.notes, holding_bars: holding, holding_mode: draft.holding_mode, quantity, fees: draft.fees }
      if (editing) {
        body.expected_revision = editing.revision
        const oldIds = [...new Set((editing.constituents || []).flatMap(item => item.run_ids))].sort()
        if (JSON.stringify(oldIds) !== JSON.stringify([...runIds].sort())) body.run_ids = runIds
      } else if (source === 'scan') {
        if (!scanId || !scanDate || !automaticCount) throw new Error('请选择有触发结果的扫描记录和单个扫描日')
        Object.assign(body, { scan_id: scanId, selection, scan_date: scanDate })
      } else {
        if (!runIds.length || runIds.length > 100) throw new Error('请至少选择 1 条且不超过 100 条触发信号')
        body.run_ids = runIds
      }
      if (body.run_ids) {
        if (!runIds.length || runIds.length > 100) throw new Error('手动成员须为 1 至 100 条触发信号')
        const selectedSignals = availableSignals.filter(item => runIds.includes(item.id))
        const symbols = selectedSignals.map(item => stockKey(item.result.candidate?.symbol || datasets.find(dataset => dataset.id === item.dataset_id)?.symbol || ''))
        if (new Set(symbols).size !== symbols.length) throw new Error('修改成员时，每只证券只能保留一条研究信号；请取消重复证券的信号。')
      }
      const saved = await api<Basket>(editing ? `/research/baskets/${encodeURIComponent(editing.id)}` : '/research/baskets', editing ? 'PUT' : 'POST', body)
      setBaskets(current => [saved, ...current.filter(item => item.id !== saved.id)])
      setSelected(saved); setForward(Object.fromEntries((saved.constituents || []).map(item => [item.symbol, item.dataset_id])))
      setEvaluations([]); setEvaluation(null); setAudits(null); edit(saved)
      setNotice(`篮子已保存，共 ${saved.total_constituents} 只证券。可选择后续行情进行 T+1 / T+2 观察。`)
      try { setEvaluations(await api<Evaluation[]>(`/research/baskets/${encodeURIComponent(saved.id)}/evaluations`)) }
      catch { setNotice('篮子已保存；历史测算暂未载入，请点击“查看与测算”重试。') }
    } catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  async function remove() {
    if (!deletePending) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api('/research/baskets/delete-batch', 'POST', { items: deletePending })
      const ids = new Set(deletePending.map(item => item.id))
      setBaskets(current => current.filter(item => !ids.has(item.id))); setChecked(current => current.filter(id => !ids.has(id)))
      if (selected && ids.has(selected.id)) { setSelected(null); setEvaluation(null); setEvaluations([]) }
      if (editing && ids.has(editing.id)) reset()
      setNotice(`已删除 ${ids.size} 个篮子；研究来源与历史计算记录保留。`); setDeletePending(null)
      if (includeDeleted) {
        try { setBaskets(await api<Basket[]>('/research/baskets?include_deleted=true')) }
        catch { setNotice('篮子已删除；请刷新列表以查看保留的历史记录。') }
      }
    } catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  async function evaluate(event: FormEvent) {
    event.preventDefault()
    if (!selected) return
    setBusy(true); setError(''); setNotice('')
    try {
      if (!asOf) throw new Error('请选择观察截止日期')
      if ((selected.constituents || []).some(member => member.anchor_date > asOf)) throw new Error('观察截止日期不能早于任一成分的信号锚点日期')
      const next = await api<Evaluation>(`/research/baskets/${encodeURIComponent(selected.id)}/evaluations`, 'POST', { expected_revision: selected.revision, as_of_date: asOf, strict, forward_datasets: forward })
      setEvaluation(next); setEvaluations(current => [next, ...current.filter(item => item.id !== next.id)])
      setNotice('观察结果已保存，缺少行情的项目保留待定状态。')
    } catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  async function openEvaluation(id: string) {
    if (!selected) return
    setBusy(true); setError('')
    try { setEvaluation(await api<Evaluation>(`/research/baskets/${encodeURIComponent(selected.id)}/evaluations/${encodeURIComponent(id)}`)) }
    catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  async function history() {
    if (!selected) return
    setBusy(true); setError('')
    try { setAudits(await api<Audit[]>(`/research/baskets/${encodeURIComponent(selected.id)}/audit`)) }
    catch (err) { await fail(err) }
    finally { setBusy(false) }
  }

  return <section className="card span-all" aria-label="信号篮子">
    <h3>信号篮子 · T+1 / T+2 观察</h3><p className="muted">将已触发的研究信号组成观察篮子，比较两个入场日口径、持有期和费用。这是研究组合；行情缺失的项目会保持待定。</p>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    <div className="toolbar" style={{ flexWrap: 'wrap', marginBottom: 16 }}><button type="button" className="button secondary" disabled={busy} onClick={refresh}>刷新篮子与来源</button><button type="button" className="button secondary" disabled={busy} onClick={() => { if (window.confirm('清空当前编辑表单并新建篮子？')) reset() }}>新建篮子</button></div>
    <details open><summary>{editing ? `编辑篮子 · 版本 ${editing.revision}` : '创建信号篮子'}</summary>
      {conflict && <p className="danger" role="alert">当前编辑基于版本 {editing?.revision}，最新版本为 {latestRevision}。<button type="button" className="link-button" disabled={busy} onClick={() => { if (editing && window.confirm('重新载入会覆盖当前输入，确认载入？')) openBasket(editing.id, true) }}>重新载入</button><button type="button" className="link-button" disabled={busy} onClick={() => { setEditing(null); setSource('manual'); setDraft(current => ({ ...current, name: current.name.slice(0, 70) + ' · 副本' })) }}>另存为新篮子</button></p>}
      <form className="form" noValidate onSubmit={save} style={{ marginTop: 12 }}>
        <div className="form-grid"><label className="field"><span>篮子名称</span><input disabled={busy} value={draft.name} maxLength={128} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label><label className="field"><span>来源方式</span><select disabled={busy || Boolean(editing)} value={source} onChange={event => setSource(event.target.value as typeof source)}><option value="manual">手动选择触发信号</option><option value="scan">从策略扫描生成</option></select></label><label className="field" style={{ gridColumn: '1 / -1' }}><span>观察备注</span><textarea disabled={busy} value={draft.notes} maxLength={1000} onChange={event => setDraft({ ...draft, notes: event.target.value })} /></label></div>
        {source === 'manual' ? <details open><summary>选择成分信号 · {runIds.length} / 100</summary><button type="button" className="link-button" disabled={busy || !runIds.length} onClick={() => setRunIds([])}>清空成分选择</button><label className="field" style={{ margin: '12px 0' }}><span>查找证券、策略或信号日</span><input type="search" value={signalSearch} onChange={event => setSignalSearch(event.target.value)} /></label><div className="table-wrap" style={{ maxHeight: 280, overflowY: 'auto' }}><table><thead><tr><th>选择</th><th>证券</th><th>策略</th><th>信号日</th><th>证据</th></tr></thead><tbody>{shownSignals.map(item => <tr key={item.id}><td><input type="checkbox" aria-label={`加入篮子 ${item.result.candidate?.symbol || item.id} ${item.id.slice(0, 8)}`} checked={runIds.includes(item.id)} disabled={busy || (!runIds.includes(item.id) && runIds.length >= 100)} onChange={event => toggleRun(item, event.target.checked)} /></td><td>{item.result.candidate?.symbol || '—'}</td><td>{strategyNames[item.strategy_id] || item.strategy_id}</td><td>{item.result.source_date || '—'}</td><td><button type="button" className="link-button" onClick={() => onOpenResearch(item.id)}>研究记录</button></td></tr>)}</tbody></table></div>{!shownSignals.length && <p className="muted">没有可选的触发信号，请先运行策略研究或扫描。</p>}<p className="muted">列表提供最近的研究记录。手动编辑成员时，每只证券保留一条信号；未改变自动篮子成员时保留原合并证据。</p></details> : <div className="form-grid"><label className="field"><span>来源扫描</span><select disabled={busy} value={scanId} onChange={event => loadScan(event.target.value)}><option value="">请选择扫描</option>{scans.map(item => <option key={item.id} value={item.id}>{item.as_of_date || `${item.date_from} 至 ${item.date_to}`} · 并集 {item.union_count} / 交集 {item.intersection_count} · {item.id.slice(0, 8)}</option>)}</select></label><label className="field"><span>自动选取范围</span><select disabled={busy} value={selection} onChange={event => { const next = event.target.value as typeof selection; setSelection(next); const dates = [...new Set((scan?.result?.[next] || []).map(item => item.decision_date))]; setScanDate(dates.length === 1 ? dates[0] : '') }}><option value="union">同日信号并集</option><option value="intersection">同日全策略交集</option></select></label><label className="field"><span>选取单个扫描日</span><select disabled={busy} value={scanDate} onChange={event => setScanDate(event.target.value)}><option value="">请选择日期</option>{scanDates.map(day => <option key={day} value={day}>{day}</option>)}</select></label><p>将选入 {automaticCount} 只证券。</p></div>}
        <div className="form-grid"><label className="field"><span>持有期口径</span><select disabled={busy} value={draft.holding_mode} onChange={event => setDraft({ ...draft, holding_mode: event.target.value as Draft['holding_mode'] })}><option value="legacy_signal_anchor">从信号锚点计算目标日</option><option value="complete_overnights">从各自入场日计算完整持有期</option></select></label><label className="field"><span>持有期（样本交易日）</span><input disabled={busy} type="number" min="1" max="60" step="1" value={draft.holding_bars} onChange={event => setDraft({ ...draft, holding_bars: event.target.value })} /></label><label className="field"><span>每只成分股的测算数量（股）</span><input disabled={busy} type="number" min="100" max="1000000" step="100" value={draft.quantity} onChange={event => setDraft({ ...draft, quantity: event.target.value })} /></label></div>
        <p className="muted">T+1 / T+2 分别取信号锚点后的第一 / 第二根 K 线开盘。按信号锚点计算时，两个入场口径共用目标日；出现同日买卖或目标日早于入场的情况时，费用后收益保持不可计算。</p>
        <details><summary>费用参数</summary><div className="form-grid">{(Object.keys(feeNames) as Array<keyof Fees>).map(key => <label className="field" key={key}><span>{feeNames[key]}</span><input disabled={busy} type="number" min="0" max={key === 'minimum_commission' ? undefined : '0.01'} step={key === 'minimum_commission' ? '0.01' : 'any'} value={draft.fees[key]} onChange={event => setDraft({ ...draft, fees: { ...draft.fees, [key]: event.target.value } })} /></label>)}</div><p className="muted">比例使用小数，如 0.0003 表示万分之三；费用沿用共享交易计算规则。</p></details>
        <button className="button primary" disabled={busy || conflict}>{editing ? '保存篮子修改' : '创建信号篮子'}</button>
      </form>
    </details>
    <div style={{ marginTop: 20 }}><h4>已保存篮子</h4>
      <div className="form-grid" style={{ marginBottom: 12 }}><label className="field"><span>按名称筛选篮子</span><input type="search" value={listSearch} onChange={event => setListSearch(event.target.value)} /></label><label className="field"><span>按策略筛选篮子</span><select value={strategyFilter} onChange={event => setStrategyFilter(event.target.value)}><option value="">全部策略</option>{Object.entries(strategyNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label></div>
      <div className="toolbar" style={{ marginBottom: 12, flexWrap: 'wrap' }}><button type="button" className="button secondary danger" disabled={busy || !checked.length} onClick={() => setDeletePending(baskets.filter(item => checked.includes(item.id) && !item.deleted).map(item => ({ id: item.id, expected_revision: item.revision })))}>批量删除已选 {checked.length} 项</button><label className="check-field"><input type="checkbox" disabled={busy} checked={includeDeleted} onChange={event => showDeleted(event.target.checked)} />显示已删除篮子及历史记录</label></div>
      <div className="table-wrap"><table><thead><tr><th>选择</th><th>名称</th><th>来源</th><th>成分 / 持有期</th><th>版本</th><th>操作</th></tr></thead><tbody>{shownBaskets.map(item => <tr key={item.id}><td><input type="checkbox" aria-label={`选择篮子 ${item.name}`} disabled={busy || item.deleted} checked={checked.includes(item.id)} onChange={event => setChecked(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} /></td><td>{item.name}{item.deleted && <small className="muted"> · 已删除</small>}</td><td>{sourceLabel(item)}</td><td>{item.total_constituents} 只 / {item.config.holding_bars} 日</td><td>{item.revision}</td><td><button type="button" className="link-button" disabled={busy} onClick={() => openBasket(item.id)}>{item.deleted ? '查看历史' : '查看与测算'}</button>{!item.deleted && <><button type="button" className="link-button" disabled={busy} onClick={() => { if (window.confirm('载入该篮子到编辑区？当前未保存的输入将被替换。')) openBasket(item.id, true) }}>编辑</button><button type="button" className="link-button danger" disabled={busy} onClick={() => setDeletePending([{ id: item.id, expected_revision: item.revision }])}>删除</button></>}</td></tr>)}</tbody></table></div>{!shownBaskets.length && <p className="muted">暂无符合条件的信号篮子。</p>}</div>
    {deletePending && <div className="alert error" role="alert" style={{ marginTop: 12 }}><p>确认删除这 {deletePending.length} 个篮子？历史测算快照和研究证据会保留。</p><div className="toolbar"><button className="button secondary danger" type="button" disabled={busy} onClick={remove}>确认删除篮子</button><button className="button secondary" type="button" disabled={busy} onClick={() => setDeletePending(null)}>取消删除</button></div></div>}
    {selected && <div style={{ marginTop: 24 }}><h4>{selected.name} · 版本 {selected.revision}</h4><p>{selected.notes}</p><p className="muted">{sourceLabel(selected)} · {selected.total_constituents} 只成分股</p><div className="toolbar" style={{ flexWrap: 'wrap', marginBottom: 12 }}><a className="button secondary" href={`/api/v1/research/baskets/${encodeURIComponent(selected.id)}/export.csv`} download>导出篮子 CSV</a><a className="button secondary" href={`/api/v1/research/baskets/${encodeURIComponent(selected.id)}/export.xlsx`} download>导出篮子 Excel</a><button type="button" className="button secondary" disabled={busy} onClick={history}>版本历史</button></div>
      {!selected.deleted && <form className="form" noValidate onSubmit={evaluate}><div className="form-grid"><label className="field"><span>观察截止日期</span><input type="date" disabled={busy} value={asOf} onChange={event => setAsOf(event.target.value)} /></label><label className="check-field"><input type="checkbox" disabled={busy} checked={strict} onChange={event => setStrict(event.target.checked)} />严格可得时间</label></div>
        <p className="muted">为每只证券明确选择含后续交易日的冻结行情。源样本没有未来价格时可以保留选择，结果将显示等待行情。</p>
        <div className="table-wrap"><table><thead><tr><th>成分股</th><th>信号日 / 决策日 / 锚点</th><th>测算行情</th><th>研究来源</th></tr></thead><tbody>{selected.constituents?.map(member => { const candidates = datasets.filter(item => stockKey(item.symbol) === stockKey(member.symbol)); return <tr key={member.symbol}><td>{member.symbol}</td><td>{member.source_date}<br />{member.decision_date}<br />{member.anchor_date}</td><td><label className="field"><span>冻结行情 · {member.symbol}</span><select disabled={busy} value={forward[member.symbol] || member.dataset_id} onChange={event => setForward({ ...forward, [member.symbol]: event.target.value })}>{!candidates.some(item => item.id === member.dataset_id) && <option value={member.dataset_id}>原信号样本 · {member.dataset_id.slice(0, 8)}</option>}{candidates.map(item => <option key={item.id} value={item.id}>{item.symbol} · {item.first_date} 至 {item.last_date} · {item.id.slice(0, 8)}</option>)}</select></label><button type="button" className="link-button" onClick={() => onOpenMarket(forward[member.symbol] || member.dataset_id)}>查看冻结 K 线</button></td><td>{member.run_ids.map(id => <button className="link-button" type="button" key={id} onClick={() => onOpenResearch(id)}>{strategyNames[member.signals.find(item => item.id === id)?.strategy_id || ''] || '研究证据'}</button>)}</td></tr> })}</tbody></table></div>
        <button className="button primary" disabled={busy}>保存 T+1 / T+2 测算</button>
      </form>}
      {audits && <details open style={{ marginTop: 16 }}><summary>版本历史 · {audits.length} 项</summary>{audits.map(item => <details key={item.id}><summary>{item.action === 'create' ? '创建' : item.action === 'update' ? '修改' : item.action === 'evaluate' ? '保存测算' : '删除'} · 版本 {item.revision} · {new Date(item.created_at).toLocaleString()}</summary>{item.snapshot.evaluation_id ? <p>观察截止日 {item.snapshot.as_of_date}<button type="button" className="link-button" disabled={busy} onClick={() => openEvaluation(item.snapshot.evaluation_id!)}>查看测算快照</button></p> : <><p>{item.snapshot.name} · {item.snapshot.notes}</p>{item.snapshot.config && <p>持有期 {item.snapshot.config.holding_bars} 日 · {item.snapshot.config.holding_mode === 'complete_overnights' ? '各自入场日计算' : '信号锚点计算'} · 每只 {item.snapshot.config.quantity} 股</p>}<p className="muted">{item.snapshot.constituents?.map(member => member.symbol).join('、') || '—'}</p></>}</details>)}</details>}
      <details style={{ marginTop: 16 }} open><summary>历史测算 · {evaluations.length} 次</summary><div className="table-wrap"><table><thead><tr><th>保存时间</th><th>篮子版本</th><th>截止日</th><th>操作</th></tr></thead><tbody>{evaluations.map(item => <tr key={item.id}><td>{new Date(item.created_at).toLocaleString()}</td><td>{item.basket_revision}</td><td>{item.as_of_date || item.request?.as_of_date || '—'}</td><td><button className="link-button" type="button" disabled={busy} onClick={() => openEvaluation(item.id)}>查看测算快照</button></td></tr>)}</tbody></table></div>{!evaluations.length && <p className="muted">尚未保存观察结果。</p>}</details>
    </div>}
    {evaluation?.result && <div style={{ marginTop: 24 }}><h4>观察结果 · {evaluation.request?.as_of_date || evaluation.as_of_date} · 篮子版本 {evaluation.basket_revision}</h4><a className="button secondary" href={`/api/v1/research/baskets/${encodeURIComponent(evaluation.basket_id)}/evaluations/${encodeURIComponent(evaluation.id)}/export.xlsx`} download>导出测算明细 Excel</a><p className="muted">{evaluation.result.notes.join('；')}</p><div className="table-wrap"><table><thead><tr><th>口径</th><th>原始平均收益</th><th>费用后平均收益</th><th>原始胜率</th><th>费用后胜率</th><th>完成 / 待定 / 不可执行</th></tr></thead><tbody>{(['t1', 't2'] as const).map(key => { const value = evaluation.result!.summary[key]; return <tr key={key}><td>{key === 't1' ? 'T+1' : 'T+2'}</td><td>{pct(value.raw_return)}<small> · {value.raw_count} 只</small></td><td>{pct(value.after_cost_return)}<small> · {value.completed_count} 只</small></td><td>{pct(value.stock_win_rate)}</td><td>{pct(value.after_cost_win_rate)}</td><td>{value.completed_count} / {value.pending_count} / {value.untradeable_count}</td></tr> })}</tbody></table></div>
      <p className="muted">均值与胜率仅覆盖已有相应结果的成分；待定数据不按零收益计入。</p>
      <div className="table-wrap"><table><thead><tr><th>证券</th><th>最新可得价格</th><th>T+1</th><th>T+2</th><th>证据</th></tr></thead><tbody>{evaluation.result.constituents.map(item => <tr key={item.symbol}><td>{item.symbol}{Boolean(item.quality_flags?.length) && <details><summary>数据标记</summary>{item.quality_flags?.map(flag => <p className="muted" key={flag}>{flag}</p>)}</details>}</td><td>¥ {item.current_price ?? '—'}<br />{item.current_date || '待行情'}</td><td style={{ whiteSpace: 'normal', minWidth: 220 }}><CaseResult value={item.t1} label="T+1" /></td><td style={{ whiteSpace: 'normal', minWidth: 220 }}><CaseResult value={item.t2} label="T+2" /></td><td><button type="button" className="link-button" onClick={() => onOpenMarket(item.dataset_id)}>冻结 K 线</button>{item.run_ids.map(id => <button className="link-button" type="button" key={id} onClick={() => onOpenResearch(id)}>研究记录</button>)}</td></tr>)}</tbody></table></div>
      <details style={{ marginTop: 16 }}><summary>入场后的价格观察序列 · {evaluation.result.curve.length} 个日期</summary><p className="muted">展示未扣费的价格观察均值，覆盖数可能随日期变化；目标日之后的价格继续记录，固定持有期收益见上表。</p><div className="table-wrap" style={{ maxHeight: 320, overflowY: 'auto' }}><table><thead><tr><th>日期</th><th>T+1 价格观察收益</th><th>覆盖数</th><th>T+2 价格观察收益</th><th>覆盖数</th></tr></thead><tbody>{evaluation.result.curve.map(point => <tr key={point.date}><td>{point.date}</td><td>{pct(point.t1?.raw_return)}</td><td>{point.t1?.priced_count ?? '—'}</td><td>{pct(point.t2?.raw_return)}</td><td>{point.t2?.priced_count ?? '—'}</td></tr>)}</tbody></table></div></details>
    </div>}
  </section>
}
