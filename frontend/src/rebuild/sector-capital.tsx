import { useEffect, useState } from 'react'
import { api } from './api'

type FlowRow = { date: string; sector: string; return_pct: number; flow_score: number; amount: number; amount_delta: number; rank_return: number; rank_flow: number }
type Leader = { sector: string; leader_days: number; max_return_pct: number; avg_flow_score: number; first_leader_date: string; last_leader_date: string }
type Series = { sector: string; points: Array<{ date: string; return_pct: number; flow_score: number }> }
type SectorRun = { id: string; date_from: string; date_to: string; loaded_index_count: number; leader_count: number; quality_flags: string[]; result?: { dates: string[]; flow_table: FlowRow[]; series: Series[]; leaders: Leader[]; loaded_sector_count: number; missing_index_codes: string[]; metric_label: string } }
type SectorJob = { id: string; kind: string; state: string; total: number; processed: number; run_id: string | null; recent_errors: Array<{ symbol: string; code: string }> }
const storageKey = 'trade-rebuild.sector-capital.v1'
function cached() {
  try { return JSON.parse(window.localStorage.getItem(storageKey) || 'null') || {} }
  catch { return {} }
}
const defaultStart = () => { const day = new Date(); day.setFullYear(day.getFullYear() - 3); return day.toLocaleDateString('sv-SE') }

function SectorCurve({ series, mode }: { series: Series; mode: 'flow' | 'return' }) {
  const points = series.points
  if (points.length === 0) return <p className="muted">所选板块没有可用曲线。</p>
  const values = points.map(point => mode === 'flow' ? point.flow_score : point.return_pct)
  const low = Math.min(0, ...values)
  const high = Math.max(0, ...values)
  const span = high - low || 1
  const x = (index: number) => 48 + index / Math.max(points.length - 1, 1) * 700
  const y = (value: number) => 188 - (value - low) / span * 150
  return <figure><svg viewBox="0 0 790 230" role="img" aria-label={`${series.sector}${mode === 'flow' ? '资金热度' : '区间涨幅'}曲线`} style={{ width: '100%', minHeight: 180 }}><line x1="48" y1={y(0)} x2="748" y2={y(0)} stroke="currentColor" opacity="0.35" /><polyline fill="none" stroke="var(--action-primary)" strokeWidth="2" points={values.map((value, index) => `${x(index)},${y(value)}`).join(' ')} />{points.filter((_, index) => index % Math.max(1, Math.floor(points.length / 60)) === 0 || index === points.length - 1).map(point => { const index = points.indexOf(point); const value = mode === 'flow' ? point.flow_score : point.return_pct; return <circle key={point.date} cx={x(index)} cy={y(value)} r="3" fill="var(--action-primary)"><title>{point.date}：{value.toFixed(2)}%</title></circle> })}<text x="2" y="31" fill="currentColor" fontSize="12">{high.toFixed(1)}%</text><text x="2" y="194" fill="currentColor" fontSize="12">{low.toFixed(1)}%</text><text x="48" y="218" fill="currentColor" fontSize="12">{points[0].date}</text><text x="660" y="218" fill="currentColor" fontSize="12">{points[points.length - 1].date}</text></svg><figcaption className="muted">资金热度为当日板块指数成交额相对窗口均值的百分比变化，属于旧版代理指标，不是真实资金净流入。</figcaption></figure>
}

export function SectorCapitalPanel() {
  const [dateFrom, setDateFrom] = useState<string>(() => cached().dateFrom || defaultStart())
  const [dateTo, setDateTo] = useState<string>(() => cached().dateTo || new Date().toLocaleDateString('sv-SE'))
  const [dailyTopN, setDailyTopN] = useState<number>(() => cached().dailyTopN || 5)
  const [flowWindow, setFlowWindow] = useState<number>(() => cached().flowWindow || 5)
  const [maxBars, setMaxBars] = useState<number>(() => cached().maxBars || 1000)
  const [runs, setRuns] = useState<SectorRun[]>([])
  const [jobs, setJobs] = useState<SectorJob[]>([])
  const [selected, setSelected] = useState<SectorRun | null>(null)
  const [focusDate, setFocusDate] = useState('')
  const [focusSector, setFocusSector] = useState('')
  const [chartMode, setChartMode] = useState<'flow' | 'return'>(() => cached().chartMode || 'flow')
  const [tableFilter, setTableFilter] = useState<'all' | 'top_flow' | 'top_return'>(() => cached().tableFilter || 'top_flow')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    try { window.localStorage.setItem(storageKey, JSON.stringify({ dateFrom, dateTo, dailyTopN, flowWindow, maxBars, chartMode, tableFilter, lastRunId: selected?.id || cached().lastRunId })) }
    catch { /* Keep the current form in memory if storage is full. */ }
  }, [dateFrom, dateTo, dailyTopN, flowWindow, maxBars, chartMode, tableFilter, selected?.id])
  useEffect(() => {
    api<SectorRun[]>('/research/sector-flow-runs').then(setRuns).catch(() => {})
    const id = cached().lastRunId
    if (id) api<SectorRun>('/research/sector-flow-runs/' + id).then(setSelected).catch(() => {})
  }, [])
  useEffect(() => {
    let active = true
    const refresh = () => api<SectorJob[]>('/research/tdx-universe-jobs').then(rows => {
      if (!active) return
      const sectorJobs = rows.filter(row => row.kind === 'sector')
      setJobs(sectorJobs)
      if (sectorJobs.some(row => row.run_id)) api<SectorRun[]>('/research/sector-flow-runs').then(setRuns).catch(() => {})
    }).catch(() => {})
    refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])
  async function submit() {
    setError(''); setBusy(true)
    try {
      const job = await api<SectorJob>('/research/tdx-sector-flow-jobs', 'POST', {
        date_from: dateFrom, date_to: dateTo, daily_top_n: dailyTopN,
        flow_window: flowWindow, max_bars: maxBars,
      })
      setJobs(current => [job, ...current])
    } catch (err) { setError(err instanceof Error ? err.message : '板块资金扫描提交失败') }
    finally { setBusy(false) }
  }
  async function open(id: string) {
    try { setSelected(await api<SectorRun>('/research/sector-flow-runs/' + id)) }
    catch (err) { setError(err instanceof Error ? err.message : '板块资金记录读取失败') }
  }
  async function cancel(id: string) {
    try {
      const job = await api<SectorJob>(`/research/tdx-universe-jobs/${id}/cancel`, 'POST', {})
      setJobs(current => current.map(item => item.id === id ? job : item))
    } catch (err) { setError(err instanceof Error ? err.message : '取消任务失败') }
  }
  const latestDate = selected?.result?.dates.at(-1) || ''
  const day = focusDate && selected?.result?.dates.includes(focusDate) ? focusDate : latestDate
  const leaderSeries = selected?.result?.series || []
  const sector = leaderSeries.find(item => item.sector === focusSector) || leaderSeries[0]
  const rows = selected?.result?.flow_table.filter(row => row.date === day && (tableFilter === 'all' ||
    (tableFilter === 'top_flow' ? row.rank_flow : row.rank_return) <= dailyTopN)) || []

  return <section className="market-detail"><h3>板块资金热度 · 通达信指数</h3><p className="muted">旧版“板块资金”使用板块指数成交额相对滚动均值计算资金热度，结合板块涨幅排名；这是成交额代理指标，不是真实资金净流入。后台逐只冻结本地行业指数日线并保留来源。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="form-grid"><label className="field"><span>开始日期</span><input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label className="field"><span>结束日期</span><input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label><label className="field"><span>每日 Top N</span><input type="number" min="1" max="15" value={dailyTopN} onChange={event => setDailyTopN(Number(event.target.value))} /></label><label className="field"><span>成交额均值窗口（交易日）</span><input type="number" min="3" max="60" value={flowWindow} onChange={event => setFlowWindow(Number(event.target.value))} /></label><label className="field"><span>每指数冻结 K 线数量</span><input type="number" min="251" max="2000" value={maxBars} onChange={event => setMaxBars(Number(event.target.value))} /></label></div><button type="button" className="button secondary" disabled={busy || dateFrom > dateTo || maxBars < 251 || maxBars > 2000} onClick={submit}>{busy ? '正在提交…' : '扫描板块资金热度'}</button>
    {jobs.length > 0 && <div className="table-wrap"><table><thead><tr><th>任务</th><th>指数进度</th><th>状态</th><th>错误</th><th>操作</th></tr></thead><tbody>{jobs.map(job => <tr key={job.id}><td>{job.id.slice(0, 8)}</td><td>{job.processed}/{job.total}</td><td>{job.state}</td><td>{job.recent_errors.slice(0, 2).map(item => `${item.symbol}：${item.code}`).join('、') || '—'}</td><td>{['queued', 'running', 'cancelling'].includes(job.state) && <button type="button" className="link-button" onClick={() => cancel(job.id)}>取消</button>}{job.run_id && <button type="button" className="link-button" onClick={() => open(job.run_id!)}>查看结果</button>}</td></tr>)}</tbody></table></div>}
    {runs.length > 0 && <details><summary>历史板块资金热度 · {runs.length} 次</summary><div className="table-wrap"><table><thead><tr><th>日期范围</th><th>指数</th><th>领先板块</th><th>操作</th></tr></thead><tbody>{runs.map(row => <tr key={row.id}><td>{row.date_from} 至 {row.date_to}</td><td>{row.loaded_index_count}</td><td>{row.leader_count}</td><td><button type="button" className="link-button" onClick={() => open(row.id)}>查看</button></td></tr>)}</tbody></table></div></details>}
    {selected?.result && <><h4>结果 · {selected.id.slice(0, 12)}</h4><p className="muted">加载 {selected.loaded_index_count} 个指数、{selected.result.loaded_sector_count} 个板块；领先板块 {selected.leader_count} 个。质量：{selected.quality_flags.join('、') || '—'}；缺失指数 {selected.result.missing_index_codes?.length || 0} 个。</p><div className="table-wrap"><table><thead><tr><th>板块</th><th>资金领先天数</th><th>最高区间涨幅</th><th>平均资金热度</th><th>领先区间</th><th>查看</th></tr></thead><tbody>{selected.result.leaders.map(row => <tr key={row.sector}><td>{row.sector}</td><td>{row.leader_days}</td><td>{row.max_return_pct.toFixed(2)}%</td><td>{row.avg_flow_score.toFixed(2)}%</td><td>{row.first_leader_date} 至 {row.last_leader_date}</td><td><button type="button" className="link-button" onClick={() => setFocusSector(row.sector)}>查看曲线</button></td></tr>)}</tbody></table></div><div className="form-grid"><label className="field"><span>曲线板块</span><select value={sector?.sector || ''} onChange={event => setFocusSector(event.target.value)}>{leaderSeries.map(row => <option key={row.sector} value={row.sector}>{row.sector}</option>)}</select></label><label className="field"><span>曲线指标</span><select value={chartMode} onChange={event => setChartMode(event.target.value as 'flow' | 'return')}><option value="flow">资金热度</option><option value="return">区间涨幅</option></select></label></div>{sector && <SectorCurve series={sector} mode={chartMode} />}<div className="form-grid"><label className="field"><span>明细日期</span><select value={day} onChange={event => setFocusDate(event.target.value)}>{selected.result.dates.map(value => <option key={value} value={value}>{value}</option>)}</select></label><label className="field"><span>明细范围</span><select value={tableFilter} onChange={event => setTableFilter(event.target.value as 'all' | 'top_flow' | 'top_return')}><option value="top_flow">资金热度 Top N</option><option value="top_return">涨幅 Top N</option><option value="all">全部板块</option></select></label></div><div className="table-wrap"><table><thead><tr><th>板块</th><th>资金热度排名</th><th>涨幅排名</th><th>资金热度</th><th>涨幅</th><th>成交额（元）</th><th>较前日成交额（元）</th></tr></thead><tbody>{rows.map(row => <tr key={`${row.date}:${row.sector}`}><td>{row.sector}</td><td>{row.rank_flow}</td><td>{row.rank_return}</td><td>{row.flow_score.toFixed(2)}%</td><td>{row.return_pct.toFixed(2)}%</td><td>{row.amount.toLocaleString()}</td><td>{row.amount_delta.toLocaleString()}</td></tr>)}</tbody></table></div>{rows.length === 0 && <p className="muted">当前日期与过滤条件下没有板块数据。</p>}</>}
  </section>
}

