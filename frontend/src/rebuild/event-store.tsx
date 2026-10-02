import { useEffect, useState } from 'react'
import { api } from './api'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
type Profile = { profile_id: string; name: string; revision: number }
type Version = { window_days: number; strict: boolean; event_profile: { profile_id: string; revision: number; sha256: string; snapshot: { name: string } }; code_sha256: string }
type Stats = { record_count: number; stored_bytes: number; algorithm_version: string; backfill_cache_hits: number; backfill_writes: number; backfill_hit_rate: number | null; method: string; versions: Array<{ id: string; window_days: number; strict: boolean; profile_id: string; profile_revision: number; current_code: boolean; complete_count: number; insufficient_count: number; empty_event_count: number }> }
type Preview = { input_sha256: string; total_count: number; cached_count: number; missing_count: number; start_date: string; end_date: string; versions: Record<string, Version>; warnings: Array<{ dataset_id: string; code: string }> }
type Job = { id: string; state: string; total_count: number; completed_count: number; cache_hits: number; written_count: number; elapsed_ms: number; result_bytes: number; error: string | null; capabilities: { cancel: boolean; resume: boolean }; request?: { start_date: string; end_date: string; versions: Record<string, Version> } }
type RecordRow = { id: string; version_id: string; dataset_id: string; symbol: string; decision_date: string; decision_at: string; source_date: string | null; status: string; event_count: number; risk_count: number; primary_event: string; observed_bars: number; created_at: string; content_sha256: string }
type Detail = RecordRow & { current_code: boolean; version: Version; result: { quality_flags: string[]; has_data: boolean; required_bars: number; source_path: string; event_age_days: Record<string, number>; snapshot: { events: string[]; risk_events: string[]; phase: string; signal: string; event_score: number; risk_score: number; confirmation_status: string; event_chain: Array<{ event: string; date: string; category: string }>; event_confirmation_map: Record<string, string> } } }
const path = '/research/event-store'
const stateText: Record<string, string> = { queued: '等待计算', running: '正在回填', cancelling: '正在取消', cancelled: '已取消', failed: '失败', succeeded: '完成' }
const warnings: Record<string, string> = { no_observed_dates_in_range: '样本在区间内无记录', dataset_ends_before_range: '样本结束早于区间末日', stale_source_date: '决策日只能看到此前行情', historical_availability_unknown: '历史可得时间未知' }

export function EventStoreWorkspace({ onOpenMarket }: { onOpenMarket?: (id: string) => void }) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profileId, setProfileId] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [windows, setWindows] = useState('60')
  const [strict, setStrict] = useState(true)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const jobId = job?.id
  const completedCount = job?.completed_count
  const [records, setRecords] = useState<RecordRow[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    Promise.all([api<Dataset[]>('/market/datasets'), api<{ profiles: Profile[]; active_profile_id: string }>('/research/event-profiles'), api<Stats>(path + '/stats'), api<Job[]>(path + '/jobs')])
      .then(([items, catalog, totals, history]) => {
        if (active) { setDatasets(items); setProfiles(catalog.profiles); setProfileId(catalog.active_profile_id); setStats(totals); setJobs(history) }
      }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [])
  useEffect(() => { setPreview(null) }, [selected, start, end, windows, strict, profileId])
  useEffect(() => {
    let active = true
    setRecords([]); setDetail(null)
    api<RecordRow[]>(`${path}/records?limit=100&offset=${offset}${jobId ? `&job_id=${jobId}` : ''}`)
      .then(rows => { if (active) setRecords(rows) }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [jobId, completedCount, offset])
  useEffect(() => {
    if (!jobs.some(row => ['queued', 'running', 'cancelling'].includes(row.state))) return
    let active = true
    const timer = window.setInterval(() => {
      Promise.all([api<Job[]>(path + '/jobs'), api<Stats>(path + '/stats')]).then(([history, totals]) => {
        if (!active) return
        setJobs(history); setStats(totals)
        if (jobId) {
          const updated = history.find(item => item.id === jobId)
          if (updated) setJob(current => current?.id === jobId ? { ...current, ...updated } : current)
        }
      }).catch(err => { if (active) setError(err.message) })
    }, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [jobs, jobId])
  function pick(ids: string[]) {
    setSelected(ids)
    if (!selected.length && ids.length) {
      const first = datasets.find(item => item.id === ids[0])
      if (first) { setEnd(first.last_date); const from = new Date(first.last_date + 'T00:00:00Z'); from.setUTCDate(from.getUTCDate() - 29); setStart(from.toISOString().slice(0, 10)) }
    }
  }
  function input() {
    const profile = profiles.find(item => item.profile_id === profileId)
    if (!profile) throw new Error('请选择事件模板')
    const values = windows.split(/[,，\s]+/).filter(Boolean).map(Number)
    if (!values.length || values.length > 3 || values.some(value => !Number.isInteger(value) || value < 20 || value > 240)) throw new Error('窗口为 20 至 240 的整数，最多 3 个')
    return { dataset_ids: selected, start_date: start, end_date: end, window_days: values, strict,
      event_profile_id: profile.profile_id, event_profile_revision: profile.revision }
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  async function refresh() {
    const [totals, history] = await Promise.all([api<Stats>(path + '/stats'), api<Job[]>(path + '/jobs')])
    setStats(totals); setJobs(history)
  }
  async function openJob(id: string) { setJob(await api<Job>(path + '/jobs/' + id)); setOffset(0) }
  async function control(action: string) {
    if (!job) return
    setJob(await api<Job>(`${path}/jobs/${job.id}/${action}`, 'POST', {})); await refresh()
  }
  return <section className="card"><div className="section-heading"><div><h2>维科夫事件仓</h2><p>按明确的数据、模板和算法版本保存每日事件。查询与查看历史不会触发计算。</p></div><button className="button secondary" disabled={busy} onClick={() => run(refresh)}>刷新统计</button></div>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <p role="status">{notice}</p>}
    {stats && <><div className="form-grid"><p>缓存快照 <strong>{stats.record_count}</strong></p><p>已存证据 {(stats.stored_bytes / 1024 / 1024).toFixed(2)} MiB</p><p>回填写入 {stats.backfill_writes} · 复用 {stats.backfill_cache_hits} · 命中率 {stats.backfill_hit_rate === null ? '暂无任务' : `${(stats.backfill_hit_rate * 100).toFixed(1)}%`}</p></div><p className="muted">{stats.method}</p></>}
    <h3>显式回填</h3><p className="muted">1—100 个不同证券样本；最多 3 个窗口；日期范围最多 366 天且总判断不超过 3000 次。每批 10 个快照，与回测共用受限 CPU；取消保留已完成快照，恢复后接续。数据和算法变化会另建版本。</p>
    <div className="form-grid"><label className="field">冻结行情样本（可多选）<select multiple size={6} value={selected} disabled={busy} onChange={event => pick([...event.target.selectedOptions].map(option => option.value))}>{datasets.map(item => <option key={item.id} value={item.id}>{item.symbol} · {item.first_date} 至 {item.last_date} · {item.id.slice(0, 8)}</option>)}</select></label><label className="field">事件模板<select value={profileId} disabled={busy} onChange={event => setProfileId(event.target.value)}>{profiles.map(item => <option key={item.profile_id} value={item.profile_id}>{item.name} · v{item.revision}</option>)}</select></label><label className="field">回填开始<input type="date" value={start} disabled={busy} onChange={event => setStart(event.target.value)} /></label><label className="field">回填结束<input type="date" value={end} disabled={busy} onChange={event => setEnd(event.target.value)} /></label><label className="field">观察窗口（逗号分隔）<input value={windows} disabled={busy} onChange={event => setWindows(event.target.value)} /></label><label><input type="checkbox" checked={strict} disabled={busy} onChange={event => setStrict(event.target.checked)} />严格可得时间</label></div>
    {!strict && <p className="alert">宽松模式允许可得时间未知的历史日 K；结果会标记该限制，不能据此证明当时已知。</p>}
    <button className="button secondary" disabled={busy || !selected.length || !start || !end} onClick={() => run(async () => setPreview(await api<Preview>(path + '/preview', 'POST', input())))}>预览版本与缺失快照</button>
    {preview && <div className="market-detail"><p>本次 {preview.total_count} 个判断 · 已缓存 {preview.cached_count} · 待计算 {preview.missing_count}。源区间 {preview.start_date} 至 {preview.end_date}。</p>{Object.entries(preview.versions).map(([id, value]) => <p className="muted" key={id}>窗口 {value.window_days} · 模板 {value.event_profile.snapshot.name} v{value.event_profile.revision} · 版本 {id.slice(0, 12)}</p>)}{preview.warnings.map((item, index) => <p className="alert" key={index}>{warnings[item.code] || item.code} · {item.dataset_id.slice(0, 12)}</p>)}<button className="button primary" disabled={busy} onClick={() => run(async () => {
      const saved = await api<Job>(path + '/jobs', 'POST', { ...input(), expected_preview_sha256: preview.input_sha256 })
      setJob(saved); setOffset(0); await refresh(); setNotice('回填任务已保存；相同冻结请求复用已有任务。')
    })}>确认创建回填任务</button></div>}
    <h3>回填历史</h3><div className="table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>进度</th><th>复用 / 写入</th><th>操作</th></tr></thead><tbody>{jobs.map(item => <tr key={item.id}><td>{item.id.slice(0, 10)}</td><td>{stateText[item.state] || item.state}</td><td>{item.completed_count} / {item.total_count}</td><td>{item.cache_hits} / {item.written_count}</td><td><button className="link-button" onClick={() => run(() => openJob(item.id))}>查看任务</button></td></tr>)}</tbody></table></div>
    {job && <div className="market-detail"><h3>当前回填：{stateText[job.state] || job.state}</h3><p>{job.completed_count} / {job.total_count} · {(job.elapsed_ms / 1000).toFixed(2)} 秒累计计算 · {job.error || '无任务错误'}</p><div className="form-actions">{job.capabilities.cancel && <button className="button secondary" disabled={busy} onClick={() => run(() => control('cancel'))}>取消回填</button>}{job.capabilities.resume && <button className="button secondary" disabled={busy} onClick={() => run(() => control('resume'))}>恢复未完成回填</button>}<button className="button secondary" onClick={() => { setJob(null); setOffset(0) }}>查看全部版本快照</button></div></div>}
    <h3>{job ? '当前任务已有快照' : '事件快照历史'}</h3><p className="muted">“样本不足”不会归类为“无事件”。数据文件异常会使回填失败，不生成空记录。事件日期早于决策日期不代表该事件在当日已确认。</p>
    <div className="table-wrap"><table><thead><tr><th>证券</th><th>决策日 / 可见来源日</th><th>状态</th><th>主事件</th><th>正向 / 风险事件</th><th>证据</th></tr></thead><tbody>{records.map(item => <tr key={item.id}><td>{item.symbol}</td><td>{item.decision_date} / {item.source_date || '无'}</td><td>{item.status === 'complete' ? '完整计算' : '样本不足'}</td><td>{item.primary_event || '无'}</td><td>{item.event_count} / {item.risk_count}</td><td><button className="link-button" onClick={() => run(async () => setDetail(await api<Detail>(path + '/records/' + item.id)))}>查看事件证据</button></td></tr>)}</tbody></table></div>
    <div className="form-actions"><button className="button secondary" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 100))}>上一页</button><span>第 {offset / 100 + 1} 页</span><button className="button secondary" disabled={records.length < 100} onClick={() => setOffset(value => value + 100)}>下一页</button></div>
    {detail && <div className="market-detail"><h3>事件证据 · {detail.symbol} · {detail.decision_date}</h3><p>可见样本 {detail.observed_bars} / 最少 {detail.result.required_bars} · 观察窗口 {detail.version.window_days} · {detail.current_code ? '当前算法' : '历史算法版本'}</p><p>决策时间 {detail.decision_at} · 来源 {detail.source_date || '无可见行情'} · 模板 {detail.version.event_profile.snapshot.name} v{detail.version.event_profile.revision}</p><p>阶段 {detail.result.snapshot.phase} · 事件分 {detail.result.snapshot.event_score} · 风险分 {detail.result.snapshot.risk_score} · 确认 {detail.result.snapshot.confirmation_status}</p>{detail.result.quality_flags.length > 0 && <p className="alert">{detail.result.quality_flags.map(flag => warnings[flag] || flag).join('；')}</p>}<div className="table-wrap"><table><thead><tr><th>事件</th><th>事件日期</th><th>截至决策日年龄（观察交易条数）</th><th>截至决策日确认状态</th></tr></thead><tbody>{detail.result.snapshot.event_chain.map((item, index) => <tr key={index}><td>{item.event}</td><td>{item.date}</td><td>{detail.result.event_age_days[item.event] ?? '—'}</td><td>{detail.result.snapshot.event_confirmation_map[item.event] || '—'}</td></tr>)}</tbody></table></div>{onOpenMarket && <button className="button secondary" onClick={() => onOpenMarket(detail.dataset_id)}>打开来源行情</button>}<details><summary>冻结版本与来源</summary><p style={{ overflowWrap: 'anywhere' }}>样本 {detail.dataset_id}<br />版本 {detail.version_id}<br />证据 {detail.content_sha256}<br />代码 {detail.version.code_sha256}<br />口径 {detail.result.source_path}</p></details></div>}
    {stats && <details><summary>按版本查看覆盖统计</summary><div className="table-wrap"><table><thead><tr><th>版本</th><th>窗口 / 模板修订</th><th>完整 / 不足</th><th>完整但无事件</th><th>算法</th></tr></thead><tbody>{stats.versions.map(item => <tr key={item.id}><td>{item.id.slice(0, 12)}</td><td>{item.window_days} / {item.profile_revision}</td><td>{item.complete_count} / {item.insufficient_count}</td><td>{item.empty_event_count}</td><td>{item.current_code ? '当前' : '历史'}</td></tr>)}</tbody></table></div></details>}
  </section>
}
