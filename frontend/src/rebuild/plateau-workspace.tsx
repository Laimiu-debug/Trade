import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { PlateauTableDownload } from './research-table-downloads'
import { useTaskDeepLink } from './task-deep-link'
import { Icon } from './workspace-icons'

type Axis = { key: string; values: string; low: string; high: string; precision: number }
type Spec = { type: string; minimum?: number; maximum?: number; enum?: string[] }
type Source = { id: string; strategy_id: string; state: string; created_at: string }
type Metrics = { total_return: number; max_drawdown: number; trade_count: number; win_rate: number | null; profit_factor: number | null; profit_factor_unbounded: boolean; entry_fill_rate: number; quality_flags: string[] }
type Point = { id: string; ordinal: number; point_sha256: string; state: string; axis_values: Record<string, string>; params: Record<string, string | boolean>; config: Record<string, unknown>; error: string | null; attempt_number: number; metrics: Metrics | null; result_sha256: string | null; result?: Record<string, unknown> | null }
type Scored = Point & { rank: number; point_score: number; plateau_score: number | null; local_score: number | null; neighbor_count: number; neighbor_ids: string[]; neighbor_pass_rate: number | null; neighbor_median_score: number | null; neighbor_p25_score: number | null; sensitivity_penalty: number | null; hard_filter_failures: string[] }
type Experiment = { id: string; name: string; strategy_id: string; state: string; counts: Record<string, number>; error: string | null; capabilities: Record<string, boolean>; elapsed_ms: number; total_bytes: number; created_at: string; result_sha256: string | null; plan: { seed: number; sampling_mode: string; actual_points: number; requested_points: number; invalid_points: number; axes: Record<string, unknown>; notes: string[] }; points?: Point[]; symbol?: string; frozen_context?: Record<string, unknown>; analysis?: { points: Scored[]; regions: Array<{ id: string; point_count: number; center_point_id: string; median_plateau_score: number; parameter_ranges: Record<string, string[]> }>; correlations: Array<{ parameter: string; total_return_pearson: number | null; sample_count: number }>; recommended_point_id: string | null; peak_point_id: string | null; notes: string[] } | null }
type Preview = { requestKey: string; preview_sha256: string; plan: Experiment['plan'] & { deduplicated_points: number; points: Array<{ ordinal: number; axis_values: Record<string, string>; error: string | null }> }; limitations: string[] }
const stateName: Record<string, string> = { queued: '排队', running: '计算中', pausing: '暂停中，等待当前点完成', paused: '已暂停', cancelling: '正在取消', cancelled: '已取消', succeeded: '已完成', failed: '失败', invalid: '参数组合无效' }
const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const score = (value: number | null | undefined) => value == null ? '证据不足' : value.toFixed(2)

function SliceHeatmap({ points, axisNames, onSelect }: { points: Point[]; axisNames: string[]; onSelect: (point: Point) => void }) {
  const [x, setX] = useState(axisNames[0] || '')
  const [y, setY] = useState(axisNames[1] || '')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const unique = (key: string) => [...new Set(points.map(point => point.axis_values[key]))].sort((a, b) => Number(a) - Number(b) || a.localeCompare(b))
  const fixed = axisNames.filter(key => key !== x && key !== y)
  const selected = points.filter(point => fixed.every(key => point.axis_values[key] === (filters[key] ?? unique(key)[0])))
  const xs = unique(x), ys = y ? unique(y) : ['单维']
  const cells = selected.filter(point => point.metrics)
  const max = Math.max(.0001, ...cells.map(point => Math.abs(point.metrics!.total_return)))
  return <section aria-label="参数收益热图"><h4>收益热图 · 固定其他维度</h4><div className="toolbar" style={{ flexWrap: 'wrap' }}>
    <label>横轴<select value={x} onChange={event => { setX(event.target.value); if (event.target.value === y) setY('') }}>{axisNames.map(key => <option key={key}>{key}</option>)}</select></label>
    <label>纵轴<select value={y} onChange={event => setY(event.target.value)}><option value="">单维</option>{axisNames.filter(key => key !== x).map(key => <option key={key}>{key}</option>)}</select></label>
    {fixed.map(key => <label key={key}>固定 {key}<select value={filters[key] ?? unique(key)[0]} onChange={event => setFilters({ ...filters, [key]: event.target.value })}>{unique(key).map(value => <option key={value}>{value}</option>)}</select></label>)}
  </div><p className="muted">每个圆点对应一次已完成评估，空白处没有样本；LHS 不插值。颜色与圆点大小表示收益绝对值，红色为盈利、绿色为亏损。点击查看参数点。</p>
    <div className="backtest-chart"><svg viewBox="0 0 800 290" role="img" aria-label="参数切片收益热图">
      <line x1="60" y1="245" x2="770" y2="245" stroke="currentColor" /><line x1="60" y1="20" x2="60" y2="245" stroke="currentColor" />
      {cells.map(point => { const indexX = xs.indexOf(point.axis_values[x]); const indexY = y ? ys.indexOf(point.axis_values[y]) : 0; return <circle key={point.id} cx={80 + indexX / Math.max(1, xs.length - 1) * 670} cy={225 - indexY / Math.max(1, ys.length - 1) * 185} r={Math.min(14, 5 + Math.abs(point.metrics!.total_return) / max * 9)} fill={point.metrics!.total_return >= 0 ? 'var(--market-up)' : 'var(--market-down)'} opacity=".85" role="button" tabIndex={0} aria-label={`参数点 ${point.ordinal + 1} 收益 ${pct(point.metrics!.total_return)}`} onClick={() => onSelect(point)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(point) } }}><title>{JSON.stringify(point.axis_values)} · 收益 {pct(point.metrics!.total_return)}</title></circle> })}
      <text x="70" y="270" fill="currentColor" fontSize="12">{x} · {xs[0]} → {xs.at(-1)}</text><text x="65" y="15" fill="currentColor" fontSize="12">{y || '单维'} · {ys[0]} → {ys.at(-1)}</text>
    </svg></div>{!cells.length && <p className="muted">当前固定切片没有已完成样本，请调整其他维度。</p>}
  </section>
}

export function PlateauWorkspace({ baseRunId }: { baseRunId?: string }) {
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState(baseRunId || '')
  const [schema, setSchema] = useState<Record<string, Spec>>({})
  const [mode, setMode] = useState<'grid' | 'lhs'>('grid')
  const [name, setName] = useState('单股参数实验')
  const [axes, setAxes] = useState<Axis[]>([{ key: 'config.holding_bars', values: '5,10,15', low: '2', high: '20', precision: 0 }])
  const [seed, setSeed] = useState('20260926')
  const [sampleCount, setSampleCount] = useState(24)
  const [maxPoints, setMaxPoints] = useState(120)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [runs, setRuns] = useState<Experiment[]>([])
  const [selected, setSelected] = useState<Experiment | null>(null)
  const [point, setPoint] = useState<Point | null>(null)
  const [presetName, setPresetName] = useState('')
  const [deleteId, setDeleteId] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const live = useRef(true)
  const selectedId = useRef('')
  const pointRequest = useRef(0)
  const active = runs.some(run => ['queued', 'running', 'pausing', 'cancelling'].includes(run.state))
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  useEffect(() => { if (baseRunId) setSourceId(baseRunId) }, [baseRunId])
  async function refresh() {
    const [history, backtests] = await Promise.all([api<Experiment[]>('/research/plateaus'), api<Source[]>('/backtests')])
    if (live.current) { setRuns(history); setSources(backtests.filter(run => run.state === 'succeeded')) }
  }
  useEffect(() => { refresh().catch(err => { if (live.current) setError(String(err.message || err)) }) }, [])
  useEffect(() => { let current = true; setSchema({}); if (sourceId) api<{ schema: Record<string, Spec> }>(`/research/plateaus/schema/${sourceId}`).then(value => { if (current) setSchema(value.schema) }).catch(err => { if (current) setError(err.message) }); return () => { current = false } }, [sourceId])
  useEffect(() => {
    if (!active) return
    let polling = false
    const timer = setInterval(async () => { if (polling) return; polling = true; try { await refresh(); const id = selectedId.current; if (id) { const detail = await api<Experiment>(`/research/plateaus/${id}`); if (live.current && selectedId.current === id) setSelected(detail) } } catch (err) { if (live.current) setError(err instanceof Error ? err.message : String(err)) } finally { polling = false } }, 2000)
    return () => clearInterval(timer)
  }, [active])
  const body = useMemo(() => ({ base_run_id: sourceId, sampling_mode: mode, seed: seed.trim() ? Number(seed) : null, sample_points: sampleCount, max_points: maxPoints,
    axes: Object.fromEntries(axes.map(axis => [axis.key, mode === 'grid' ? { values: axis.values.split(/[,，]/).map(item => item.trim()).filter(Boolean) } : { min: axis.low, max: axis.high, precision: axis.precision }])) }), [sourceId, mode, seed, sampleCount, maxPoints, axes])
  const requestKey = JSON.stringify(body)
  const activePreview = preview?.requestKey === requestKey ? preview : null
  async function perform(action: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await action() } catch (err) { if (live.current) setError(err instanceof Error ? err.message : String(err)) } finally { if (live.current) setBusy(false) } }
  async function choose(id: string) { selectedId.current = id; setPoint(null); setDeleteId(''); const result = await api<Experiment>(`/research/plateaus/${id}`); if (live.current && selectedId.current === id) setSelected(result) }
  useTaskDeepLink('plateau', choose, '[aria-label="收益平原详情"]', setError)
  async function choosePoint(candidate: Point) { const id = selectedId.current, token = ++pointRequest.current; const result = await api<Point>(`/research/plateaus/${id}/points/${candidate.id}`); if (live.current && selectedId.current === id && token === pointRequest.current) { setPoint(result); setPresetName(`平原候选 ${candidate.ordinal + 1}`) } }
  async function control(id: string, action: string) { await api(`/research/plateaus/${id}${action === 'delete' ? '' : '/' + action}`, action === 'delete' ? 'DELETE' : 'POST', {}); if (action === 'delete') { if (selectedId.current === id) { selectedId.current = ''; setSelected(null); setPoint(null) } setDeleteId('') } else await choose(id); await refresh() }
  function changeAxis(index: number, patch: Partial<Axis>) { setAxes(current => current.map((axis, at) => at === index ? { ...axis, ...patch } : axis)) }
  const scoreMap = new Map(selected?.analysis?.points.map(item => [item.id, item]) || [])
  const displayedPoints = [...(selected?.points || [])].sort((a, b) => (scoreMap.get(a.id)?.rank || 9999) - (scoreMap.get(b.id)?.rank || 9999) || a.ordinal - b.ordinal)
  const pointScore = point ? scoreMap.get(point.id) : null
  return <section className="card" aria-label="单股收益平原" style={{ marginTop: 20 }}>
    <div className="section-header"><div><h3>单股收益平原</h3><p className="muted">冻结行情与执行参数上的 grid / LHS 实验。每点单独保存，暂停在当前点完成后生效。最多 400 点、8 维；单点 120 秒，实验累计 1 小时、结果 64 MiB。</p></div><button type="button" className="button secondary" disabled={busy} onClick={() => perform(refresh)}><Icon name="refresh" />刷新实验历史</button></div>
    <p className="muted">当前为单股日线模型；旧组合仓位撮合、日内减仓、矩阵回测与样本外验证仍待承接。候选参数不会自动替换策略配置。</p>
    {error && <p className="alert error" role="alert">{error}</p>}{notice && <p className="alert" role="status">{notice}</p>}
    <div className="form-grid"><label><span>实验名称</span><input value={name} onChange={event => setName(event.target.value)} maxLength={80} /></label>
      <label><span>来源已完成回测</span><select value={sourceId} onChange={event => setSourceId(event.target.value)}><option value="">选择已完成回测</option>{sources.map(source => <option key={source.id} value={source.id}>{source.strategy_id} · {source.created_at.slice(0, 19)} · {source.id.slice(0, 8)}</option>)}</select></label>
      <label><span>采样方式</span><select value={mode} onChange={event => setMode(event.target.value as 'grid' | 'lhs')}><option value="grid">grid 完整网格</option><option value="lhs">LHS 分层采样</option></select></label>
      <label><span>随机种子</span><input value={seed} onChange={event => setSeed(event.target.value)} placeholder="留空由预览生成并冻结" /></label>
      <label><span>点数上限</span><input type="number" min={1} max={400} value={maxPoints} onChange={event => setMaxPoints(Number(event.target.value))} /></label>
      {mode === 'lhs' && <label><span>LHS 请求点数</span><input type="number" min={1} max={400} value={sampleCount} onChange={event => setSampleCount(Number(event.target.value))} /></label>}
    </div>
    {axes.map((axis, index) => <div className="toolbar" key={index} style={{ marginTop: 12, flexWrap: 'wrap' }}>
      <label>维度 {index + 1}<select aria-label={`参数维度 ${index + 1}`} value={axis.key} onChange={event => { const key = event.target.value, spec = schema[key]; changeAxis(index, { key, precision: spec?.type === 'integer' ? 0 : 4, values: spec?.type === 'boolean' ? 'true,false' : axis.values }) }}>{Object.entries(schema).filter(([key, spec]) => (!axes.some((item, at) => item.key === key && at !== index)) && (mode === 'grid' || ['integer', 'number'].includes(spec.type))).map(([key]) => <option key={key}>{key}</option>)}</select></label>
      {mode === 'grid' ? <label>取值，逗号分隔<input aria-label={`网格取值 ${index + 1}`} value={axis.values} onChange={event => changeAxis(index, { values: event.target.value })} /></label> : <><label>下限<input aria-label={`采样下限 ${index + 1}`} value={axis.low} onChange={event => changeAxis(index, { low: event.target.value })} /></label><label>上限<input aria-label={`采样上限 ${index + 1}`} value={axis.high} onChange={event => changeAxis(index, { high: event.target.value })} /></label><label>小数精度<input aria-label={`采样精度 ${index + 1}`} type="number" min={0} max={6} value={axis.precision} onChange={event => changeAxis(index, { precision: Number(event.target.value) })} /></label></>}
      <button type="button" className="button secondary" disabled={busy || axes.length === 1} onClick={() => setAxes(axes.filter((_, at) => at !== index))}>移除维度 {index + 1}</button>
    </div>)}
    <div className="toolbar" style={{ marginTop: 12 }}><button type="button" className="button secondary" disabled={busy || axes.length >= 8 || !Object.keys(schema).some(key => !axes.some(axis => axis.key === key))} onClick={() => { const key = Object.keys(schema).find(key => !axes.some(axis => axis.key === key) && (mode === 'grid' || ['integer', 'number'].includes(schema[key].type))); if (key) setAxes([...axes, { key, values: String(schema[key].minimum ?? 0), low: String(schema[key].minimum ?? 0), high: String(schema[key].maximum ?? 1), precision: schema[key].type === 'integer' ? 0 : 4 }]) }}>增加采样维度</button>
      <button type="button" className="button secondary" disabled={busy || !sourceId || !Object.keys(schema).length} onClick={() => perform(async () => { const checked = await api<Omit<Preview, 'requestKey'>>('/research/plateaus/preview', 'POST', body); if (live.current) setPreview({ ...checked, requestKey }) })}><Icon name="document" />预览采样计划</button>
    </div>
    {activePreview && <div className="alert" style={{ marginTop: 12 }}><p>请求 {activePreview.plan.requested_points} 点 → 实际 {activePreview.plan.actual_points} 点；去重 {activePreview.plan.deduplicated_points} 点；无效组合 {activePreview.plan.invalid_points} 点；冻结种子 {activePreview.plan.seed}。</p><p>{activePreview.plan.notes.join('；')}</p>
      <details><summary>查看冻结参数组合</summary><div className="table-wrap"><table><thead><tr><th>点</th><th>参数</th><th>预检结果</th></tr></thead><tbody>{activePreview.plan.points.map(item => <tr key={item.ordinal}><td>{item.ordinal + 1}</td><td>{JSON.stringify(item.axis_values)}</td><td>{item.error || '可执行'}</td></tr>)}</tbody></table></div></details>
      <button type="button" className="button" disabled={busy || !name.trim()} onClick={() => perform(async () => { const created = await api<Experiment>('/research/plateaus', 'POST', { ...body, seed: activePreview.plan.seed, name, expected_preview_sha256: activePreview.preview_sha256 }); selectedId.current = created.id; setSelected(created); setPoint(null); setNotice('实验已排队，和扫描、单股回测共享一个计算进程。'); await refresh() })}>确认并创建参数实验</button>
    </div>}
    <div className="table-wrap" style={{ marginTop: 20 }}><table><thead><tr><th>实验</th><th>状态</th><th>检查点</th><th>采样 / 种子</th><th>操作</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td>{run.name}</td><td>{stateName[run.state] || run.state}</td><td>{run.counts.succeeded || 0} 成功 / {run.counts.failed || 0} 失败 / {run.counts.invalid || 0} 无效 · 共 {run.plan.actual_points}</td><td>{run.plan.sampling_mode} · {run.plan.seed}</td><td><button type="button" className="link-button" disabled={busy} onClick={() => perform(() => choose(run.id))}>查看实验</button>{[['pause', 'pause', '暂停实验'], ['resume', 'resume', '继续实验'], ['cancel', 'cancel', '取消实验'], ['retry_failed', 'retry-failed', '重试失败点']].map(([capability, action, label]) => run.capabilities[capability] && <button key={action} type="button" className="link-button" disabled={busy} onClick={() => perform(() => control(run.id, action))}>{label}</button>)}{run.capabilities.delete && <button type="button" className="link-button danger" disabled={busy} onClick={() => setDeleteId(run.id)}>删除实验</button>}</td></tr>)}</tbody></table></div>
    {!runs.length && <p className="muted">暂无参数实验。先完成单股回测，再预览参数范围。</p>}
    {deleteId && <div className="alert error"><p>从历史列表删除“{runs.find(run => run.id === deleteId)?.name}”？检查点保留在本地审计数据中。</p><button type="button" className="button secondary danger" disabled={busy} onClick={() => perform(() => control(deleteId, 'delete'))}><Icon name="check" />确认删除实验</button><button type="button" className="button secondary" onClick={() => setDeleteId('')}>保留实验</button></div>}
    {selected && <section aria-label="收益平原详情" style={{ marginTop: 24 }}><h3>{selected.name} · {stateName[selected.state] || selected.state}</h3><p className="muted">{selected.symbol} · {selected.strategy_id} · 累计计算 {(selected.elapsed_ms / 1000).toFixed(1)} 秒 · 已保存 {(selected.total_bytes / 1048576).toFixed(2)} MiB</p>{selected.error && <p className="alert error">{selected.error}</p>}
      {['succeeded', 'failed', 'cancelled'].includes(selected.state) && <PlateauTableDownload id={selected.id} />}<a className="button secondary" href={`/api/v1/research/plateaus/${selected.id}/export.json`} download>导出采样与评估 JSON</a>
      {selected.points?.length ? <SliceHeatmap key={selected.id} points={selected.points} axisNames={Object.keys(selected.plan.axes)} onSelect={candidate => perform(() => choosePoint(candidate))} /> : null}
      <div className="table-wrap"><table aria-label="收益平原参数点"><thead><tr><th>排名 / 点</th><th>参数</th><th>状态 / 尝试</th><th>收益 / 回撤</th><th>交易 / 入场成交率</th><th>点分 / 平原分</th><th>说明</th><th /></tr></thead><tbody>{displayedPoints.map(item => { const scored = scoreMap.get(item.id); return <tr key={item.id}><td>{scored?.rank || '—'} / {item.ordinal + 1}{selected.analysis?.peak_point_id === item.id ? ' · 收益峰值' : ''}{selected.analysis?.recommended_point_id === item.id ? ' · 区域中心' : ''}</td><td>{Object.entries(item.axis_values).map(([key, value]) => `${key}=${value}`).join('；')}</td><td>{stateName[item.state] || item.state} / {item.attempt_number}</td><td>{pct(item.metrics?.total_return)} / {pct(item.metrics?.max_drawdown)}</td><td>{item.metrics?.trade_count ?? '—'} / {pct(item.metrics?.entry_fill_rate)}</td><td>{score(scored?.point_score)} / {score(scored?.plateau_score)}</td><td>{item.error || scored?.hard_filter_failures.join('；') || '—'}</td><td><button type="button" className="link-button" disabled={busy} onClick={() => perform(() => choosePoint(item))}>点详情</button></td></tr> })}</tbody></table></div>
      {selected.analysis && <><p className="muted">{selected.analysis.notes.join('；')}</p><h4>稳定区域</h4>{selected.analysis.regions.length ? selected.analysis.regions.map(region => <p key={region.id}>区域 {region.id.slice(0, 8)} · {region.point_count} 点 · 平原分中位 {score(region.median_plateau_score)} <button type="button" className="link-button" onClick={() => { const candidate = selected.points?.find(item => item.id === region.center_point_id); if (candidate) perform(() => choosePoint(candidate)) }}>查看区域中心</button></p>) : <p className="muted">当前样本未形成通过门槛的稳定区域；不自动推荐收益最高点。</p>}<h4>参数与样本内收益相关性</h4><div className="table-wrap"><table><thead><tr><th>参数</th><th>Pearson</th><th>成功样本数</th></tr></thead><tbody>{selected.analysis.correlations.map(item => <tr key={item.parameter}><td>{item.parameter}</td><td>{item.total_return_pearson?.toFixed(4) ?? '常量，无法计算'}</td><td>{item.sample_count}</td></tr>)}</tbody></table></div></>}
      <details><summary>冻结来源与执行配置</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(selected.frozen_context, null, 2)}</pre></details>
    </section>}
    {point && selected && <section aria-label="收益平原点详情" style={{ marginTop: 24 }}><h4>参数点 {point.ordinal + 1} · {stateName[point.state] || point.state}</h4><p>{point.error}</p>{pointScore && <p>邻居 {pointScore.neighbor_count} · 通过率 {pct(pointScore.neighbor_pass_rate)} · 中位分 {score(pointScore.neighbor_median_score)} · P25 {score(pointScore.neighbor_p25_score)} · 敏感性百分位 {score(pointScore.sensitivity_penalty)} · 局部稳定性 {score(pointScore.local_score)}</p>}
      <p className="muted">质量标记：{point.metrics?.quality_flags.join('、') || '无额外标记'}。以下预设仅保存策略参数，不包含事件模板、止盈止损、仓位、滑点与其他执行配置。</p>
      {point.state === 'succeeded' && <div className="toolbar"><label>候选参数预设名称<input value={presetName} onChange={event => setPresetName(event.target.value)} maxLength={80} /></label><button type="button" className="button secondary" disabled={busy || !presetName.trim()} onClick={() => perform(async () => { const params = Object.fromEntries(Object.entries(point.params).filter(([key]) => key !== 'mode')); await api('/research/presets', 'POST', { name: presetName, strategy_id: selected.strategy_id, params, favorite: false }); setNotice('候选参数已另存为预设；使用时仍需在策略表单中预览并显式应用。') })}>将该点另存为参数预设</button></div>}
      <details open><summary>参数与执行配置</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ params: point.params, config: point.config }, null, 2)}</pre></details>
      {point.result && <details><summary>冻结点结果、交易与决策证据</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 460, overflow: 'auto' }}>{JSON.stringify(point.result, null, 2)}</pre></details>}
    </section>}
  </section>
}
