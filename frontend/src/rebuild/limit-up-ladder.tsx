import { useEffect, useState } from 'react'
import { api } from './api'

type LadderPoint = { symbol: string; name: string; dataset_id: string; date: string; board_height: number }
type LadderStock = { symbol: string; name: string; dataset_id: string; max_board_height: number; active_days: number; latest_board_height: number }
type LadderRun = { id: string; date_from: string; date_to: string; total_scanned: number; stock_count: number; quality_flags: string[]; result?: { dates: string[]; timeline: LadderPoint[]; summaries: LadderStock[]; data_scope: string } }
type LadderJob = { id: string; kind: string; state: string; total: number; processed: number; run_id: string | null; recent_errors: Array<{ symbol: string; code: string }> }
type Board = 'main' | 'gem' | 'star' | 'beijing' | 'st'
const boardLabels: Record<Board, string> = { main: '主板', gem: '创业板', star: '科创板', beijing: '北交所', st: '包含 ST' }
const storageKey = 'trade-rebuild.limit-up-ladder.v1'
function cached() {
  try { return JSON.parse(window.localStorage.getItem(storageKey) || 'null') || {} }
  catch { return {} }
}
const defaultStart = () => { const day = new Date(); day.setDate(day.getDate() - 30); return day.toLocaleDateString('sv-SE') }

function LadderChart({ run, selectedDate, onDate }: {
  run: LadderRun; selectedDate: string; onDate: (date: string) => void
}) {
  const result = run.result
  if (!result || result.timeline.length === 0) return null
  const dates = result.dates
  const height = Math.max(1, ...result.timeline.map(point => point.board_height))
  const ticks = height <= 10 ? Array.from({ length: height }, (_, index) => index + 1)
    : [...new Set([1, Math.ceil(height / 4), Math.ceil(height / 2), Math.ceil(height * 3 / 4), height])]
  const x = (day: string) => 42 + (dates.indexOf(day) / Math.max(dates.length - 1, 1)) * 700
  const y = (level: number) => 186 - ((level - 1) / Math.max(height - 1, 1)) * 145
  return <figure><svg viewBox="0 0 790 230" role="img" aria-label="涨停梯队层级时间图" style={{ width: '100%', minHeight: 180 }}>
    <line x1="42" y1="186" x2="742" y2="186" stroke="currentColor" opacity="0.35" />
    {ticks.map(level => <g key={level}><line x1="42" y1={y(level)} x2="742" y2={y(level)} stroke="currentColor" opacity="0.12" /><text x="6" y={y(level) + 4} fill="currentColor" fontSize="12">{level} 板</text></g>)}
    {result.timeline.map(point => <circle key={`${point.date}:${point.symbol}`} cx={x(point.date)} cy={y(point.board_height)} r={point.date === selectedDate ? 5 : 3.5} fill="var(--action-primary)" opacity={point.date === selectedDate ? 1 : 0.72} tabIndex={0} onClick={() => onDate(point.date)}><title>{point.date} · {point.symbol} {point.name} · {point.board_height} 板</title></circle>)}
    <text x="42" y="218" fill="currentColor" fontSize="12">{dates[0]}</text><text x="660" y="218" fill="currentColor" fontSize="12">{dates[dates.length - 1]}</text>
  </svg><figcaption className="muted">横轴为日期，纵轴为连板高度；点选一个日期可查看证券明细。</figcaption></figure>
}

export function LimitUpLadderPanel({ datasetIds, onOpenMarket }: {
  datasetIds: string[]; onOpenMarket: (datasetId: string) => void
}) {
  const [dateFrom, setDateFrom] = useState<string>(() => cached().dateFrom || defaultStart())
  const [dateTo, setDateTo] = useState<string>(() => cached().dateTo || new Date().toLocaleDateString('sv-SE'))
  const [recentDays, setRecentDays] = useState<number>(() => cached().recentDays || 5)
  const [minBoards, setMinBoards] = useState<number>(() => cached().minBoards || 3)
  const [boards, setBoards] = useState<Board[]>(() => cached().boards || ['main', 'gem', 'star'])
  const [markets, setMarkets] = useState<Array<'sh' | 'sz' | 'bj'>>(() => cached().markets || ['sh', 'sz'])
  const [maxBars, setMaxBars] = useState<number>(() => cached().maxBars || 360)
  const [runs, setRuns] = useState<LadderRun[]>([])
  const [jobs, setJobs] = useState<LadderJob[]>([])
  const [selected, setSelected] = useState<LadderRun | null>(null)
  const [shownDate, setShownDate] = useState('')
  const [minimumHeight, setMinimumHeight] = useState(1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    try { window.localStorage.setItem(storageKey, JSON.stringify({ dateFrom, dateTo, recentDays, minBoards, boards, markets, maxBars, lastRunId: selected?.id || cached().lastRunId })) }
    catch { /* Keep the current form in memory if storage is full. */ }
  }, [dateFrom, dateTo, recentDays, minBoards, boards, markets, maxBars, selected?.id])
  useEffect(() => {
    api<LadderRun[]>('/research/limit-up-ladder-runs').then(setRuns).catch(() => {})
    const id = cached().lastRunId
    if (id) api<LadderRun>('/research/limit-up-ladder-runs/' + id).then(setSelected).catch(() => {})
  }, [])
  useEffect(() => {
    let active = true
    const refresh = () => api<LadderJob[]>('/research/tdx-universe-jobs').then(rows => {
      if (!active) return
      const ladderJobs = rows.filter(row => row.kind === 'ladder')
      setJobs(ladderJobs)
      if (ladderJobs.some(row => row.run_id)) api<LadderRun[]>('/research/limit-up-ladder-runs').then(setRuns).catch(() => {})
    }).catch(() => {})
    refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  async function run(market: boolean) {
    setError(''); setBusy(true)
    try {
      const params = { date_from: dateFrom, date_to: dateTo, recent_days: recentDays,
        historical_min_boards: minBoards, board_filters: boards }
      if (market) {
        const saved = await api<LadderJob>('/research/tdx-ladder-jobs', 'POST', {
          ...params, markets, max_bars: maxBars,
        })
        setJobs(current => [saved, ...current])
      } else {
        const saved = await api<LadderRun>('/research/limit-up-ladder-runs', 'POST', {
          ...params, dataset_ids: datasetIds,
        })
        setSelected(saved)
        setRuns(await api<LadderRun[]>('/research/limit-up-ladder-runs'))
      }
    } catch (err) { setError(err instanceof Error ? err.message : '涨停梯队扫描失败') }
    finally { setBusy(false) }
  }
  async function open(id: string) {
    try { setSelected(await api<LadderRun>('/research/limit-up-ladder-runs/' + id)) }
    catch (err) { setError(err instanceof Error ? err.message : '涨停梯队记录读取失败') }
  }
  async function cancel(id: string) {
    try {
      const job = await api<LadderJob>(`/research/tdx-universe-jobs/${id}/cancel`, 'POST', {})
      setJobs(current => current.map(item => item.id === id ? job : item))
    } catch (err) { setError(err instanceof Error ? err.message : '取消任务失败') }
  }
  const latestDate = selected?.result?.dates.at(-1) || ''
  const day = shownDate && selected?.result?.dates.includes(shownDate) ? shownDate : latestDate
  const points = selected?.result?.timeline.filter(point => point.date === day && point.board_height >= minimumHeight) || []
  const stocks = selected?.result?.summaries || []

  return <section className="market-detail"><h3>涨停梯队 · 冻结行情扫描</h3><p className="muted">按旧版规则计算连板高度：主板 10%、创业板 / 科创板 20%、北交所 30%，允许 0.2 个百分点价格误差。近期日期展示首板，较早日期只展示达到指定高度的股票。ST 名称缺失时板块过滤可能不准确。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="form-grid"><label className="field"><span>开始日期</span><input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label className="field"><span>结束日期</span><input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label><label className="field"><span>近期交易日</span><input type="number" min="1" max="30" value={recentDays} onChange={event => setRecentDays(Number(event.target.value))} /></label><label className="field"><span>历史最低连板高度</span><input type="number" min="2" max="10" value={minBoards} onChange={event => setMinBoards(Number(event.target.value))} /></label><fieldset><legend>板块</legend>{(Object.keys(boardLabels) as Board[]).map(board => <label key={board} className="check-field"><input type="checkbox" checked={boards.includes(board)} onChange={event => setBoards(current => event.target.checked ? [...current, board] : current.filter(item => item !== board))} />{boardLabels[board]}</label>)}</fieldset></div>
    <button type="button" className="button secondary" disabled={busy || datasetIds.length === 0 || dateFrom > dateTo} onClick={() => run(false)}>{busy ? '正在扫描…' : `扫描已选 ${datasetIds.length} 只`}</button>
    <h4>通达信全市场涨停梯队</h4><p className="muted">后台逐只冻结行情并计算连板，保存历史输入与结果。全市场单次最多 90 自然日；通达信文件逐只冻结，非同一瞬间快照。</p><div className="form-grid"><fieldset><legend>市场</legend>{(['sh', 'sz', 'bj'] as const).map(market => <label key={market} className="check-field"><input type="checkbox" checked={markets.includes(market)} onChange={event => setMarkets(current => event.target.checked ? [...current, market] : current.filter(item => item !== market))} />{({ sh: '上海', sz: '深圳', bj: '北京' } as const)[market]}</label>)}</fieldset><label className="field"><span>每股冻结 K 线数量</span><input type="number" min="251" max="1000" value={maxBars} onChange={event => setMaxBars(Number(event.target.value))} /></label></div><button type="button" className="button secondary" disabled={busy || markets.length === 0 || maxBars < 251 || maxBars > 1000 || dateFrom > dateTo || (Date.parse(dateTo) - Date.parse(dateFrom)) > 90 * 86400000} onClick={() => run(true)}>启动全市场梯队任务</button>
    {jobs.length > 0 && <div className="table-wrap"><table><thead><tr><th>任务</th><th>进度</th><th>状态</th><th>错误</th><th>操作</th></tr></thead><tbody>{jobs.map(job => <tr key={job.id}><td>{job.id.slice(0, 8)}</td><td>{job.processed}/{job.total}</td><td>{job.state}</td><td>{job.recent_errors.slice(0, 2).map(item => `${item.symbol}：${item.code}`).join('、') || '—'}</td><td>{['queued', 'running', 'cancelling'].includes(job.state) && <button type="button" className="link-button" onClick={() => cancel(job.id)}>取消</button>}{job.run_id && <button type="button" className="link-button" onClick={() => open(job.run_id!)}>查看</button>}</td></tr>)}</tbody></table></div>}
    {runs.length > 0 && <details><summary>历史梯队扫描 · {runs.length} 次</summary><div className="table-wrap"><table><thead><tr><th>日期范围</th><th>扫描</th><th>入榜</th><th>操作</th></tr></thead><tbody>{runs.map(row => <tr key={row.id}><td>{row.date_from} 至 {row.date_to}</td><td>{row.total_scanned}</td><td>{row.stock_count}</td><td><button type="button" className="link-button" onClick={() => open(row.id)}>查看</button></td></tr>)}</tbody></table></div></details>}
    {selected?.result && <><h4>梯队结果 · {selected.id.slice(0, 12)}</h4><p className="muted">{selected.result.data_scope === 'tdx_full_market' ? '通达信全市场' : '已选冻结样本'}；扫描 {selected.total_scanned} 只，入榜 {selected.stock_count} 只；质量：{selected.quality_flags.join('、') || '已提供历史可得时间'}。</p><LadderChart run={selected} selectedDate={day} onDate={setShownDate} /><div className="form-grid"><label className="field"><span>查看日期</span><select value={day} onChange={event => setShownDate(event.target.value)}>{selected.result.dates.map(value => <option key={value} value={value}>{value}</option>)}</select></label><label className="field"><span>最低连板高度</span><input type="number" min="1" value={minimumHeight} onChange={event => setMinimumHeight(Number(event.target.value))} /></label></div><div className="table-wrap"><table><thead><tr><th>层级</th><th>证券</th><th>操作</th></tr></thead><tbody>{points.map(point => <tr key={`${point.date}:${point.symbol}`}><td>{point.board_height} 板</td><td>{point.symbol} {point.name}</td><td><button type="button" className="link-button" onClick={() => onOpenMarket(point.dataset_id)}>查看 K 线</button></td></tr>)}</tbody></table></div>{points.length === 0 && <p className="muted">该日没有符合展示规则的连板证券。</p>}<details><summary>区间股票汇总 · {stocks.length} 只</summary><div className="table-wrap"><table><thead><tr><th>证券</th><th>最高连板</th><th>入榜天数</th><th>最近连板</th><th>操作</th></tr></thead><tbody>{stocks.map(row => <tr key={row.dataset_id}><td>{row.symbol} {row.name}</td><td>{row.max_board_height}</td><td>{row.active_days}</td><td>{row.latest_board_height}</td><td><button type="button" className="link-button" onClick={() => onOpenMarket(row.dataset_id)}>查看 K 线</button></td></tr>)}</tbody></table></div></details></>}
  </section>
}

