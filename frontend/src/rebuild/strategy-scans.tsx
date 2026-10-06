import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from './api'
import { ScanJobs, type ScanJob } from './scan-jobs'
import { useTaskDeepLink } from './task-deep-link'
import type { EventProfileCatalog, EventProfileSnapshot } from './event-profiles'
import { Icon } from './workspace-icons'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
type ParamSchema = { type: 'number' | 'integer' | 'boolean' | 'enum'; title?: string; minimum?: number; maximum?: number; options?: Array<string | number>; enum?: Array<string | number> }
type Strategy = { enabled_in_rebuild?: boolean; id: string; name: string; signal_params: Record<string, string> | null; params_schema: Record<string, ParamSchema>; limitations?: string[] }
type ProfileBinding = { profile_id: string; revision: number; sha256: string; snapshot: EventProfileSnapshot }
type ScanRequest = { dataset_ids: string[]; strategies: Array<{ strategy_id: string; params: Record<string, string | boolean | number> }>; mode: 'single_date' | 'date_range'; as_of_date: string | null; date_from: string | null; date_to: string | null; strict: boolean; event_profile: ProfileBinding | null }
type ScanEvidence = { run_id: string; dataset_id: string; symbol: string; imported_symbol: string; strategy_id: string; decision_date: string; source_date: string | null; status: string; signal: boolean; run_signal: boolean | null; fresh_source: boolean; draft_eligible: boolean; primary_signal: string | null; events: string[]; risk_events: string[]; local_score: number | null; score_kind: string | null; quality_flags: string[] }
type ScanGroup = { symbol: string; decision_date: string; matched_strategy_count: number; strategy_ids: string[]; run_ids: string[] }
type Appearance = { symbol: string; strategies: Array<{ strategy_id: string; evaluation_count: number; signal_count: number; signal_dates: string[]; run_ids: string[]; scores: Array<{ date: string; value: number | null; kind: string | null }> }> }
type Scan = { id: string; created_at: string; mode: 'single_date' | 'date_range'; as_of_date: string | null; date_from: string | null; date_to: string | null; dataset_count: number; strategy_count: number; evaluation_count: number; signal_count: number; union_count: number; intersection_count: number; request?: ScanRequest; result?: { rows: ScanEvidence[]; union: ScanGroup[]; intersection: ScanGroup[]; per_symbol: Appearance[]; notes: string[]; intersection_supported: boolean } }
type View = 'union' | 'intersection' | 'appearance' | 'evidence'
const profileStrategies = new Set(['wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'])
const qualityNames: Record<string, string> = { SOURCE_DATE_BEFORE_SCAN_DATE: '源行情早于扫描日，本日不计为触发', historical_availability_unknown: '历史可得时间未知', sector_context_unavailable: '缺少板块上下文', candidate_universe_insufficient_data: '入池历史不足', candidate_universe_rejected: '未通过入池筛选', candidate_universe_filter_not_run: '尚未核对入池条件', indicator_warmup_short: '指标预热不足', historical_elasticity_short_window: '历史弹性窗口不足', st_status_unknown: 'ST 状态未知', ma5_volume_unavailable: '五日均量不足' }
const viewNames: Record<View, string> = { union: '信号并集', intersection: '同日交集', appearance: '逐股出现次数', evidence: '全部判断证据' }
const PAGE_SIZE = 50
const today = () => new Date().toLocaleDateString('sv-SE')
const sevenDaysAgo = () => { const day = new Date(); day.setDate(day.getDate() - 7); return day.toLocaleDateString('sv-SE') }
const dayCount = (from: string, to: string) => Math.floor((Date.parse(to + 'T00:00:00Z') - Date.parse(from + 'T00:00:00Z')) / 86_400_000) + 1
const dateLabel = (run: Scan) => run.mode === 'single_date' ? run.as_of_date : `${run.date_from} 至 ${run.date_to}`

function stockKey(symbol: string) {
  const match = /^(?:sh|sz|bj)?([0-9]{6})(?:\.(?:sh|sz|bj))?$/i.exec(symbol.trim())
  return match?.[1] || symbol.trim().toLowerCase()
}

function Parameters({ strategy, values, disabled, onChange }: { strategy: Strategy; values: Record<string, string>; disabled: boolean; onChange: (key: string, value: string) => void }) {
  return <details><summary>{strategy.name}参数 · {Object.keys(values).length} 项</summary>
    {strategy.limitations?.length ? <p className="muted">{strategy.limitations.join('；')}</p> : null}
    <div className="form-grid">{Object.entries(values).map(([key, value]) => {
      const schema = strategy.params_schema[key]
      const choices = schema?.options || schema?.enum || []
      return <label className="field" key={key}><span>{schema?.title || key}</span>
        {schema?.type === 'boolean' ? <select disabled={disabled} value={value} onChange={event => onChange(key, event.target.value)}><option value="true">启用</option><option value="false">关闭</option></select> :
          schema?.type === 'enum' ? <select disabled={disabled} value={value} onChange={event => onChange(key, event.target.value)}>{choices.map(choice => <option key={String(choice)} value={String(choice)}>{String(choice)}</option>)}</select> :
            <input disabled={disabled} type="number" min={schema?.minimum} max={schema?.maximum} step={schema?.type === 'integer' ? 1 : 'any'} value={value} onChange={event => onChange(key, event.target.value)} />}
      </label>
    })}</div>
  </details>
}

export function StrategyScanEditor({ datasets, strategies, onOpenMarket, onPromoted }: { datasets: Dataset[]; strategies: Strategy[]; onOpenMarket: (datasetId: string) => void; onPromoted: (runId: string) => void }) {
  const supported = useMemo(() => strategies.filter(item => item.signal_params !== null && item.enabled_in_rebuild !== false), [strategies])
  const [selectedDatasets, setSelectedDatasets] = useState<string[]>([])
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([])
  const [params, setParams] = useState<Record<string, Record<string, string>>>({})
  const [mode, setMode] = useState<'single_date' | 'date_range'>('single_date')
  const [asOf, setAsOf] = useState(today)
  const [dateFrom, setDateFrom] = useState(sevenDaysAgo)
  const [dateTo, setDateTo] = useState(today)
  const [strict, setStrict] = useState(true)
  const [profiles, setProfiles] = useState<EventProfileCatalog | null>(null)
  const [profileId, setProfileId] = useState('')
  const [profileRevision, setProfileRevision] = useState<number | null>(null)
  const [runs, setRuns] = useState<Scan[]>([])
  const [focusJobId, setFocusJobId] = useState('')
  const [selected, setSelected] = useState<Scan | null>(null)
  const [view, setView] = useState<View>('union')
  const [filter, setFilter] = useState<'all' | 'hit' | 'stale'>('all')
  const [strategyFilter, setStrategyFilter] = useState('')
  const [page, setPage] = useState(0)
  const [datasetSearch, setDatasetSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [deleteId, setDeleteId] = useState<string | null>(null)
  useTaskDeepLink('scan', async id => { setFocusJobId(id) }, '[aria-label="多策略扫描"]', setError)

  useEffect(() => {
    let live = true
    Promise.allSettled([api<Scan[]>('/research/scans'), api<EventProfileCatalog>('/research/event-profiles')]).then(([history, catalog]) => {
      if (!live) return
      if (history.status === 'fulfilled') setRuns(history.value)
      if (catalog.status === 'fulfilled') {
        setProfiles(catalog.value)
        const active = catalog.value.profiles.find(item => item.profile_id === catalog.value.active_profile_id)
        if (active) { setProfileId(active.profile_id); setProfileRevision(active.revision) }
      }
      const failures = [history, catalog].filter(item => item.status === 'rejected').map(item => item.reason instanceof Error ? item.reason.message : '扫描配置读取失败')
      if (failures.length) setError(failures.join('；'))
    })
    return () => { live = false }
  }, [])

  const usesProfile = selectedStrategies.some(id => profileStrategies.has(id))
  const selectedProfile = profiles?.profiles.find(item => item.profile_id === profileId)
  const profileConflict = usesProfile && (!selectedProfile || profileRevision !== selectedProfile.revision)
  const missingData = selectedDatasets.filter(id => !datasets.some(item => item.id === id))
  const missingStrategies = selectedStrategies.filter(id => !supported.some(item => item.id === id))
  const rangeDays = dayCount(dateFrom, dateTo)
  const estimate = mode === 'single_date' ? selectedDatasets.length * selectedStrategies.length : selectedDatasets.reduce((count, id) => {
    const dataset = datasets.find(item => item.id === id)
    if (!dataset) return count
    const from = dateFrom > dataset.first_date ? dateFrom : dataset.first_date
    const to = dateTo < dataset.last_date ? dateTo : dataset.last_date
    return count + Math.max(0, dayCount(from, to) || 0)
  }, 0) * selectedStrategies.length
  const exactOverLimit = mode === 'single_date' && estimate > 100
  const invalidDates = mode === 'single_date' ? !asOf : !Number.isFinite(rangeDays) || rangeDays < 1 || rangeDays > 90
  const canRun = selectedDatasets.length > 0 && selectedStrategies.length > 0 && !missingData.length && !missingStrategies.length && !profileConflict && !exactOverLimit && !invalidDates
  const name = (id: string) => strategies.find(item => item.id === id)?.name || id
  const visibleDatasets = datasets.filter(item => `${item.symbol} ${item.id}`.toLowerCase().includes(datasetSearch.trim().toLowerCase()))
  const evidence = (selected?.result?.rows || []).filter(item => (!strategyFilter || item.strategy_id === strategyFilter) && (filter === 'all' || (filter === 'hit' ? item.signal : !item.fresh_source && item.source_date !== null)))
  const groups = view === 'intersection' ? selected?.result?.intersection || [] : selected?.result?.union || []
  const appearances = selected?.result?.per_symbol || []
  const rowCount = view === 'evidence' ? evidence.length : view === 'appearance' ? appearances.length : groups.length
  const pages = Math.max(1, Math.ceil(rowCount / PAGE_SIZE))
  const pageIndex = Math.min(page, pages - 1)
  const start = pageIndex * PAGE_SIZE

  function toggleDataset(dataset: Dataset) {
    setNotice('')
    if (selectedDatasets.includes(dataset.id)) { setSelectedDatasets(current => current.filter(id => id !== dataset.id)); return }
    const sameStock = datasets.find(item => selectedDatasets.includes(item.id) && stockKey(item.symbol) === stockKey(dataset.symbol))
    if (sameStock) { setError(`${dataset.symbol} 已选择一个冻结样本，请先取消该证券的另一份样本。`); return }
    setSelectedDatasets(current => [...current, dataset.id]); setError('')
  }

  function toggleStrategy(strategy: Strategy) {
    setNotice('')
    setSelectedStrategies(current => current.includes(strategy.id) ? current.filter(id => id !== strategy.id) : [...current, strategy.id])
    setParams(current => ({ ...current, [strategy.id]: current[strategy.id] || { ...strategy.signal_params } }))
  }

  async function refresh() {
    setBusy(true); setError('')
    try {
      const [nextRuns, nextProfiles] = await Promise.all([api<Scan[]>('/research/scans'), api<EventProfileCatalog>('/research/event-profiles')])
      setRuns(nextRuns); setProfiles(nextProfiles)
    } catch (err) { setError(err instanceof Error ? err.message : '读取扫描记录失败') }
    finally { setBusy(false) }
  }

  async function run(event: FormEvent) {
    event.preventDefault()
    if (!canRun || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      for (const id of selectedStrategies) {
        for (const [key, value] of Object.entries(params[id] || {})) {
          if (!value.trim()) throw new Error(`${name(id)}：${key} 需要填写数值或选项`)
        }
      }
      const result = await api<ScanJob>('/research/scan-jobs', 'POST', { dataset_ids: selectedDatasets,
        strategies: selectedStrategies.map(id => ({ strategy_id: id, params: params[id] || {} })), strict,
        ...(mode === 'single_date' ? { as_of_date: asOf } : { date_from: dateFrom, date_to: dateTo }),
        ...(usesProfile ? { event_profile_id: profileId, event_profile_revision: profileRevision } : {}) })
      setFocusJobId(result.id); setPage(0); setView('union'); setFilter('all'); setStrategyFilter(''); setDeleteId(null)
      setNotice(`扫描任务已提交：共 ${result.total_count} 次判断，可在下方查看进度。`)
    } catch (err) { setError(err instanceof Error ? err.message : '策略扫描失败') }
    finally { setBusy(false) }
  }

  async function open(id: string, copy = false) {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<Scan>(`/research/scans/${encodeURIComponent(id)}`)
      setSelected(result); setPage(0); setFilter('all'); setStrategyFilter(''); setDeleteId(null)
      setRuns(current => [result, ...current.filter(item => item.id !== result.id)])
      if (copy && result.request) {
        const request = result.request
        setSelectedDatasets([...request.dataset_ids]); setSelectedStrategies(request.strategies.map(item => item.strategy_id))
        const copied: Record<string, Record<string, string>> = {}
        for (const item of request.strategies) {
          const available = supported.find(strategy => strategy.id === item.strategy_id)
          const keys = Object.keys(available?.signal_params || item.params)
          copied[item.strategy_id] = Object.fromEntries(keys.map(key => [key, String(item.params[key] ?? available?.signal_params?.[key] ?? '')]))
        }
        setParams(copied); setMode(request.mode); setStrict(request.strict)
        if (request.as_of_date) setAsOf(request.as_of_date)
        if (request.date_from) setDateFrom(request.date_from)
        if (request.date_to) setDateTo(request.date_to)
        if (request.event_profile) { setProfileId(request.event_profile.profile_id); setProfileRevision(request.event_profile.revision) }
        setNotice('已复制历史配置。运行前请核对样本、当前策略参数和事件模板版本。')
      }
    } catch (err) { setError(err instanceof Error ? err.message : '扫描记录读取失败') }
    finally { setBusy(false) }
  }

  async function remove(id: string) {
    setBusy(true); setError(''); setNotice('')
    try {
      const outcome = await api<{ preserved_research_run_count: number }>(`/research/scans/${encodeURIComponent(id)}`, 'DELETE')
      setRuns(current => current.filter(item => item.id !== id)); if (selected?.id === id) setSelected(null)
      setDeleteId(null); setNotice(`扫描记录已删除，${outcome.preserved_research_run_count} 条研究证据仍保留。`)
    } catch (err) { setError(err instanceof Error ? err.message : '删除扫描记录失败') }
    finally { setBusy(false) }
  }

  function groupRow(group: ScanGroup) {
    const source = selected?.result?.rows.find(item => item.symbol === group.symbol && item.decision_date === group.decision_date)
    return <tr key={`${group.symbol}:${group.decision_date}`}><td>{group.symbol}</td><td>{group.decision_date}</td><td>{group.matched_strategy_count} / {selected?.strategy_count}</td><td style={{ whiteSpace: 'normal' }}>{group.strategy_ids.map(id => {
      const child = selected?.result?.rows.find(item => item.symbol === group.symbol && item.decision_date === group.decision_date && item.strategy_id === id)
      return <button type="button" className="link-button" key={id} onClick={() => child && onPromoted(child.run_id)}>{name(id)}</button>
    })}</td><td>{source && <button type="button" className="link-button" onClick={() => onOpenMarket(source.dataset_id)}>冻结 K 线</button>}</td></tr>
  }

  return <section className="card span-all" aria-label="多策略扫描">
    <h3>多策略扫描与信号交集</h3><p className="muted">在选定冻结样本上比较各策略的同日信号。交集表示同一证券在同一扫描日被所有已选策略触发；各策略评分只用于理解其自身结果。</p>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    <form className="form" noValidate onSubmit={run}>
      <details open><summary>冻结行情样本 · 已选 {selectedDatasets.length} / 100</summary>
        <div className="form-grid" style={{ margin: '12px 0' }}><label className="field"><span>搜索证券或样本</span><input type="search" value={datasetSearch} onChange={event => setDatasetSearch(event.target.value)} placeholder="证券代码或样本 ID" /></label><div className="toolbar"><button className="button secondary" type="button" disabled={busy || !selectedDatasets.length} onClick={() => setSelectedDatasets([])}>清空样本选择</button></div></div>
        <div className="table-wrap" style={{ maxHeight: 260, overflowY: 'auto' }}><table><thead><tr><th>选择</th><th>证券</th><th>日期范围</th><th>历史可得时间</th></tr></thead><tbody>{visibleDatasets.map(item => <tr key={item.id}><td><input type="checkbox" aria-label={`选择行情 ${item.symbol} ${item.id.slice(0, 8)}`} checked={selectedDatasets.includes(item.id)} disabled={busy || (!selectedDatasets.includes(item.id) && selectedDatasets.length >= 100)} onChange={() => toggleDataset(item)} /></td><td>{item.symbol}<small className="muted"> · {item.id.slice(0, 8)}</small></td><td>{item.first_date} 至 {item.last_date}</td><td>{item.availability_quality === 'provided_availability' ? '已提供' : '未知'}</td></tr>)}</tbody></table></div>
        {!visibleDatasets.length && <p className="muted">没有匹配的冻结样本，请先导入行情。</p>}{missingData.length > 0 && <p className="danger">历史配置中有 {missingData.length} 个样本当前不可用，请重新选择。</p>}
      </details>
      <details open><summary>比较策略 · 已选 {selectedStrategies.length} / 10</summary>
        <div className="form-grid" style={{ marginTop: 12 }}>{supported.map(strategy => <label className="check-field" key={strategy.id}><input type="checkbox" checked={selectedStrategies.includes(strategy.id)} disabled={busy || (!selectedStrategies.includes(strategy.id) && selectedStrategies.length >= 10)} onChange={() => toggleStrategy(strategy)} />{strategy.name}</label>)}</div>
        <p className="muted">选择 2 至 10 个策略进行交集比较，也可只扫描一个策略。</p>
        {selectedStrategies.map(id => { const strategy = supported.find(item => item.id === id); return strategy && <Parameters key={id} strategy={strategy} values={params[id] || { ...strategy.signal_params }} disabled={busy} onChange={(key, value) => setParams(current => ({ ...current, [id]: { ...current[id], [key]: value } }))} /> })}
        {missingStrategies.length > 0 && <p className="danger">历史配置中的策略当前不可运行：{missingStrategies.map(name).join('、')}。<button type="button" className="link-button" onClick={() => setSelectedStrategies(current => current.filter(id => !missingStrategies.includes(id)))}>移除不可用策略</button></p>}
      </details>
      <div className="form-grid"><label className="field"><span>扫描方式</span><select disabled={busy} value={mode} onChange={event => setMode(event.target.value as typeof mode)}><option value="single_date">指定日期</option><option value="date_range">日期区间逐日扫描</option></select></label>
        {mode === 'single_date' ? <label className="field"><span>扫描日期</span><input disabled={busy} type="date" value={asOf} onChange={event => setAsOf(event.target.value)} /></label> : <><label className="field"><span>起始日期</span><input disabled={busy} type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label className="field"><span>结束日期</span><input disabled={busy} type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label></>}
      </div>
      <label className="check-field"><input disabled={busy} type="checkbox" checked={strict} onChange={event => setStrict(event.target.checked)} />严格可得时间</label><p className="muted">以扫描日上海时间 23:59:59 为截止点。严格模式排除历史可得时间未知的 K 线；旧源日期的信号保留证据，本日不计触发。</p>
      {usesProfile && <div className="form-grid"><label className="field"><span>事件判定模板</span><select disabled={busy} value={profileId} onChange={event => { const profile = profiles?.profiles.find(item => item.profile_id === event.target.value); setProfileId(event.target.value); setProfileRevision(profile?.revision ?? null) }}>{!selectedProfile && <option value={profileId}>{profileId ? '历史模板当前不可用，请重新选择' : '请选择模板'}</option>}{profiles?.profiles.map(item => <option key={item.profile_id} value={item.profile_id}>{item.name} · 版本 {item.revision}</option>)}</select></label><div><p>将冻结模板版本：{profileRevision ?? '未选择'}</p>{profileConflict && <p className="danger">模板版本需要核对。{selectedProfile && <button type="button" className="link-button" disabled={busy} onClick={() => setProfileRevision(selectedProfile.revision)}>采用当前版本 {selectedProfile.revision}</button>}</p>}</div></div>}
      <p className={exactOverLimit || invalidDates ? 'danger' : 'muted'}>{mode === 'single_date' ? `预计 ${estimate} 次判断，上限 100 次。` : `按样本覆盖的自然日保守估计最多 ${estimate} 次判断；服务端按实际样本日期计算，上限 1000 次。区间最多 90 个自然日。`}{mode === 'date_range' && estimate > 1000 ? '建议减少证券、策略或区间长度。' : ''}</p>
      <div className="toolbar" style={{ flexWrap: 'wrap' }}><button className="button primary" disabled={busy || !canRun}>{busy ? '处理中…' : '运行策略扫描'}</button><button type="button" className="button secondary" disabled={busy} onClick={refresh}><Icon name="refresh" />刷新历史与模板</button></div>
    </form>
    <ScanJobs focusId={focusJobId} onOpen={id => { void open(id) }} />
    <details style={{ marginTop: 20 }}><summary>历史扫描 · {runs.length} 次</summary><div className="table-wrap"><table><thead><tr><th>时间</th><th>日期</th><th>样本 / 策略</th><th>判断 / 触发</th><th>操作</th></tr></thead><tbody>{runs.map(item => <tr key={item.id}><td>{new Date(item.created_at).toLocaleString()}</td><td>{dateLabel(item)}</td><td>{item.dataset_count} / {item.strategy_count}</td><td>{item.evaluation_count} / {item.signal_count}</td><td><button type="button" className="link-button" disabled={busy} onClick={() => open(item.id)}>查看</button><button type="button" className="link-button" disabled={busy} onClick={() => { if (window.confirm('将用这次扫描的配置替换当前表单，确认复制？')) open(item.id, true) }}>复制配置</button><button type="button" className="link-button danger" disabled={busy} onClick={() => setDeleteId(item.id)}>删除</button></td></tr>)}</tbody></table></div>{!runs.length && <p className="muted">暂无扫描记录。</p>}</details>
    {deleteId && <div className="alert error" role="alert" style={{ marginTop: 12 }}><p>确认删除这次扫描记录？关联的研究证据将继续保留。</p><div className="toolbar"><button className="button secondary danger" type="button" disabled={busy} onClick={() => remove(deleteId)}><Icon name="check" />确认删除扫描</button><button className="button secondary" type="button" disabled={busy} onClick={() => setDeleteId(null)}><Icon name="close" />取消删除</button></div></div>}
    {selected?.result && <div style={{ marginTop: 20 }}><h4>扫描结果 · {dateLabel(selected)}</h4><div className="period-summary"><span>判断 {selected.evaluation_count} 次</span><span>信号触发 {selected.signal_count} 次</span><span>并集 {selected.union_count} 个证券日</span><span>交集 {selected.intersection_count} 个证券日</span><span>严格模式：{selected.request?.strict ? '是' : '否'}</span></div>
      {selected.request?.event_profile && <p className="muted">事件模板：{selected.request.event_profile.snapshot.name} · 冻结版本 {selected.request.event_profile.revision}</p>}
      <p className="muted">{selected.result.notes.join('；')}</p>
      <div className="form-grid"><label className="field"><span>结果视图</span><select value={view} onChange={event => { setView(event.target.value as View); setPage(0) }}>{Object.entries(viewNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>{view === 'evidence' && <><label className="field"><span>证据范围</span><select value={filter} onChange={event => { setFilter(event.target.value as typeof filter); setPage(0) }}><option value="all">全部判断</option><option value="hit">同日触发</option><option value="stale">源行情早于扫描日</option></select></label><label className="field"><span>策略筛选</span><select value={strategyFilter} onChange={event => { setStrategyFilter(event.target.value); setPage(0) }}><option value="">全部策略</option>{selected.request?.strategies.map(item => <option key={item.strategy_id} value={item.strategy_id}>{name(item.strategy_id)}</option>)}</select></label></>}</div>
      {view === 'intersection' && !selected.result.intersection_supported && <p className="muted">本次只选择了一个策略，交集比较需要至少两个策略。</p>}
      <div className="table-wrap" style={{ marginTop: 12 }}>
        {(view === 'union' || view === 'intersection') && <table><thead><tr><th>证券</th><th>扫描日</th><th>命中策略数</th><th>策略与研究证据</th><th>行情</th></tr></thead><tbody>{groups.slice(start, start + PAGE_SIZE).map(groupRow)}</tbody></table>}
        {view === 'appearance' && <table><thead><tr><th>证券</th><th>各策略出现次数 / 判断次数</th><th>行情</th></tr></thead><tbody>{appearances.slice(start, start + PAGE_SIZE).map(item => { const source = selected.result?.rows.find(row => row.symbol === item.symbol); return <tr key={item.symbol}><td>{item.symbol}</td><td style={{ whiteSpace: 'normal' }}>{item.strategies.map(strategy => <details key={strategy.strategy_id}><summary>{name(strategy.strategy_id)}：{strategy.signal_count} / {strategy.evaluation_count} 次</summary>{strategy.signal_dates.length ? strategy.signal_dates.map((day, index) => <button type="button" className="link-button" key={day} onClick={() => onPromoted(strategy.run_ids[index])}>{day}</button>) : <p className="muted">无同日触发</p>}</details>)}</td><td>{source && <button type="button" className="link-button" onClick={() => onOpenMarket(source.dataset_id)}>冻结 K 线</button>}</td></tr> })}</tbody></table>}
        {view === 'evidence' && <table><thead><tr><th>证券 / 策略</th><th>扫描日 / 源日期</th><th>状态</th><th>策略自身分数</th><th>数据说明</th><th>证据</th></tr></thead><tbody>{evidence.slice(start, start + PAGE_SIZE).map(item => <tr key={`${item.run_id}:${item.decision_date}`}><td>{item.symbol}<br />{name(item.strategy_id)}</td><td>{item.decision_date}<br /><small className="muted">源：{item.source_date || '无'}</small></td><td>{item.signal ? '同日触发' : !item.fresh_source && item.source_date ? '旧源日期，不计触发' : item.status === 'insufficient_data' ? '数据不足' : '未触发'}{item.primary_signal && <small> · {item.primary_signal}</small>}</td><td>{item.local_score ?? '—'}</td><td style={{ whiteSpace: 'normal', minWidth: 180 }}>{item.quality_flags.map(flag => qualityNames[flag] || flag).join('；') || '—'}{item.risk_events.length > 0 && <div className="danger">风险事件：{item.risk_events.join('、')}</div>}</td><td><button type="button" className="link-button" onClick={() => onPromoted(item.run_id)}>研究证据</button><button type="button" className="link-button" onClick={() => onOpenMarket(item.dataset_id)}>冻结 K 线</button></td></tr>)}</tbody></table>}
      </div>
      {!rowCount && <p className="muted">当前视图没有结果。</p>}
      {rowCount > PAGE_SIZE && <div className="toolbar" style={{ marginTop: 12 }}><button type="button" className="button secondary" disabled={pageIndex === 0} onClick={() => setPage(pageIndex - 1)}>上一页</button><span>{pageIndex + 1} / {pages} 页 · {rowCount} 项</span><button type="button" className="button secondary" disabled={pageIndex + 1 >= pages} onClick={() => setPage(pageIndex + 1)}>下一页</button></div>}
    </div>}
  </section>
}
