import { useEffect, useState } from 'react'
import { api } from './api'

type DailyLimit = { date: string; day_offset: number; cum_dev_10: number | null; cum_dev_30: number | null; max_dev_10: number | null; max_dev_30: number | null; effective_max_stock_pct: number | null; actual_deviation: number | null; actual_stock_pct: number | null; actual_index_pct: number | null; reset_10: boolean; reset_30: boolean }
type AbnormalEvent = { symbol: string; name: string; dataset_id: string; board: string | null; kind: '10d' | '30d'; status: string; entry_date: string; trigger_date: string | null; as_of_date: string; cum_deviation: number; episode_day: number; threshold: number; warn_threshold: number; benchmark_symbol: string; benchmark_name: string; benchmark_dataset_id: string; daily_limits: DailyLimit[] }
type AbnormalRun = { id: string; date_from: string; date_to: string; scan_mode: string; total_scanned: number; event_count: number; quality_flags: string[]; result?: { trade_date_to: string; algorithm_version: string; symbols_analyzed: number; symbols_skipped_no_index: number; events: AbnormalEvent[]; excluded: Array<{ symbol: string; code: string }> } }
type AbnormalJob = { id: string; kind: string; state: string; total: number; processed: number; run_id: string | null; recent_errors: Array<{ symbol: string; code: string }> }
type Board = 'main' | 'gem' | 'star' | 'beijing' | 'st'
const boardLabels: Record<Board, string> = { main: '主板', gem: '创业板', star: '科创板', beijing: '北交所', st: '包含 ST' }
const storageKey = 'trade-rebuild.abnormal-scan.v1'
function cached() {
  try { return JSON.parse(window.localStorage.getItem(storageKey) || 'null') || {} }
  catch { return {} }
}
const defaultStart = () => { const day = new Date(); day.setDate(day.getDate() - 90); return day.toLocaleDateString('sv-SE') }
const showNumber = (value: number | null) => value === null ? '—' : `${value.toFixed(2)}%`

export function AbnormalScanPanel({ onOpenMarket }: { onOpenMarket: (datasetId: string) => void }) {
  const [dateFrom, setDateFrom] = useState<string>(() => cached().dateFrom || defaultStart())
  const [dateTo, setDateTo] = useState<string>(() => cached().dateTo || new Date().toLocaleDateString('sv-SE'))
  const [markets, setMarkets] = useState<Array<'sh' | 'sz' | 'bj'>>(() => cached().markets || ['sh', 'sz'])
  const [boards, setBoards] = useState<Board[]>(() => cached().boards || [])
  const [mode, setMode] = useState<'snapshot' | 'full'>(() => cached().mode || 'snapshot')
  const [includeWarnings, setIncludeWarnings] = useState<boolean>(() => cached().includeWarnings ?? true)
  const [coolingDays, setCoolingDays] = useState<number>(() => cached().coolingDays ?? 3)
  const [maxBars, setMaxBars] = useState<number>(() => cached().maxBars || 900)
  const [runs, setRuns] = useState<AbnormalRun[]>([])
  const [jobs, setJobs] = useState<AbnormalJob[]>([])
  const [selected, setSelected] = useState<AbnormalRun | null>(null)
  const [selectedEvent, setSelectedEvent] = useState<AbnormalEvent | null>(null)
  const [statusFilter, setStatusFilter] = useState<'all' | 'triggered' | 'warning'>('all')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    try { window.localStorage.setItem(storageKey, JSON.stringify({ dateFrom, dateTo, markets, boards, mode, includeWarnings, coolingDays, maxBars, lastRunId: selected?.id || cached().lastRunId })) }
    catch { /* Keep current form in memory if storage is full. */ }
  }, [dateFrom, dateTo, markets, boards, mode, includeWarnings, coolingDays, maxBars, selected?.id])
  useEffect(() => {
    api<AbnormalRun[]>('/research/abnormal-runs').then(setRuns).catch(() => {})
    const id = cached().lastRunId
    if (id) api<AbnormalRun>('/research/abnormal-runs/' + id).then(setSelected).catch(() => {})
  }, [])
  useEffect(() => {
    let active = true
    const refresh = () => api<AbnormalJob[]>('/research/tdx-universe-jobs').then(rows => {
      if (!active) return
      const abnormalJobs = rows.filter(row => row.kind === 'abnormal')
      setJobs(abnormalJobs)
      if (abnormalJobs.some(row => row.run_id)) api<AbnormalRun[]>('/research/abnormal-runs').then(setRuns).catch(() => {})
    }).catch(() => {})
    refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])
  async function submit() {
    setError(''); setBusy(true)
    try {
      const job = await api<AbnormalJob>('/research/tdx-abnormal-jobs', 'POST', {
        markets, date_from: dateFrom, date_to: dateTo, include_warnings: includeWarnings,
        board_filters: boards, scan_mode: mode, cooling_days: coolingDays, max_bars: maxBars,
      })
      setJobs(current => [job, ...current])
    } catch (err) { setError(err instanceof Error ? err.message : '异动扫描任务提交失败') }
    finally { setBusy(false) }
  }
  async function open(id: string) {
    try { setSelected(await api<AbnormalRun>('/research/abnormal-runs/' + id)); setSelectedEvent(null) }
    catch (err) { setError(err instanceof Error ? err.message : '异动扫描记录读取失败') }
  }
  async function cancel(id: string) {
    try {
      const job = await api<AbnormalJob>(`/research/tdx-universe-jobs/${id}/cancel`, 'POST', {})
      setJobs(current => current.map(item => item.id === id ? job : item))
    } catch (err) { setError(err instanceof Error ? err.message : '取消异动任务失败') }
  }
  const events = selected?.result?.events.filter(item => statusFilter === 'all' || item.status === statusFilter) || []

  return <section className="market-detail"><h3>严重异动扫描 · 冻结行情</h3><p className="muted">按旧版 v4.1 规则计算相对分类指数的 10 日累计偏离 100% / 30 日累计偏离 200%，并展示预警、冷却期合并与日度诊断。通达信指数和股票逐只冻结，历史可得时间未知会标注；缺少基准指数的证券单列，不伪造偏离值。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="form-grid"><label className="field"><span>开始日期</span><input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label className="field"><span>结束日期</span><input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label><label className="field"><span>扫描模式</span><select value={mode} onChange={event => setMode(event.target.value as 'snapshot' | 'full')}><option value="snapshot">截至日仍在异动区</option><option value="full">区间全部异动段</option></select></label><label className="field"><span>冷却交易日</span><input type="number" min="0" max="10" value={coolingDays} onChange={event => setCoolingDays(Number(event.target.value))} /></label><label className="field"><span>每股冻结 K 线数量</span><input type="number" min="251" max="900" value={maxBars} onChange={event => setMaxBars(Number(event.target.value))} /></label><label className="check-field"><input type="checkbox" checked={includeWarnings} onChange={event => setIncludeWarnings(event.target.checked)} />包含预警</label><fieldset><legend>市场</legend>{(['sh', 'sz', 'bj'] as const).map(market => <label key={market} className="check-field"><input type="checkbox" checked={markets.includes(market)} onChange={event => setMarkets(current => event.target.checked ? [...current, market] : current.filter(item => item !== market))} />{({ sh: '上海', sz: '深圳', bj: '北京' } as const)[market]}</label>)}</fieldset><fieldset><legend>板块（不选则全部）</legend>{(Object.keys(boardLabels) as Board[]).map(board => <label key={board} className="check-field"><input type="checkbox" checked={boards.includes(board)} onChange={event => setBoards(current => event.target.checked ? [...current, board] : current.filter(item => item !== board))} />{boardLabels[board]}</label>)}</fieldset></div><button type="button" className="button secondary" disabled={busy || markets.length === 0 || dateFrom > dateTo || maxBars < 251 || maxBars > 900 || (Date.parse(dateTo) - Date.parse(dateFrom)) > 365 * 86400000} onClick={submit}>{busy ? '正在提交…' : '启动全市场异动扫描'}</button>
    {jobs.length > 0 && <div className="table-wrap"><table><thead><tr><th>任务</th><th>进度</th><th>状态</th><th>错误</th><th>操作</th></tr></thead><tbody>{jobs.map(job => <tr key={job.id}><td>{job.id.slice(0, 8)}</td><td>{job.processed}/{job.total}</td><td>{job.state}</td><td>{job.recent_errors.slice(0, 2).map(item => `${item.symbol}：${item.code}`).join('、') || '—'}</td><td>{['queued', 'running', 'cancelling'].includes(job.state) && <button type="button" className="link-button" onClick={() => cancel(job.id)}>取消</button>}{job.run_id && <button type="button" className="link-button" onClick={() => open(job.run_id!)}>查看结果</button>}</td></tr>)}</tbody></table></div>}
    {runs.length > 0 && <details><summary>历史异动扫描 · {runs.length} 次</summary><div className="table-wrap"><table><thead><tr><th>日期范围</th><th>模式</th><th>证券</th><th>事件</th><th>操作</th></tr></thead><tbody>{runs.map(row => <tr key={row.id}><td>{row.date_from} 至 {row.date_to}</td><td>{row.scan_mode === 'snapshot' ? '截至日快照' : '完整区间'}</td><td>{row.total_scanned}</td><td>{row.event_count}</td><td><button type="button" className="link-button" onClick={() => open(row.id)}>查看</button></td></tr>)}</tbody></table></div></details>}
    {selected?.result && <><h4>异动结果 · {selected.id.slice(0, 12)}</h4><p className="muted">算法 {selected.result.algorithm_version}；实际交易截至日 {selected.result.trade_date_to}；扫描 {selected.total_scanned} 只，分析 {selected.result.symbols_analyzed} 只，缺基准 {selected.result.symbols_skipped_no_index} 只，事件 {selected.event_count} 条。质量：{selected.quality_flags.join('、') || '—'}。</p><label className="field"><span>事件状态</span><select value={statusFilter} onChange={event => setStatusFilter(event.target.value as 'all' | 'triggered' | 'warning')}><option value="all">全部</option><option value="triggered">已触发</option><option value="warning">预警</option></select></label><div className="table-wrap"><table><thead><tr><th>证券</th><th>状态</th><th>窗口</th><th>入区 / 触发</th><th>截至日</th><th>累计偏离</th><th>异动段天数</th><th>基准指数</th><th>操作</th></tr></thead><tbody>{events.map((row, index) => <tr key={`${row.symbol}:${row.kind}:${row.entry_date}:${index}`}><td>{row.symbol} {row.name}</td><td>{row.status === 'triggered' ? '已触发' : '预警'}</td><td>{row.kind}</td><td>{row.entry_date} / {row.trigger_date || '—'}</td><td>{row.as_of_date}</td><td>{row.cum_deviation.toFixed(2)}%</td><td>{row.episode_day}</td><td>{row.benchmark_name} {row.benchmark_symbol}</td><td><button type="button" className="link-button" onClick={() => setSelectedEvent(row)}>日度诊断</button><button type="button" className="link-button" onClick={() => onOpenMarket(row.dataset_id)}>查看 K 线</button></td></tr>)}</tbody></table></div>{events.length === 0 && <p className="muted">该模式与过滤条件下没有异动事件。</p>}{selectedEvent && <details open><summary>{selectedEvent.symbol} · {selectedEvent.kind} 日度诊断</summary><div className="table-wrap"><table><thead><tr><th>日期</th><th>日序</th><th>10 日累计偏离</th><th>30 日累计偏离</th><th>有效日涨幅上限</th><th>实际个股涨幅</th><th>实际指数涨幅</th><th>重置</th></tr></thead><tbody>{selectedEvent.daily_limits.map((row, index) => <tr key={`${row.date}:${index}`}><td>{row.date}</td><td>{row.day_offset}</td><td>{showNumber(row.cum_dev_10)}</td><td>{showNumber(row.cum_dev_30)}</td><td>{showNumber(row.effective_max_stock_pct)}</td><td>{showNumber(row.actual_stock_pct)}</td><td>{showNumber(row.actual_index_pct)}</td><td>{row.reset_10 || row.reset_30 ? '是' : '否'}</td></tr>)}</tbody></table></div></details>}</>}
  </section>
}
