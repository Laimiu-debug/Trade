import { useEffect, useState } from 'react'
import { api } from './api'
import { marketSymbolKey } from './market-symbols'
import { Icon } from './workspace-icons'

type Inputs = { initial_date: string; wallet_date: string; wallet_revision: number; initial_capital: string; symbols: string[]; current_quantities: Record<string, number>; fill_count: number }
type Dataset = { id: string; symbol: string; first_date: string; last_date: string; adjustment: string }
type Position = { symbol: string; quantity: number; dataset_id: string | null; close: string | null; quote_date: string | null; market_value: string | null; quality: string }
type Point = { date: string; cash: string; total_assets: string | null; known_position_value: string; asset_index: string | null; drawdown_pct: string | null; drawdown_quality: string; quality: string; cumulative_fees: string; positions: Position[]; axis_reasons: string[] }
type Summary = { ending_assets: string | null; ending_cash: string; range_return_pct: string | null; range_denominator_assets: string | null; range_denominator_date: string; max_drawdown_pct: string | null; observed_max_drawdown_pct: string; missing_observation_count: number; stale_observation_count: number }
type Report = { id: string; input_sha256: string; date_from?: string; date_to?: string; wallet_revision?: number; created_at?: string; summary?: Summary; result?: { date_from: string; date_to: string; initial_date: string; initial_capital: string; points: Point[]; summary: Summary; monthly: Array<{ month: string; start_date: string; end_date: string; denominator_assets: string | null; ending_assets: string | null; return_pct: string | null; is_partial_month: boolean; quality: string; missing_observation_count: number }>; method: string } }
const qualityName: Record<string, string> = { complete: '完整', fresh: '当日已可得', missing_quotes: '持仓缺价', missing: '缺价', stale_quotes: '使用旧收盘', stale: '旧收盘', availability_unknown: '可得时间未知', available_at_unknown: '可得时间未知', missing_endpoint: '缺少资产端点', stale_endpoint: '端点采用旧收盘', observed_points_only: '仅已观察点', initial_capital_before_first_fill: '初始资金、成交前' }
const money = (value?: string | null) => value == null ? '缺失' : `¥ ${value}`
const pct = (value?: string | null) => value == null ? '缺失' : `${value}%`

function EquityChart({ points, field, title }: { points: Point[]; field: 'total_assets' | 'drawdown_pct'; title: string }) {
  const values = points.flatMap(point => point[field] === null ? [] : [Number(point[field])])
  if (!values.length) return <p className="muted">{title}：没有完整估值点可绘制。</p>
  const min = Math.min(...values), max = Math.max(...values), span = max - min || Math.max(1, max * .01)
  const from = Date.parse(points[0].date), days = Date.parse(points.at(-1)!.date) - from || 1
  const segments: string[] = [], current: string[] = []
  points.forEach(point => {
    if (point[field] === null) { if (current.length) segments.push(current.splice(0).join(' ')); return }
    const x = 52 + (Date.parse(point.date) - from) / days * 656
    const y = 180 - (Number(point[field]) - min) / span * 140
    current.push(`${x},${y}`)
  })
  if (current.length) segments.push(current.join(' '))
  return <figure style={{ margin: '1rem 0' }}><figcaption>{title} · 缺失点断开</figcaption><svg viewBox="0 0 760 220" style={{ width: '100%', display: 'block' }} role="img" aria-label={title}><line x1="52" y1="180" x2="708" y2="180" stroke="currentColor" opacity=".3" /><text x="4" y="42" fill="currentColor" fontSize="12">{max.toFixed(2)}</text><text x="4" y="180" fill="currentColor" fontSize="12">{min.toFixed(2)}</text>{segments.map((segment, index) => <g key={index}><polyline points={segment} fill="none" stroke="var(--action-primary)" strokeWidth="2.5" />{!segment.includes(' ') && <circle cx={segment.split(',')[0]} cy={segment.split(',')[1]} r="3" fill="var(--action-primary)" />}</g>)}<text x="52" y="207" fill="currentColor" fontSize="12">{points[0].date}</text><text x="620" y="207" fill="currentColor" fontSize="12">{points.at(-1)!.date}</text></svg></figure>
}

export function SimEquityReports({ accountId, walletRevision }: { accountId: string; walletRevision?: number }) {
  const root = `/sim-accounts/${accountId}/equity-reports`
  const [inputs, setInputs] = useState<Inputs | null>(null), [datasets, setDatasets] = useState<Dataset[]>([]), [history, setHistory] = useState<Report[]>([])
  const [from, setFrom] = useState(''), [to, setTo] = useState(''), [selected, setSelected] = useState<Record<string, string>>({}), [strict, setStrict] = useState(true)
  const [preview, setPreview] = useState<Report | null>(null), [saved, setSaved] = useState<Report | null>(null), [pointDate, setPointDate] = useState('')
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [deleteId, setDeleteId] = useState('')
  useEffect(() => {
    let live = true
    setInputs(null); setPreview(null); setSaved(null); setError(''); setNotice(''); setSelected({})
    Promise.allSettled([api<Inputs>(root + '/inputs'), api<Dataset[]>('/market/datasets'), api<Report[]>(root)]).then(([meta, quotes, rows]) => {
      if (!live) return
      if (meta.status === 'fulfilled') {
        setInputs(meta.value); setFrom(meta.value.initial_date); setTo(meta.value.wallet_date)
      } else setError(meta.reason instanceof Error ? meta.reason.message : '模拟历史来源读取失败')
      if (quotes.status === 'fulfilled') setDatasets(quotes.value)
      if (rows.status === 'fulfilled') setHistory(rows.value)
    })
    return () => { live = false }
  }, [root, walletRevision])
  useEffect(() => { setPreview(null) }, [from, to, selected, strict])
  const body = { date_from: from, date_to: to, dataset_ids: Object.values(selected).filter(Boolean), strict }
  const shown = saved || preview
  const result = shown?.result
  const point = result?.points.find(item => item.date === pointDate) || result?.points.at(-1)
  async function action(label: string, callback: () => Promise<void>) {
    setBusy(label); setError(''); setNotice('')
    try { await callback() } catch (err) { setError(err instanceof Error ? err.message : '资产曲线操作失败') } finally { setBusy('') }
  }
  return <section className="card"><h2 className="title-with-icon"><Icon name="chart" />模拟资产与回撤曲线</h2><p className="muted">显式选择冻结行情后预览和保存。成交重放使用实际成交金额与当时费用；持仓缺价时总资产为空，不把累计已实现盈亏当作账户净值。</p>
    {error && <p role="alert" className="danger">{error}</p>}{notice && <p role="status">{notice}</p>}
    {inputs && <><p>初始资金 {money(inputs.initial_capital)} · {inputs.initial_date} · 模拟日期 {inputs.wallet_date} · 钱包版本 {inputs.wallet_revision} · {inputs.fill_count} 笔成交</p><div className="form-grid"><label className="field"><span>资产曲线开始日</span><input type="date" min={inputs.initial_date} max={inputs.wallet_date} value={from} onChange={event => setFrom(event.target.value)} /></label><label className="field"><span>资产曲线结束日</span><input type="date" min={from} max={inputs.wallet_date} value={to} onChange={event => setTo(event.target.value)} /></label><label className="check-field"><input type="checkbox" checked={strict} onChange={event => setStrict(event.target.checked)} />资产曲线严格可得时间</label></div>
      <p className="muted">最多 64 个证券、60000 根日线、366 个自然日。选择留空会保留对应持仓缺价；原始价格与交易所身份必须匹配。</p><div className="form-grid">{inputs.symbols.map(symbol => <label className="field" key={symbol}><span>{symbol} 估值样本（当前 {inputs.current_quantities[symbol] || 0} 股）</span><select value={selected[symbol] || ''} onChange={event => setSelected(current => ({ ...current, [symbol]: event.target.value }))}><option value="">未选择，缺价保留未知</option>{datasets.filter(dataset => marketSymbolKey(dataset.symbol) === symbol && dataset.adjustment === 'none').map(dataset => <option key={dataset.id} value={dataset.id}>{dataset.symbol} · {dataset.first_date} 至 {dataset.last_date} · {dataset.id.slice(0, 8)}</option>)}</select></label>)}</div>
      <div className="form-actions"><button type="button" className="button secondary" disabled={Boolean(busy) || !from || !to} onClick={() => action('preview', async () => { const next = await api<Report>(root + '/preview', 'POST', body); setSaved(null); setPreview(next); setPointDate('') })}><Icon name="document" />预览资产曲线</button>{preview && <button type="button" className="button primary" disabled={Boolean(busy)} onClick={() => action('save', async () => { const next = await api<Report>(root, 'POST', { ...body, expected_input_sha256: preview.input_sha256 }); setSaved(next); setPreview(null); setHistory(await api<Report[]>(root)); setNotice('已保存冻结资产报告，可用于当前日完整总资产比例换算。') })}><Icon name="save" />保存资产报告</button>}</div></>}
    {saved && <a className="button secondary" href={`/api/v1${root}/${saved.id}/export.pdf`} download>导出权益曲线 PDF</a>}
    {result && <><h3>{saved ? '已保存资产报告' : '资产报告预览'} · {result.date_from} 至 {result.date_to}</h3><div className="period-summary"><span>期末总资产：{money(result.summary.ending_assets)}</span><span>期末现金：{money(result.summary.ending_cash)}</span><span>区间变化：{pct(result.summary.range_return_pct)}</span><span>分母：{result.summary.range_denominator_date} · {money(result.summary.range_denominator_assets)}</span><span>样本最大回撤：{pct(result.summary.max_drawdown_pct)}</span><span>已观察最大回撤：{pct(result.summary.observed_max_drawdown_pct)}</span><span>缺价观察点：{result.summary.missing_observation_count}</span><span>旧收盘观察点：{result.summary.stale_observation_count}</span></div><p className="muted">{result.method}</p><EquityChart points={result.points} field="total_assets" title="模拟总资产（元）" /><EquityChart points={result.points} field="drawdown_pct" title="已观察资产回撤（%）" />
      <label className="field"><span>资产观察日期</span><select value={point?.date || ''} onChange={event => setPointDate(event.target.value)}>{result.points.map(row => <option key={row.date} value={row.date}>{row.date} · {qualityName[row.quality] || row.quality}</option>)}</select></label>{point && <><div className="period-summary"><span>现金：{money(point.cash)}</span><span>已知市值：{money(point.known_position_value)}</span><span>总资产：{money(point.total_assets)}</span><span>资产指数（初始=1）：{point.asset_index ?? '缺失'}</span><span>累计实际费用：{money(point.cumulative_fees)}</span><span>回撤：{pct(point.drawdown_pct)} · {qualityName[point.drawdown_quality] || point.drawdown_quality}</span></div><div className="table-wrap"><table><thead><tr><th>证券</th><th>数量</th><th>报价日期</th><th>收盘</th><th>市值</th><th>质量</th></tr></thead><tbody>{point.positions.map(row => <tr key={row.symbol}><td>{row.symbol}</td><td>{row.quantity}</td><td>{row.quote_date || '缺失'}</td><td>{row.close || '缺失'}</td><td>{money(row.market_value)}</td><td>{qualityName[row.quality] || row.quality}</td></tr>)}</tbody></table></div>{!point.positions.length && <p className="muted">该观察日没有持仓，总资产等于现金。</p>}</>}
      <h3>月份资产变化</h3><div className="table-wrap"><table><thead><tr><th>月份</th><th>分母日期 / 资产</th><th>结束日期 / 资产</th><th>月内变化</th><th>覆盖</th></tr></thead><tbody>{result.monthly.map(row => <tr key={row.month}><td>{row.month}</td><td>{row.start_date}<br />{money(row.denominator_assets)}</td><td>{row.end_date}<br />{money(row.ending_assets)}</td><td>{pct(row.return_pct)}</td><td>{row.is_partial_month ? '部分月份' : '完整日历月'} · {qualityName[row.quality] || row.quality} · 缺价 {row.missing_observation_count} 点</td></tr>)}</tbody></table></div><details><summary>冻结资产报告标识</summary><p style={{ overflowWrap: 'anywhere' }}>{shown?.id} · 输入摘要 {shown?.input_sha256}</p></details></>}
    <h3>资产报告历史</h3><div className="table-wrap"><table><thead><tr><th>日期范围</th><th>期末资产</th><th>钱包版本</th><th>操作</th></tr></thead><tbody>{history.map(row => <tr key={row.id}><td>{row.date_from} 至 {row.date_to}</td><td>{money(row.summary?.ending_assets)}</td><td>{row.wallet_revision}</td><td><button type="button" className="link-button" disabled={Boolean(busy)} onClick={() => action('history', async () => { setSaved(await api(root + '/' + row.id)); setPreview(null); setPointDate('') })}>查看资产报告</button><button type="button" className="link-button danger" onClick={() => setDeleteId(row.id)}>删除资产报告</button></td></tr>)}</tbody></table></div>{deleteId && <div role="alert"><p>删除这份资产报告，成交和草稿保留。</p><button type="button" disabled={Boolean(busy)} onClick={() => action('delete', async () => { await api(root + '/' + deleteId, 'DELETE'); if (saved?.id === deleteId) setSaved(null); setDeleteId(''); setHistory(await api(root)) })}>确认删除资产报告</button><button type="button" onClick={() => setDeleteId('')}>取消</button></div>}
  </section>
}
