import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, apiStream, apiUpload, type Analytics, type Trade } from './api'

type Kind = 'stock_analysis' | 'review_draft' | 'ocr_trades' | 'ocr_assets' | 'review_scores'
type ReviewType = 'daily' | 'weekly' | 'monthly' | 'round' | 'rehearsal'
type ScoreScope = 'daily' | 'trade' | 'batch' | 't_group'
type ScoreSubject = { subject_id: string; scope: 'daily' | 'trade' | 't_group'; trade_ids: string[]; scores: Record<string, { score: number | null; comment: string }>; comment: string }
type ScoreTarget = { subject_id: string; scope: ScoreSubject['scope']; trade_ids: string[]; current_revision: number; before: { scores?: Record<string, { final?: number | null; comment?: string }> } | null; dimensions: string[] }
type TradeRow = { trade_date: string | null; symbol: string | null; name: string; side: 'buy' | 'sell' | 'hold' | null; quantity: number | null; price: string | null; fee: string | null }
type PositionRow = { symbol: string | null; name: string; quantity: number | null; market_value: string | null }
type Output = { summary?: string; subjects?: ScoreSubject[]; conclusion?: string; confidence?: number | null; breakout_date?: string | null; trend_bull_type?: string | null; theme_name?: string | null; rise_reasons?: string[]; sections?: Record<string, string>; trades?: TradeRow[]; warnings?: string[]; snap_date?: string | null; total_assets?: string | null; available_cash?: string | null; positions?: PositionRow[] }
type Target = { dataset_id?: string; decision_at?: string; strict?: boolean; type?: ReviewType; key?: string; fields?: string[]; attachment_ids?: string[]; scope?: ScoreScope; trade_ids?: string[] }
type Source = { target?: Target; score_targets?: ScoreTarget[]; target_before?: { revision?: number; sections?: Record<string, string>; [key: string]: unknown }; destination_snapshot?: { revision?: number; [key: string]: unknown } | null; account_revision?: number; breakout_candidates?: string[]; images?: Array<{ id: string; revision: number; sha256: string; mime_type: string; byte_size: number }>; [key: string]: unknown }
type Generation = { id: string; revision: number; status: string; kind: Kind; account_id: string | null; run_id: string; target: Target; source: Source; input_sha256: string; output: Output | null; validation_errors: Array<string | { code?: string; message?: string }>; acceptance: Record<string, unknown> | null; created_at: string; updated_at: string; deleted: boolean }
type Preview = { kind: Kind; target: Target; config_revision: number; channel: string; model: string; provider: string; messages: Array<{ role: string; content: unknown }>; context: unknown; input_sha256: string }
type RequestBody = { kind: Kind; config_revision: number; notes: string; target: Target }
type ModelConfig = { revision: number; text: { model: string; configured: boolean; secret_ref: string; secret_configured: boolean }; vision: { model: string; configured: boolean; secret_ref: string; secret_configured: boolean } }
type Call = { id: string; account_id: string | null; status: string; output: string; error_code: string | null; cancel_requested: boolean; usage: { prompt_tokens: number | null; completion_tokens: number | null; total_tokens: number | null } }
type Dataset = { id: string; symbol: string; first_date: string; last_date: string; adjustment: string }
type Attachment = { id: string; url: string; original_name: string; byte_size: number; revision: number }
type Audit = { id: string; action: string; revision: number; created_at: string; snapshot: unknown }
const kindNames: Record<Kind, string> = { stock_analysis: '个股研究', review_draft: '复盘与预演草稿', ocr_trades: '截图成交识别', ocr_assets: '截图资产识别', review_scores: 'AI 复盘评分建议' }
const scoreNames: Record<string, string> = { position: '仓位', drawdown: '回撤', discipline: '纪律', entry: '买入', exit: '卖出', emotion: '心态', timing: '时机' }
const phaseNames: Record<string, string> = { queued: '等待模型结果', running: '正在生成', completed: '模型已完成', failed: '调用失败', cancelled: '已停止', interrupted: '响应中断', draft: '待人工核对', invalid_output: '结果需要修正', accepted: '已接受', rejected: '已拒绝', retracted: '已撤回' }
const fieldNames: Record<string, string> = { title: '标题', market_observation: '市场观察', decision_review: '决策复盘', mistakes: '问题与错误', tomorrow_plan: '明日计划', overall_summary: '当日总结', reflection: '反思与改进', next_market_forecast: '市场预演', next_position_plan: '仓位预演', next_risk_plan: '风险预案', core_goals: '核心目标', achievements: '完成情况', resource_analysis: '资源分析', market_rhythm: '市场节奏', next_week_strategy: '下周策略', key_insight: '关键认知', right_things: '做对的事', wrong_things: '做错的事', market_review: '市场回顾', next_strategy: '下期策略', system_iteration: '体系迭代', next_goal: '下月目标', summary: '总结' }
const fields: Record<ReviewType, string[]> = { daily: ['title', 'market_observation', 'decision_review', 'mistakes', 'tomorrow_plan', 'overall_summary', 'reflection', 'next_market_forecast', 'next_position_plan', 'next_risk_plan'], rehearsal: ['next_market_forecast', 'next_position_plan', 'next_risk_plan'], weekly: ['core_goals', 'achievements', 'resource_analysis', 'market_rhythm', 'next_week_strategy', 'key_insight', 'right_things', 'wrong_things', 'market_review', 'next_strategy'], monthly: ['system_iteration', 'next_goal', 'summary', 'market_review'], round: ['summary'] }
const today = () => new Date().toLocaleDateString('sv-SE')
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
const plain = { whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, fontFamily: 'inherit', lineHeight: 1.6 }
const newTrade = (): TradeRow => ({ trade_date: null, symbol: null, name: '', side: null, quantity: null, price: null, fee: null })
const newPosition = (): PositionRow => ({ symbol: null, name: '', quantity: null, market_value: null })
function emptyOutput(kind: Kind, target: Target, targets: ScoreTarget[] = []): Output {
  if (kind === 'review_scores') return { summary: '', subjects: targets.map(item => ({ subject_id: item.subject_id, scope: item.scope, trade_ids: item.trade_ids, comment: '', scores: Object.fromEntries(item.dimensions.map(dimension => [dimension, { score: null, comment: '' }])) })) }
  if (kind === 'stock_analysis') return { summary: '', conclusion: 'Unknown', confidence: null, breakout_date: null, trend_bull_type: null, theme_name: null, rise_reasons: [] }
  if (kind === 'review_draft') return { sections: Object.fromEntries((target.fields || []).map(key => [key, ''])) }
  if (kind === 'ocr_trades') return { trades: [], warnings: [] }
  return { snap_date: null, total_assets: null, available_cash: null, positions: [], warnings: [] }
}

function OutputEditor({ kind, value, onChange, disabled, candidates, scoreTargets }: { kind: Kind; value: Output; onChange: (next: Output) => void; disabled: boolean; candidates: string[]; scoreTargets: ScoreTarget[] }) {
  const text = (key: keyof Output, label: string, multiline = false, nullable = false) => <label className="field" key={key}><span>{label}</span>{multiline ? <textarea aria-label={label} disabled={disabled} value={String(value[key] ?? '')} onChange={event => onChange({ ...value, [key]: event.target.value })} /> : <input aria-label={label} disabled={disabled} value={String(value[key] ?? '')} onChange={event => onChange({ ...value, [key]: nullable ? event.target.value || null : event.target.value })} />}</label>
  if (kind === 'stock_analysis') return <div className="form"><div className="form-grid">{text('summary', '研究摘要', true)}<label className="field"><span>研究阶段</span><select aria-label="研究阶段" disabled={disabled} value={value.conclusion || 'Unknown'} onChange={event => onChange({ ...value, conclusion: event.target.value })}>{['Unknown', '发酵中', '高潮', '退潮'].map(item => <option key={item} value={item}>{item === 'Unknown' ? '未知' : item}</option>)}</select></label><label className="field"><span>模型置信度（0–1）</span><input disabled={disabled} type="number" min="0" max="1" step="0.01" value={value.confidence ?? ''} onChange={event => onChange({ ...value, confidence: event.target.value === '' ? null : Number(event.target.value) })} /></label><label className="field"><span>起爆日（冻结候选日）</span><select aria-label="起爆日（冻结候选日）" disabled={disabled} value={value.breakout_date || ''} onChange={event => onChange({ ...value, breakout_date: event.target.value || null })}><option value="">未知 / 无证据</option>{candidates.map(day => <option key={day} value={day}>{day}</option>)}</select></label>{text('trend_bull_type', '趋势类型（可留空）', false, true)}{text('theme_name', '题材名称（可留空）', false, true)}</div><label className="field"><span>上涨原因（每行一项，最多 5 项）</span><textarea aria-label="上涨原因" disabled={disabled} value={(value.rise_reasons || []).join('\n')} onChange={event => onChange({ ...value, rise_reasons: event.target.value.split('\n') })} /></label></div>
  if (kind === 'review_draft') return <div className="form">{Object.entries(value.sections || {}).map(([key, content]) => <label className="field" key={key}><span>{fieldNames[key] || key}</span><textarea aria-label={`草稿栏目 ${fieldNames[key] || key}`} disabled={disabled} value={content} onChange={event => onChange({ ...value, sections: { ...value.sections, [key]: event.target.value } })} /></label>)}</div>
  if (kind === 'review_scores') return <div className="form">{text('summary', '评分总结', true)}{value.subjects?.map((subject, index) => {
    const target = scoreTargets.find(item => item.subject_id === subject.subject_id)
    const update = (patch: Partial<ScoreSubject>) => onChange({ ...value, subjects: value.subjects?.map((item, offset) => index === offset ? { ...item, ...patch } : item) })
    return <div className="market-detail" key={subject.subject_id}><h3>{subject.scope === 'daily' ? '整日评分' : subject.scope === 't_group' ? '做 T 组合' : '逐笔评分'} · {subject.subject_id.slice(0, 20)}</h3><p className="muted">冻结评分版本 {target?.current_revision ?? '未知'} · 关联成交 {subject.trade_ids.length} 笔</p><div className="table-wrap"><table><thead><tr><th>维度</th><th>现有人工最终分</th><th>AI 建议（0–10）</th><th>建议依据</th></tr></thead><tbody>{Object.entries(subject.scores).map(([dimension, score]) => <tr key={dimension}><td>{scoreNames[dimension] || dimension}</td><td>{target?.before?.scores?.[dimension]?.final ?? '尚未评分'}</td><td><label className="field"><span>建议分 · {index + 1} · {scoreNames[dimension] || dimension}</span><input disabled={disabled} type="number" min="0" max="10" step="1" value={score.score ?? ''} onChange={event => update({ scores: { ...subject.scores, [dimension]: { ...score, score: event.target.value === '' ? null : Number(event.target.value) } } })} /></label></td><td><label className="field"><span>依据 · {index + 1} · {scoreNames[dimension] || dimension}</span><textarea aria-label={`依据 · ${index + 1} · ${scoreNames[dimension] || dimension}`} disabled={disabled} value={score.comment} onChange={event => update({ scores: { ...subject.scores, [dimension]: { ...score, comment: event.target.value } } })} /></label></td></tr>)}</tbody></table></div><label className="field"><span>目标点评 · {index + 1}</span><textarea aria-label={`目标点评 · ${index + 1}`} disabled={disabled} value={subject.comment} onChange={event => update({ comment: event.target.value })} /></label></div>
  })}</div>
  const updateTrade = (index: number, patch: Partial<TradeRow>) => onChange({ ...value, trades: value.trades?.map((row, offset) => offset === index ? { ...row, ...patch } : row) })
  const updatePosition = (index: number, patch: Partial<PositionRow>) => onChange({ ...value, positions: value.positions?.map((row, offset) => offset === index ? { ...row, ...patch } : row) })
  return <div className="form"><p className="muted">空白数值表示未识别。请核对截图，只有明确确认的零值才填 0。</p>{kind === 'ocr_assets' && <div className="form-grid"><label className="field"><span>资产日期</span><input type="date" disabled={disabled} value={value.snap_date || ''} onChange={event => onChange({ ...value, snap_date: event.target.value || null })} /></label>{text('total_assets', '总资产（元）', false, true)}{text('available_cash', '可用资金（元）', false, true)}</div>}
    <div className="table-wrap"><table><thead><tr>{kind === 'ocr_trades' && <th>日期 / 方向</th>}<th>代码 / 名称</th><th>数量（股）</th><th>{kind === 'ocr_trades' ? '成交价 / 实付费用' : '持仓市值（元）'}</th><th>操作</th></tr></thead><tbody>{kind === 'ocr_trades' ? (value.trades || []).map((row, index) => <tr key={index}><td><label className="field"><span>日期 · {index + 1}</span><input type="date" disabled={disabled} value={row.trade_date || ''} onChange={event => updateTrade(index, { trade_date: event.target.value || null })} /></label><label className="field"><span>方向 · {index + 1}</span><select aria-label={`方向 · ${index + 1}`} disabled={disabled} value={row.side || ''} onChange={event => updateTrade(index, { side: event.target.value ? event.target.value as TradeRow['side'] : null })}><option value="">未知</option><option value="buy">买入</option><option value="sell">卖出</option><option value="hold">仅持仓，无成交方向</option></select></label></td><td><label className="field"><span>代码 · {index + 1}</span><input disabled={disabled} value={row.symbol || ''} onChange={event => updateTrade(index, { symbol: event.target.value || null })} /></label><label className="field"><span>名称 · {index + 1}</span><input disabled={disabled} value={row.name} onChange={event => updateTrade(index, { name: event.target.value })} /></label></td><td><label className="field"><span>数量 · {index + 1}</span><input type="number" min="0" step="1" disabled={disabled} value={row.quantity ?? ''} onChange={event => updateTrade(index, { quantity: event.target.value === '' ? null : Number(event.target.value) })} /></label></td><td><label className="field"><span>成交价 · {index + 1}</span><input disabled={disabled} value={row.price || ''} onChange={event => updateTrade(index, { price: event.target.value || null })} /></label><label className="field"><span>实付费用 · {index + 1}</span><input disabled={disabled} value={row.fee ?? ''} onChange={event => updateTrade(index, { fee: event.target.value || null })} /></label></td><td><button className="link-button danger" type="button" disabled={disabled} onClick={() => onChange({ ...value, trades: value.trades?.filter((_, offset) => offset !== index) })}>移除此行</button></td></tr>) : (value.positions || []).map((row, index) => <tr key={index}><td><label className="field"><span>代码 · {index + 1}</span><input disabled={disabled} value={row.symbol || ''} onChange={event => updatePosition(index, { symbol: event.target.value || null })} /></label><label className="field"><span>名称 · {index + 1}</span><input disabled={disabled} value={row.name} onChange={event => updatePosition(index, { name: event.target.value })} /></label></td><td><label className="field"><span>数量 · {index + 1}</span><input type="number" min="0" step="1" disabled={disabled} value={row.quantity ?? ''} onChange={event => updatePosition(index, { quantity: event.target.value === '' ? null : Number(event.target.value) })} /></label></td><td><label className="field"><span>持仓市值 · {index + 1}</span><input disabled={disabled} value={row.market_value ?? ''} onChange={event => updatePosition(index, { market_value: event.target.value || null })} /></label></td><td><button className="link-button danger" type="button" disabled={disabled} onClick={() => onChange({ ...value, positions: value.positions?.filter((_, offset) => offset !== index) })}>移除此行</button></td></tr>)}</tbody></table></div>
    <button className="button secondary" type="button" disabled={disabled || (kind === 'ocr_trades' ? value.trades?.length || 0 : value.positions?.length || 0) >= 100} onClick={() => kind === 'ocr_trades' ? onChange({ ...value, trades: [...value.trades || [], newTrade()] }) : onChange({ ...value, positions: [...value.positions || [], newPosition()] })}>补充一行</button><label className="field"><span>识别提示（每行一项）</span><textarea aria-label="识别提示" disabled={disabled} value={(value.warnings || []).join('\n')} onChange={event => onChange({ ...value, warnings: event.target.value.split('\n').filter(Boolean) })} /></label></div>
}

export function AIGenerationEditor({ accountId, accountKind, onBusyChange }: { accountId?: string; accountKind?: string; onBusyChange?: (busy: boolean) => void }) {
  const [scope, setScope] = useState('')
  const [kind, setKind] = useState<Kind>('stock_analysis')
  const [config, setConfig] = useState<ModelConfig | null>(null)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [decisionAt, setDecisionAt] = useState('')
  const [strict, setStrict] = useState(true)
  const [reviewType, setReviewType] = useState<ReviewType>('daily')
  const [targetKey, setTargetKey] = useState(today)
  const [selectedFields, setSelectedFields] = useState(['overall_summary', 'reflection'])
  const [rounds, setRounds] = useState<Array<{ id: string; symbol: string; name: string; start_date: string; end_date: string | null }>>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [scoreScope, setScoreScope] = useState<ScoreScope>('daily')
  const [scoreDay, setScoreDay] = useState(today)
  const [tradeIds, setTradeIds] = useState<string[]>([])
  const [attachmentDay, setAttachmentDay] = useState(today)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [attachmentIds, setAttachmentIds] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewBody, setPreviewBody] = useState<RequestBody | null>(null)
  const [generations, setGenerations] = useState<Generation[]>([])
  const [selected, setSelected] = useState<Generation | null>(null)
  const [draft, setDraft] = useState<Output | null>(null)
  const [dirty, setDirty] = useState(false)
  const [run, setRun] = useState<Call | null>(null)
  const [audit, setAudit] = useState<Audit[] | null>(null)
  const [pending, setPending] = useState<'accept' | 'reject' | 'retract' | 'delete' | null>(null)
  const [busy, setBusy] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const controller = useRef<AbortController | null>(null)
  const mounted = useRef(true)
  const scoped = (path: string, value = scope) => value ? `${path}${path.includes('?') ? '&' : '?'}account_id=${encodeURIComponent(value)}` : path
  const locked = busy || streaming || run?.status === 'running'
  useEffect(() => { onBusyChange?.(locked); return () => onBusyChange?.(false) }, [locked, onBusyChange])
  useEffect(() => {
    mounted.current = true
    let live = true
    Promise.all([api<ModelConfig>('/ai/config'), api<Dataset[]>('/market/datasets')]).then(([next, market]) => { if (live) { setConfig(next); setDatasets(market) } }).catch(err => { if (live) setError(err.message) })
    return () => { live = false; mounted.current = false; controller.current?.abort() }
  }, [])
  useEffect(() => {
    let live = true
    setSelected(null); setDraft(null); setDirty(false); setRun(null); setPreview(null); setPreviewBody(null); setPending(null); setAudit(null); setGenerations([]); setAttachmentIds([]); setAttachments([]); setRounds([]); setTrades([]); setTradeIds([])
    api<Generation[]>(scoped('/ai/generations')).then(items => { if (live) setGenerations(items) }).catch(err => { if (live) setError(err.message) })
    if (scope) api<Analytics>(`/accounts/${encodeURIComponent(scope)}/analytics`).then(value => { if (live) setRounds(value.result?.rounds || []) }).catch(err => { if (live) setError(err.message) })
    if (scope) api<Trade[]>(`/accounts/${encodeURIComponent(scope)}/trades`).then(value => { if (live) setTrades(value) }).catch(err => { if (live) setError(err.message) })
    return () => { live = false }
  }, [scope])
  useEffect(() => {
    let live = true
    setAttachments([]); setAttachmentIds([])
    if (scope && attachmentDay) api<Attachment[]>(`/accounts/${encodeURIComponent(scope)}/daily-reviews/${attachmentDay}/attachments`).then(items => { if (live) setAttachments(items) }).catch(err => { if (live) setError(err.message) })
    return () => { live = false }
  }, [scope, attachmentDay])
  useEffect(() => { setPreview(null); setPreviewBody(null) }, [kind, datasetId, decisionAt, strict, reviewType, targetKey, selectedFields, attachmentIds, notes, scoreScope, scoreDay, tradeIds])
  useEffect(() => {
    if (!run || run.status !== 'running' || streaming) return
    let live = true, reading = false
    const timer = window.setInterval(async () => {
      if (reading) return
      reading = true
      try { const next = await api<Call>(scoped(`/ai/runs/${run.id}`, run.account_id || '')); if (live) setRun(next) }
      catch (err) { if (live) setError(err instanceof Error ? err.message : '调用状态读取失败') }
      finally { reading = false }
    }, 2000)
    return () => { live = false; window.clearInterval(timer) }
  }, [run?.id, run?.status, streaming])

  function install(value: Generation) { setSelected(value); setDraft(value.output); setDirty(false); setPending(null); setAudit(null); setGenerations(current => [value, ...current.filter(item => item.id !== value.id)]) }
  function report(err: unknown) { const caught = err as Error & { code?: string }; setError(caught.message || '操作失败'); if (caught.code?.includes('CONFLICT') || caught.code?.includes('CHANGED')) setNotice('草稿已保留；来源或目标版本已变化，请核对当前记录后重新生成。') }
  async function refresh() {
    setBusy(true); setError('')
    try { const [items, nextConfig, market, latestTrades] = await Promise.all([api<Generation[]>(scoped('/ai/generations')), api<ModelConfig>('/ai/config'), api<Dataset[]>('/market/datasets'), scope ? api<Trade[]>(`/accounts/${encodeURIComponent(scope)}/trades`) : Promise.resolve([])]); setGenerations(items); setConfig(nextConfig); setDatasets(market); setTrades(latestTrades); setPreview(null); setPreviewBody(null); setNotice('记录与模型配置已刷新，当前草稿保留。') }
    catch (err) { report(err) } finally { setBusy(false) }
  }
  async function open(item: Generation) {
    if (dirty && !window.confirm('载入记录将替换当前未保存的草稿，确认载入？')) return
    setBusy(true); setError('')
    try { const value = await api<Generation>(scoped(`/ai/generations/${item.id}`)); install(value); setRun(await api<Call>(scoped(`/ai/runs/${value.run_id}`))) }
    catch (err) { report(err) } finally { setBusy(false) }
  }
  function body(): RequestBody {
    if (!config) throw new Error('请先刷新模型配置')
    let target: Target
    if (kind === 'stock_analysis') { if (!datasetId || !decisionAt) throw new Error('请选择冻结行情和明确的决策时间'); target = { dataset_id: datasetId, decision_at: new Date(decisionAt).toISOString(), strict } }
    else {
      if (!scope) throw new Error('此类任务需要显式选择真实账户')
      if (kind === 'review_scores') { if (!scoreDay) throw new Error('请选择评分日期'); if (scoreScope !== 'daily' && !tradeIds.length) throw new Error('请选择本次评分的成交'); target = { scope: scoreScope, key: scoreDay, trade_ids: scoreScope === 'daily' ? [] : tradeIds } }
      else if (kind === 'review_draft') { if (!targetKey || !selectedFields.length) throw new Error('请选择复盘目标与至少一个栏目'); target = { type: reviewType, key: targetKey, fields: selectedFields } }
      else { if (!attachmentIds.length || attachmentIds.length > 3) throw new Error('请选择 1 至 3 张截图'); if (attachments.filter(item => attachmentIds.includes(item.id)).reduce((sum, item) => sum + item.byte_size, 0) > 8 * 1024 * 1024) throw new Error('本次图片总大小不能超过 8 MB'); target = { attachment_ids: attachmentIds } }
    }
    return { kind, config_revision: config.revision, notes, target }
  }
  async function previewTask(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try { const request = body(); const value = await api<Preview>(scoped('/ai/generations/preview'), 'POST', request); setPreview(value); setPreviewBody(request); setNotice('已预览本次携带的资料与目标，尚未调用外部模型。') }
    catch (err) { report(err) } finally { setBusy(false) }
  }
  async function stream(call: Call) {
    const current = new AbortController(); controller.current = current; setStreaming(true); setRun(call); setError('')
    try { await apiStream(scoped(`/ai/runs/${call.id}/stream`, call.account_id || ''), {}, event => {
      if (!mounted.current) return
      const data = event.data as { type?: string; content?: string; run?: Call; usage?: Call['usage'] }
      const eventKind = event.event === 'message' ? data.type : event.event
      if (data.run) setRun(data.run)
      else if (eventKind === 'started') setRun(value => value ? { ...value, status: 'running' } : value)
      else if (eventKind === 'delta' && typeof data.content === 'string') setRun(value => value ? { ...value, status: 'running', output: value.output + data.content } : value)
      else if (eventKind === 'usage' && data.usage) setRun(value => value ? { ...value, usage: data.usage! } : value)
    }, current.signal) } catch (err) { if (!(err instanceof DOMException && err.name === 'AbortError') && mounted.current) report(err) }
    finally { if (mounted.current) { try { setRun(await api<Call>(scoped(`/ai/runs/${call.id}`, call.account_id || ''))) } catch (err) { report(err) }; setStreaming(false) }; if (controller.current === current) controller.current = null }
  }
  async function generate() {
    if (!preview || !previewBody) return
    setBusy(true); setError('')
    try { const value = await api<Generation>(scoped('/ai/generations'), 'POST', { ...previewBody, expected_input_sha256: preview.input_sha256 }); install(value); setPreview(null); setPreviewBody(null); const call = await api<Call>(scoped(`/ai/runs/${value.run_id}`)); setBusy(false); await stream(call) }
    catch (err) { report(err) } finally { setBusy(false) }
  }
  async function stop() {
    if (!run) return
    try { setRun(await api<Call>(scoped(`/ai/runs/${run.id}/cancel`, run.account_id || ''), 'POST', {})); controller.current?.abort(); setNotice('已请求停止，部分响应会保留。') } catch (err) { report(err) }
  }
  async function finalize() {
    if (!selected) return
    setBusy(true); setError('')
    try { install(await api<Generation>(scoped(`/ai/generations/${selected.id}/finalize`), 'POST', { expected_revision: selected.revision })); setNotice('已校验模型结果。请人工核对后选择保存修改或接受。') }
    catch (err) { report(err) } finally { setBusy(false) }
  }
  async function saveDraft(event: FormEvent) {
    event.preventDefault(); if (!selected || !draft) return
    setBusy(true); setError('')
    try { const output = { ...draft, ...(draft.rise_reasons ? { rise_reasons: draft.rise_reasons.filter(item => item.trim()) } : {}) }; install(await api<Generation>(scoped(`/ai/generations/${selected.id}`), 'PUT', { expected_revision: selected.revision, output })); setNotice('人工修正已保存，尚未写入业务记录。') }
    catch (err) { report(err) } finally { setBusy(false) }
  }
  async function act() {
    if (!selected || !pending) return
    setBusy(true); setError('')
    try {
      if (pending === 'delete') { await api(scoped(`/ai/generations/${selected.id}?expected_revision=${selected.revision}`), 'DELETE'); setGenerations(current => current.filter(item => item.id !== selected.id)); setSelected(null); setRun(null); setDraft(null); setPending(null); setNotice('生成记录已删除。'); return }
      const payload: Record<string, unknown> = { expected_revision: selected.revision }
      if (pending === 'accept' && selected.kind === 'review_draft') payload.expected_target_revision = selected.source.target_before?.revision ?? 0
      if (pending === 'accept' && selected.kind === 'ocr_assets') payload.expected_target_revision = selected.source.destination_snapshot?.revision ?? 0
      install(await api<Generation>(scoped(`/ai/generations/${selected.id}/${pending}`), 'POST', payload))
      setNotice(pending === 'accept' ? selected.kind === 'ocr_trades' ? '已送入待确认交易，请到交易流水的待确认区逐笔核对。' : '已接受本次草稿。' : pending === 'retract' ? '已撤回本次接受。' : '已拒绝该草稿。')
    } catch (err) { report(err) } finally { setBusy(false) }
  }
  async function upload(file: File) {
    setBusy(true); setError('')
    try { if (!scope) throw new Error('请先选择真实账户'); await apiUpload(`/accounts/${encodeURIComponent(scope)}/daily-reviews/${attachmentDay}/attachments`, file); setAttachments(await api<Attachment[]>(`/accounts/${encodeURIComponent(scope)}/daily-reviews/${attachmentDay}/attachments`)); setNotice('截图已保存在所选账户的复盘附件中，请勾选本次要识别的图片。') }
    catch (err) { report(err) } finally { setBusy(false) }
  }
  const editable = Boolean(selected && ['draft', 'invalid_output'].includes(selected.status))
  const chosenModel = config?.[kind.startsWith('ocr') ? 'vision' : 'text']
  const missingConfig = !chosenModel?.configured || Boolean(chosenModel.secret_ref && !chosenModel.secret_configured)
  const selectedTarget = selected?.target || selected?.source.target || {}
  const targetRevision = selected?.kind === 'ocr_assets' ? selected.source.destination_snapshot?.revision ?? 0 : selected?.source.target_before?.revision ?? 0

  return <div aria-label="AI 识别与复盘">
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    <section className="card"><h2>识别与复盘草稿</h2><p className="muted">先预览本次资料，再调用模型。生成内容经过校验与人工核对后，才能接受为研究记录、复盘文字或待确认数据。</p><form className="form" onSubmit={previewTask}><div className="form-grid"><label className="field"><span>任务类型</span><select aria-label="任务类型" disabled={locked} value={kind} onChange={event => setKind(event.target.value as Kind)}>{Object.entries(kindNames).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label className="field"><span>本次账户范围</span><select aria-label="本次账户范围" disabled={locked} value={scope} onChange={event => setScope(event.target.value)}><option value="">不绑定账户（仅个股研究）</option>{accountId && accountKind === 'real' && <option value={accountId}>当前真实账户 · {accountId.slice(0, 8)}</option>}</select></label></div>
      {kind === 'stock_analysis' ? <div className="form-grid"><label className="field"><span>研究冻结行情</span><select aria-label="研究冻结行情" disabled={locked} value={datasetId} onChange={event => { setDatasetId(event.target.value); const item = datasets.find(value => value.id === event.target.value); if (item) setDecisionAt(`${item.last_date}T17:00`) }}><option value="">请选择样本</option>{datasets.map(item => <option key={item.id} value={item.id}>{item.symbol} · {item.first_date} 至 {item.last_date} · {item.adjustment} · {item.id.slice(0, 8)}</option>)}</select></label><label className="field"><span>研究决策时间（浏览器本地时区）</span><input type="datetime-local" disabled={locked} value={decisionAt} onChange={event => setDecisionAt(event.target.value)} /></label><label className="check-field"><input type="checkbox" disabled={locked} checked={strict} onChange={event => setStrict(event.target.checked)} />严格检查可得时间</label></div> : kind === 'review_scores' ? <div><div className="form-grid"><label className="field"><span>评分日期</span><input type="date" disabled={locked} value={scoreDay} onChange={event => { setScoreDay(event.target.value); setTradeIds([]) }} /></label><label className="field"><span>评分范围</span><select aria-label="评分范围" disabled={locked} value={scoreScope} onChange={event => { setScoreScope(event.target.value as ScoreScope); setTradeIds([]) }}><option value="daily">整日复盘</option><option value="trade">单笔成交</option><option value="batch">批量逐笔评分</option><option value="t_group">同日做 T 组合</option></select></label></div>{scoreScope !== 'daily' && <div className="table-wrap" style={{ maxHeight: 280, overflowY: 'auto', marginTop: 12 }}><table><thead><tr><th>选择</th><th>证券</th><th>方向</th><th>股数 / 价格</th><th>记录</th></tr></thead><tbody>{trades.filter(item => item.trade_date === scoreDay).map(item => <tr key={item.id}><td><input type="checkbox" aria-label={`评分成交 ${item.symbol} ${item.id.slice(0, 8)}`} disabled={locked || (!tradeIds.includes(item.id) && tradeIds.length >= 100)} checked={tradeIds.includes(item.id)} onChange={event => setTradeIds(current => event.target.checked ? scoreScope === 'trade' ? [item.id] : [...current, item.id] : current.filter(id => id !== item.id))} /></td><td>{item.symbol} · {item.name}</td><td>{item.side === 'buy' ? '买入' : '卖出'}</td><td>{item.quantity} / {item.price}</td><td>{item.id.slice(0, 8)}</td></tr>)}</tbody></table></div>}<p className="muted">整日评分覆盖当日成交；批量逐笔可选最多 100 笔；做 T 组合须为同日同股并含买卖双方。评分对象与已有版本会在预览时冻结。</p></div> : kind === 'review_draft' ? <div><div className="form-grid"><label className="field"><span>复盘目标类型</span><select aria-label="复盘目标类型" disabled={locked} value={reviewType} onChange={event => { const value = event.target.value as ReviewType; setReviewType(value); setSelectedFields(value === 'daily' ? ['overall_summary', 'reflection'] : fields[value]); setTargetKey(value === 'monthly' ? today().slice(0, 7) : value === 'daily' || value === 'rehearsal' ? today() : '') }}>{Object.entries({ daily: '日复盘', weekly: '周总结', monthly: '月总结', round: '交易回合摘要', rehearsal: '交易预演' }).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label className="field"><span>复盘目标</span>{reviewType === 'round' ? <select aria-label="复盘目标" disabled={locked} value={targetKey} onChange={event => setTargetKey(event.target.value)}><option value="">请选择交易回合</option>{rounds.map(item => <option key={item.id} value={item.id}>{item.symbol} · {item.start_date} 至 {item.end_date || '持仓中'}</option>)}</select> : <input aria-label="复盘目标" type={reviewType === 'weekly' ? 'week' : reviewType === 'monthly' ? 'month' : 'date'} disabled={locked} value={targetKey} onChange={event => setTargetKey(event.target.value)} />}</label></div><div className="toolbar" style={{ flexWrap: 'wrap', marginTop: 12 }}>{fields[reviewType].map(key => <label className="check-field" key={key}><input type="checkbox" disabled={locked} checked={selectedFields.includes(key)} onChange={event => setSelectedFields(current => event.target.checked ? [...current, key] : current.filter(value => value !== key))} />{fieldNames[key]}</label>)}</div></div> : <div><div className="form-grid"><label className="field"><span>截图所属复盘日期</span><input type="date" disabled={locked} value={attachmentDay} onChange={event => setAttachmentDay(event.target.value)} /></label><label className="field"><span>上传待识别截图</span><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={locked || !scope} onChange={event => { const file = event.target.files?.[0]; if (file) upload(file); event.target.value = '' }} /></label></div><p className="muted">勾选 1 至 3 张，总计不超过 8 MB。截图保存在选定账户的复盘附件中，只有确认生成时才发送到视觉模型。</p><div className="inspiration-grid">{attachments.map(item => <figure key={item.id} className="review-image"><img src={item.url} alt={item.original_name} loading="lazy" /><figcaption><label className="check-field"><input type="checkbox" disabled={locked || (!attachmentIds.includes(item.id) && attachmentIds.length >= 3)} checked={attachmentIds.includes(item.id)} onChange={event => setAttachmentIds(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} />{item.original_name} · {(item.byte_size / 1024).toFixed(1)} KB</label></figcaption></figure>)}</div>{!attachments.length && <p className="muted">该账户和日期暂无截图。</p>}</div>}
      <label className="field"><span>本次补充要求</span><textarea aria-label="本次补充要求" disabled={locked} value={notes} maxLength={4000} onChange={event => setNotes(event.target.value)} /></label><p className="muted">本次使用{kind.startsWith('ocr') ? '视觉' : '文本'}模型：{chosenModel?.model || '未配置'}{missingConfig && '。请到“对话与配置”补全配置。'}</p><div className="form-actions"><button className="button secondary" disabled={locked}>预览资料与提示词</button><button className="button primary" type="button" disabled={locked || !preview || missingConfig} onClick={generate}>确认发送并生成</button><button className="button secondary" type="button" disabled={locked} onClick={refresh}>刷新记录与模型</button></div></form>
      {preview && <details open style={{ marginTop: 16 }}><summary>本次预览 · {kindNames[preview.kind]} · {preview.model}</summary><p className="muted" style={{ overflowWrap: 'anywhere' }}>配置版本 {preview.config_revision} · {preview.provider} · 输入摘要 {preview.input_sha256}</p><details><summary>携带来源与目标版本</summary><pre style={plain}>{pretty(preview.context)}</pre></details>{preview.messages.map((item, index) => <div key={index}><strong>{item.role}</strong><pre style={plain}>{typeof item.content === 'string' ? item.content : pretty(item.content)}</pre></div>)}</details>}
    </section>
    <section className="card"><h2>生成记录</h2><div className="table-wrap"><table><thead><tr><th>时间</th><th>类型 / 目标</th><th>状态</th><th>版本</th><th>操作</th></tr></thead><tbody>{generations.map(item => <tr key={item.id}><td>{new Date(item.created_at).toLocaleString()}</td><td>{kindNames[item.kind]}<br /><small>{item.target?.key || item.target?.dataset_id?.slice(0, 8) || '截图资料'}</small></td><td>{phaseNames[item.status] || item.status}</td><td>{item.revision}</td><td><button type="button" className="link-button" disabled={locked} onClick={() => open(item)}>查看与核对</button></td></tr>)}</tbody></table></div>{!generations.length && <p className="muted">当前账户范围暂无生成记录。</p>}</section>
    {selected && <section className="card"><h2>{kindNames[selected.kind]} · {phaseNames[selected.status] || selected.status} · 版本 {selected.revision}</h2>{run && <div><p>模型调用：{phaseNames[run.status] || run.status}{run.cancel_requested && run.status === 'running' && ' · 正在停止'} · token {run.usage.total_tokens == null ? '未知' : run.usage.total_tokens}</p>{run.error_code && <p className="danger">{run.error_code}</p>}<details open={run.status === 'running'}><summary>模型原始响应</summary><pre aria-live={streaming ? 'polite' : 'off'} style={plain}>{run.output || '尚未收到内容'}</pre></details><div className="form-actions">{run.status === 'queued' && <button className="button primary" type="button" disabled={locked} onClick={() => stream(run)}>执行已冻结请求</button>}{['queued', 'running'].includes(run.status) && <button className="button secondary" type="button" onClick={stop}>停止本次生成</button>}{run.status === 'completed' && selected.status === 'queued' && <button className="button primary" type="button" disabled={locked} onClick={finalize}>校验结果并形成草稿</button>}</div></div>}
      {selected.validation_errors.length > 0 && <div role="alert" className="market-detail"><h3>待修正问题</h3>{selected.validation_errors.map((item, index) => <p className="danger" key={index}>{typeof item === 'string' ? item : item.message || item.code}</p>)}</div>}
      <details style={{ marginTop: 12 }}><summary>冻结来源与接受目标</summary><p>账户修订 {selected.source.account_revision ?? '不涉及账户'} · 目标版本 {selected.kind === 'review_scores' ? '见各评分对象冻结版本' : targetRevision}</p><pre style={plain}>{pretty(selected.source)}</pre></details>
      {editable && !draft && <button className="button secondary" type="button" disabled={locked} onClick={() => { setDraft(emptyOutput(selected.kind, selectedTarget, selected.source.score_targets)); setDirty(true) }}>建立人工修正草稿</button>}
      {draft && <form className="form" onSubmit={saveDraft} style={{ marginTop: 18 }}><OutputEditor kind={selected.kind} value={draft} onChange={value => { setDraft(value); setDirty(true); setPending(null) }} disabled={locked || !editable} candidates={selected.source.breakout_candidates || []} scoreTargets={selected.source.score_targets || []} />{editable && <button className="button secondary" disabled={locked || !dirty}>保存人工修正</button>}</form>}
      {selected.kind === 'review_scores' && <p className="muted">接受后保存 AI 建议与依据。人工最终分在复盘评分页逐项确认；评分总结单独保留在本记录。</p>}{selected.kind === 'ocr_trades' && <p className="muted">接受成交识别会生成待确认交易；持仓行及缺少成交事实的行不能写成交易。</p>}{selected.kind === 'ocr_assets' && <p className="muted">接受前请确认资产日期、金额与每行持仓。接受后将按冻结目标版本写入资产快照。</p>}{selected.kind === 'review_draft' && <p className="muted">接受时仅替换所选复盘栏目。若目标文字或账户数据已改变，会要求重新生成；人工评分保持独立。</p>}
      <div className="form-actions" style={{ marginTop: 16 }}><button className="button secondary" type="button" disabled={locked} onClick={() => open(selected)}>载入记录最新版本</button>{selected.status === 'draft' && <button className="button primary" type="button" disabled={locked || dirty} onClick={() => setPending('accept')}>{selected.kind === 'review_scores' ? '保存 AI 评分建议' : selected.kind === 'ocr_trades' ? '接受为待确认交易' : selected.kind === 'ocr_assets' ? '接受为资产快照' : '接受本次草稿'}</button>}{['draft', 'invalid_output'].includes(selected.status) && <button className="button secondary" type="button" disabled={locked || dirty} onClick={() => setPending('reject')}>拒绝草稿</button>}{selected.status === 'accepted' && !(selected.kind === 'ocr_assets' && selected.acceptance?.before === null) && <button className="button secondary" type="button" disabled={locked} onClick={() => setPending('retract')}>撤回接受</button>}<button className="button secondary danger" type="button" disabled={locked || selected.status === 'accepted' || run?.status === 'queued'} onClick={() => setPending('delete')}>删除生成记录</button><button className="button secondary" type="button" disabled={locked} onClick={async () => { setBusy(true); try { setAudit(await api<Audit[]>(scoped(`/ai/generations/${selected.id}/audit`))) } catch (err) { report(err) } finally { setBusy(false) } }}>操作历史</button></div>
      {pending && <div className="market-detail"><p>{pending === 'accept' ? selected.kind === 'review_scores' ? '确认保存这些冻结对象的 AI 评分建议？各对象版本将逐一校验。' : selected.kind === 'stock_analysis' ? '确认接受为研究结论记录？' : `确认接受此草稿并写入上述目标？目标版本为 ${targetRevision}。` : pending === 'retract' ? '确认撤回本次接受？已经被后续人工修改的记录需要重新核对。' : pending === 'reject' ? '确认拒绝此草稿？' : '确认删除这条生成记录？'}</p><div className="form-actions"><button className="button primary" type="button" disabled={locked} onClick={act}>确认{pending === 'accept' ? '接受' : pending === 'retract' ? '撤回' : pending === 'reject' ? '拒绝' : '删除'}</button><button className="button secondary" type="button" disabled={locked} onClick={() => setPending(null)}>取消</button></div></div>}
      {selected.status === 'accepted' && selected.kind === 'ocr_assets' && selected.acceptance?.before === null && <p className="muted">已创建的确认资产快照需在资产页面人工修订。</p>}{selected.acceptance && <details style={{ marginTop: 12 }}><summary>接受记录</summary><pre style={plain}>{pretty(selected.acceptance)}</pre></details>}{audit && <details open style={{ marginTop: 12 }}><summary>操作历史 · {audit.length} 项</summary>{audit.map(item => <details key={item.id}><summary>{item.action} · v{item.revision} · {new Date(item.created_at).toLocaleString()}</summary><pre style={plain}>{pretty(item.snapshot)}</pre></details>)}</details>}
    </section>}
  </div>
}
