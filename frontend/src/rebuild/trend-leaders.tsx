import { useEffect, useState } from 'react'
import { api } from './api'

type Leader = { symbol: string; name: string; dataset_id: string; return_pct: number; window_return_pct: number; amount_avg: number; board: string | null; leader_days: number; first_leader_date: string; last_leader_date: string; max_return_pct: number }
type Series = { symbol: string; dataset_id: string; points: Array<{ date: string; return_pct: number }> }
type TrendRun = { id: string; date_from: string; date_to: string; total_scanned: number; leader_count: number; quality_flags: string[]; result?: { dates: string[]; leaders: Leader[]; series: Series[]; daily_leaders: Record<string, string[]>; data_scope: string } }
type TrendJob = { id: string; kind: string; state: string; total: number; processed: number; run_id: string | null; recent_errors: Array<{ symbol: string; code: string }> }
type Board = 'main' | 'gem' | 'star' | 'beijing' | 'st'
const boardLabels: Record<Board, string> = { main: '主板', gem: '创业板', star: '科创板', beijing: '北交所', st: '包含 ST' }
const storageKey = 'trade-rebuild.trend-leaders.v1'
const defaultStart = () => { const d = new Date(); d.setDate(d.getDate() - 90); return d.toLocaleDateString('sv-SE') }
function cached() {
  try { return JSON.parse(window.localStorage.getItem(storageKey) || 'null') || {} }
  catch { return {} }
}

function TrendCurve({ run }: { run: TrendRun }) {
  const [symbol, setSymbol] = useState('')
  const rows = run.result?.series || []
  const active = rows.find(row => row.symbol === symbol) || rows[0]
  const points = active?.points || []
  const values = points.map(point => point.return_pct)
  const low = Math.min(0, ...values)
  const high = Math.max(0, ...values)
  const span = high - low || 1
  const x = (index: number) => 44 + (index / Math.max(points.length - 1, 1)) * 710
  const y = (value: number) => 190 - ((value - low) / span) * 150
  return <details><summary>区间涨幅曲线与每日 Top N</summary>
    {active && <><label className="field"><span>查看证券</span><select value={active.symbol} onChange={event => setSymbol(event.target.value)}>{rows.map(row => <option key={row.symbol} value={row.symbol}>{row.symbol}</option>)}</select></label>
      {points.length > 0 && <figure><svg viewBox="0 0 800 230" role="img" aria-label={`${active.symbol} 区间涨幅曲线`} style={{ width: '100%', minHeight: 180 }}><line x1="44" y1={y(0)} x2="754" y2={y(0)} stroke="currentColor" opacity="0.35" /><polyline fill="none" stroke="var(--action-primary)" strokeWidth="2" points={points.map((point, index) => `${x(index)},${y(point.return_pct)}`).join(' ')} />{points.map((point, index) => <circle key={point.date} cx={x(index)} cy={y(point.return_pct)} r="3" fill="var(--action-primary)"><title>{point.date}：{point.return_pct.toFixed(2)}%</title></circle>)}<text x="2" y="32" fill="currentColor" fontSize="12">{high.toFixed(1)}%</text><text x="2" y="197" fill="currentColor" fontSize="12">{low.toFixed(1)}%</text><text x="44" y="218" fill="currentColor" fontSize="12">{points[0].date}</text><text x="672" y="218" fill="currentColor" fontSize="12">{points[points.length - 1].date}</text></svg><figcaption className="muted">以区间首个可用收盘价为基准；鼠标悬停可查看每日涨幅。</figcaption></figure>}</>}
    <div className="table-wrap"><table><thead><tr><th>日期</th><th>每日 Top N</th></tr></thead><tbody>{run.result?.dates.map(day => <tr key={day}><td>{day}</td><td>{run.result?.daily_leaders[day]?.join('、') || '无符合条件的证券'}</td></tr>)}</tbody></table></div>
  </details>
}

export function TrendLeadersPanel({ datasetIds, onOpenMarket }: {
  datasetIds: string[]; onOpenMarket: (datasetId: string) => void
}) {
  const [dateFrom, setDateFrom] = useState<string>(() => cached().dateFrom || defaultStart())
  const [dateTo, setDateTo] = useState<string>(() => cached().dateTo || new Date().toLocaleDateString('sv-SE'))
  const [windowDays, setWindowDays] = useState<number>(() => cached().windowDays || 20)
  const [dailyTopN, setDailyTopN] = useState<number>(() => cached().dailyTopN || 5)
  const [minAmount, setMinAmount] = useState<number>(() => cached().minAmount ?? 5e7)
  const [boards, setBoards] = useState<Board[]>(() => cached().boards || ['main', 'gem', 'star'])
  const [markets, setMarkets] = useState<Array<'sh' | 'sz' | 'bj'>>(() => cached().markets || ['sh', 'sz'])
  const [maxBars, setMaxBars] = useState<number>(() => cached().maxBars || 360)
  const [jobs, setJobs] = useState<TrendJob[]>([])
  const [runs, setRuns] = useState<TrendRun[]>([])
  const [selected, setSelected] = useState<TrendRun | null>(null)
  const [keyword, setKeyword] = useState('')
  const [minDays, setMinDays] = useState(1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    try { window.localStorage.setItem(storageKey, JSON.stringify({ dateFrom, dateTo, windowDays, dailyTopN, minAmount, boards, markets, maxBars, lastRunId: selected?.id || cached().lastRunId })) }
    catch { /* Preserve usable in-memory settings. */ }
  }, [dateFrom, dateTo, windowDays, dailyTopN, minAmount, boards, markets, maxBars, selected?.id])
  useEffect(() => {
    api<TrendRun[]>('/research/trend-leader-runs').then(setRuns).catch(() => {})
    const lastId = cached().lastRunId
    if (lastId) api<TrendRun>('/research/trend-leader-runs/' + lastId).then(setSelected).catch(() => {})
  }, [])
  useEffect(() => {
    let active = true
    const refresh = () => api<TrendJob[]>('/research/tdx-universe-jobs').then(rows => {
      if (!active) return
      const trendJobs = rows.filter(row => row.kind === 'trend')
      setJobs(trendJobs)
      if (trendJobs.some(row => row.run_id)) api<TrendRun[]>('/research/trend-leader-runs').then(setRuns).catch(() => {})
    }).catch(() => {})
    refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  async function run() {
    setError(''); setBusy(true)
    try {
      const saved = await api<TrendRun>('/research/trend-leader-runs', 'POST', {
        dataset_ids: datasetIds, date_from: dateFrom, date_to: dateTo,
        window_days: windowDays, daily_top_n: dailyTopN,
        board_filters: boards, min_amount_avg: minAmount,
      })
      setSelected(saved)
      setRuns(await api<TrendRun[]>('/research/trend-leader-runs'))
    } catch (err) { setError(err instanceof Error ? err.message : '趋势龙头扫描失败') }
    finally { setBusy(false) }
  }
  async function runMarket() {
    setError(''); setBusy(true)
    try {
      const saved = await api<TrendJob>('/research/tdx-trend-jobs', 'POST', {
        markets, date_from: dateFrom, date_to: dateTo, window_days: windowDays,
        daily_top_n: dailyTopN, board_filters: boards, min_amount_avg: minAmount,
        max_bars: maxBars,
      })
      setJobs(current => [saved, ...current])
    } catch (err) { setError(err instanceof Error ? err.message : '全市场趋势任务提交失败') }
    finally { setBusy(false) }
  }
  async function cancelJob(id: string) {
    try {
      const job = await api<TrendJob>(`/research/tdx-universe-jobs/${id}/cancel`, 'POST', {})
      setJobs(current => current.map(item => item.id === id ? job : item))
    } catch (err) { setError(err instanceof Error ? err.message : '取消趋势任务失败') }
  }
  async function open(id: string) {
    try { setSelected(await api<TrendRun>('/research/trend-leader-runs/' + id)) }
    catch (err) { setError(err instanceof Error ? err.message : '趋势龙头记录读取失败') }
  }
  const shown = selected?.result?.leaders.filter(row =>
    row.leader_days >= minDays && (`${row.symbol} ${row.name}`).toLowerCase().includes(keyword.trim().toLowerCase())) || []

  return <section className="market-detail"><h3>趋势龙头 · 冻结行情扫描</h3><p className="muted">按每日窗口涨幅及平均成交额选 Top N，统计领跑次数。可使用上方已选的冻结样本，或从通达信目录启动全市场后台任务。缺成交额的日期不会通过金额过滤；通达信文件逐只冻结，结果并非同一瞬间快照。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="form-grid"><label className="field"><span>开始日期</span><input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label className="field"><span>结束日期</span><input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label><label className="field"><span>涨幅窗口（交易日）</span><input type="number" min="5" max="120" value={windowDays} onChange={event => setWindowDays(Number(event.target.value))} /></label><label className="field"><span>每日 Top N</span><input type="number" min="1" max="20" value={dailyTopN} onChange={event => setDailyTopN(Number(event.target.value))} /></label><label className="field"><span>平均成交额下限（元）</span><input type="number" min="0" step="1000000" value={minAmount} onChange={event => setMinAmount(Number(event.target.value))} /></label><fieldset><legend>板块</legend>{(Object.keys(boardLabels) as Board[]).map(board => <label key={board} className="check-field"><input type="checkbox" checked={boards.includes(board)} onChange={event => setBoards(current => event.target.checked ? [...current, board] : current.filter(item => item !== board))} />{boardLabels[board]}</label>)}</fieldset></div>
    <button type="button" className="button secondary" disabled={busy || datasetIds.length === 0 || dateFrom > dateTo} onClick={run}>{busy ? '正在扫描…' : `扫描已选 ${datasetIds.length} 只`}</button>
    <h4>通达信全市场趋势扫描</h4><p className="muted">一次最多 90 自然日、每日 Top N 不超过 10，只读取所选市场的本地 A 股日线。每只股票冻结最近指定数量的 K 线；较早日期请增大数量。任务进度、取消和重启恢复沿用全市场任务队列。</p><div className="form-grid"><fieldset><legend>市场</legend>{(['sh', 'sz', 'bj'] as const).map(market => <label key={market} className="check-field"><input type="checkbox" checked={markets.includes(market)} onChange={event => setMarkets(current => event.target.checked ? [...current, market] : current.filter(item => item !== market))} />{({ sh: '上海', sz: '深圳', bj: '北京' } as const)[market]}</label>)}</fieldset><label className="field"><span>每股冻结 K 线数量</span><input type="number" min="251" max="1000" value={maxBars} onChange={event => setMaxBars(Number(event.target.value))} /></label></div><button type="button" className="button secondary" disabled={busy || markets.length === 0 || maxBars < 251 || maxBars > 1000 || dailyTopN > 10 || (Date.parse(dateTo) - Date.parse(dateFrom)) > 90 * 86400000 || dateFrom > dateTo} onClick={runMarket}>{busy ? '正在提交…' : '启动全市场趋势任务'}</button>
    {jobs.length > 0 && <div className="table-wrap"><table><thead><tr><th>任务</th><th>进度</th><th>状态</th><th>错误</th><th>操作</th></tr></thead><tbody>{jobs.map(job => <tr key={job.id}><td>{job.id.slice(0, 8)}</td><td>{job.processed}/{job.total}</td><td>{job.state}</td><td>{job.recent_errors.slice(0, 2).map(item => `${item.symbol}：${item.code}`).join('、') || '—'}</td><td>{['queued', 'running', 'cancelling'].includes(job.state) && <button type="button" className="link-button" onClick={() => cancelJob(job.id)}>取消</button>}{job.run_id && <button type="button" className="link-button" onClick={() => open(job.run_id!)}>查看结果</button>}</td></tr>)}</tbody></table></div>}
    {runs.length > 0 && <details><summary>历史趋势龙头扫描 · {runs.length} 次</summary><div className="table-wrap"><table><thead><tr><th>日期范围</th><th>样本</th><th>龙头</th><th>操作</th></tr></thead><tbody>{runs.map(item => <tr key={item.id}><td>{item.date_from} 至 {item.date_to}</td><td>{item.total_scanned}</td><td>{item.leader_count}</td><td><button type="button" className="link-button" onClick={() => open(item.id)}>查看</button></td></tr>)}</tbody></table></div></details>}
    {selected?.result && <p className="muted">结果范围：{selected.result.data_scope === 'tdx_full_market' ? '通达信全市场' : '已选冻结样本'}</p>}
    {selected?.result && <TrendCurve key={selected.id} run={selected} />}
    {selected?.result && <><h4>结果 · {selected.id.slice(0, 12)}</h4><p className="muted">共 {selected.result.dates.length} 个交易日期，领跑证券 {selected.leader_count} 只；质量：{selected.quality_flags.join('、') || '已提供历史可得时间'}。</p><div className="form-grid"><label className="field"><span>代码 / 名称筛选</span><input value={keyword} onChange={event => setKeyword(event.target.value)} /></label><label className="field"><span>至少领跑天数</span><input type="number" min="1" value={minDays} onChange={event => setMinDays(Number(event.target.value))} /></label></div><div className="table-wrap"><table><thead><tr><th>证券</th><th>板块</th><th>领跑天数</th><th>首次 / 最近</th><th>窗口涨幅</th><th>当日涨幅</th><th>最大区间涨幅</th><th>平均成交额</th><th>操作</th></tr></thead><tbody>{shown.map(row => <tr key={row.dataset_id}><td>{row.symbol} {row.name}</td><td>{row.board || '未知'}</td><td>{row.leader_days}</td><td>{row.first_leader_date} / {row.last_leader_date}</td><td>{row.window_return_pct.toFixed(2)}%</td><td>{row.return_pct.toFixed(2)}%</td><td>{row.max_return_pct.toFixed(2)}%</td><td>{row.amount_avg.toLocaleString()}</td><td><button type="button" className="link-button" onClick={() => onOpenMarket(row.dataset_id)}>查看 K 线</button></td></tr>)}</tbody></table></div>{shown.length === 0 && <p className="muted">当前条件下没有龙头证券。</p>}</>}
  </section>
}
