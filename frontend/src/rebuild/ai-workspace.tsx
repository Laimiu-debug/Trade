import { useEffect, useRef, useState, lazy, Suspense, type FormEvent } from 'react'
import { api, apiStream } from './api'
import { AIQuickPrompts } from './ai-quick-prompts'
import { useAIDraft, seedAIDraft, type ArtifactSource } from './ai-drafts'
import { AI_HANDOFF_EVENT, AI_ACTIVITY_EVENT, takeAIHandoff, aiPage, type AIHandoff, type AIActivity } from './ai-launcher'
import { Icon } from './workspace-icons'
const AIGenerationEditor = lazy(() => import('./ai-generations').then(module => ({ default: module.AIGenerationEditor })))

type Channel = 'text' | 'vision'
type Provider = { base_url: string; model: string; secret_ref: string; configured?: boolean; secret_configured?: boolean }
type Config = { revision: number; text: Provider; vision: Provider; temperature: number; max_tokens: number; timeout_seconds: number; updated_at?: string }
type ConfigDraft = { text: Provider; vision: Provider; temperature: string; max_tokens: string; timeout_seconds: string }
type Message = { id: string; run_id: string; role: 'user' | 'assistant'; content: string; status: string; created_at: string }
type Session = { id: string; title: string; account_id: string | null; revision: number; created_at: string; updated_at: string; messages?: Message[]; active_run_id?: string | null; message_count?: number; omitted_message_count?: number }
type TokenUsage = { prompt_tokens: number | null; completion_tokens: number | null; total_tokens: number | null }
type Run = { id: string; session_id: string | null; account_id: string | null; kind: 'chat' | 'connection_test' | 'stock_analysis' | 'review_draft' | 'ocr_trades' | 'ocr_assets' | 'review_scores'; channel: Channel; status: string; config_revision: number; model: string; provider: string; input_sha256: string; output: string; usage: TokenUsage; error_code: string | null; cancel_requested: boolean; created_at: string; updated_at: string; context?: unknown; request?: { messages: Array<{ role: string; content: string }>; template?: unknown; user_message?: string } }
type Preview = { messages: Array<{ role: string; content: string }>; context: unknown; config_revision: number; input_sha256: string; channel: Channel; model: string; provider: string; template: unknown; history_run_count: number }
type Template = { id: string; name: string; content: string; revision: number; readonly: boolean; scope: string; source?: unknown; strategy_id?: string }
type Usage = TokenUsage & { tokens_are_partial: boolean; call_count: number; completed_count: number; failed_count: number; unknown_usage_count: number; by_model: Array<TokenUsage & { tokens_are_partial: boolean; call_count: number; completed_count: number; failed_count: number; unknown_usage_count: number; model: string; provider: string }> }
type Dataset = { id: string; symbol: string; first_date: string; last_date: string; adjustment: string }
type ResearchRun = { id: string; strategy_id: string; dataset_id: string; decision_at: string; result: { signal: boolean | null; status: string; candidate?: { symbol?: string } | null; source_date?: string | null } }
type Artifact = ArtifactSource & { label: string; state: string; created_at: string }
type PromptBody = { message: string; channel: Channel; config_revision: number; context: { manual_text: string; dataset_ids: string[]; run_ids: string[]; strict: boolean; artifact_sources: ArtifactSource[]; decision_at?: string; account_date?: string }; template_id?: string; template_revision?: number }
const phaseNames: Record<string, string> = { queued: '已冻结，待执行', pending: '等待处理', running: '生成中', completed: '已完成', complete: '已完成', failed: '失败', cancelled: '已停止', interrupted: '响应中断' }
const token = (value: number | null | undefined) => value == null ? '未知' : value.toLocaleString()
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
const plainStyle = { whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, margin: '8px 0', fontFamily: 'inherit', lineHeight: 1.7 }
const livePhase = (status?: string) => status === 'queued' || status === 'running' || status === 'pending'
const initialConfig = (): ConfigDraft => ({ text: { base_url: '', model: '', secret_ref: '' }, vision: { base_url: '', model: '', secret_ref: '' }, temperature: '0.7', max_tokens: '2048', timeout_seconds: '60' })
const configDraft = (value: Config): ConfigDraft => ({ text: { base_url: value.text.base_url, model: value.text.model, secret_ref: value.text.secret_ref }, vision: { base_url: value.vision.base_url, model: value.vision.model, secret_ref: value.vision.secret_ref }, temperature: String(value.temperature), max_tokens: String(value.max_tokens), timeout_seconds: String(value.timeout_seconds) })

function RunStatus({ run }: { run: Run }) {
  return <div><p><strong>{phaseNames[run.status] || run.status}</strong> · {run.model || '未配置模型'}{run.cancel_requested && livePhase(run.status) && ' · 正在停止'}</p><p className="muted">输入 token {token(run.usage?.prompt_tokens)} · 输出 token {token(run.usage?.completion_tokens)} · 合计 {token(run.usage?.total_tokens)}</p>{run.error_code && <p className="danger">错误代码：{run.error_code}</p>}</div>
}

export function AIWorkspace({ accountId, accountKind, onActivityChange }: { accountId?: string; accountKind?: string; onActivityChange?: (state: AIActivity) => void }) {
  const [panel, setPanel] = useState<'chat' | 'generations'>('chat')
  const [generationsOpened, setGenerationsOpened] = useState(false)
  const [generationBusy, setGenerationBusy] = useState(false)
  const [scope, setScope] = useState('')
  const [config, setConfig] = useState<Config | null>(null)
  const [settings, setSettings] = useState<ConfigDraft>(initialConfig)
  const [configDirty, setConfigDirty] = useState(false)
  const [sessions, setSessions] = useState<Session[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const draft = useAIDraft(scope, session && (session.account_id || '') === scope ? session.id : null)
  const { message, channel, manual, datasetIds, runIds, decisionAt, accountDate, strict, templateId, artifactSources } = draft.data
  const setMessage = draft.field('message'), setChannel = draft.field('channel'), setManual = draft.field('manual')
  const setDatasetIds = draft.field('datasetIds'), setRunIds = draft.field('runIds'), setDecisionAt = draft.field('decisionAt')
  const setAccountDate = draft.field('accountDate'), setStrict = draft.field('strict'), setTemplateId = draft.field('templateId'), setArtifactSources = draft.field('artifactSources')
  const [handoff, setHandoff] = useState<AIHandoff | null>(null)
  const [handoffAccount, setHandoffAccount] = useState(false)
  const [afterScope, setAfterScope] = useState<AIHandoff | null>(null)
  const [quickQuestion, setQuickQuestion] = useState<string | null>(null)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [sessionTitle, setSessionTitle] = useState('新的研究对话')
  const [deleteSession, setDeleteSession] = useState<Session | null>(null)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [research, setResearch] = useState<ResearchRun[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [templateEditing, setTemplateEditing] = useState<Template | null>(null)
  const [templateName, setTemplateName] = useState('')
  const [templateContent, setTemplateContent] = useState('')
  const [deleteTemplate, setDeleteTemplate] = useState(false)
  const [contextSearch, setContextSearch] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewBody, setPreviewBody] = useState<PromptBody | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [testRun, setTestRun] = useState<Run | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [calls, setCalls] = useState<Run[]>([])
  const [callDetail, setCallDetail] = useState<Run | null>(null)
  const [busy, setBusy] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const streamController = useRef<AbortController | null>(null)
  const selectedSession = useRef<string | null>(null)
  const mounted = useRef(true)
  const scoped = (path: string, value = scope) => value ? `${path}${path.includes('?') ? '&' : '?'}account_id=${encodeURIComponent(value)}` : path
  const activeCall = [run, testRun].find(item => item?.status === 'running' || item?.status === 'pending')
  const locked = busy || streaming || Boolean(activeCall) || generationBusy
  const activityCallback = useRef(onActivityChange)
  activityCallback.current = onActivityChange
  const isActive = streaming || Boolean(activeCall) || generationBusy
  const activityAccount = generationBusy ? accountId || null : activeCall?.account_id || null
  const activityRun = activeCall?.id || null
  useEffect(() => {
    const value: AIActivity = { running: isActive, accountId: activityAccount, runId: activityRun }
    activityCallback.current?.(value)
    window.dispatchEvent(new CustomEvent(AI_ACTIVITY_EVENT, { detail: value }))
  }, [isActive, activityAccount, activityRun])
  useEffect(() => () => {
    const value: AIActivity = { running: false, accountId: null, runId: null }
    activityCallback.current?.(value)
    window.dispatchEvent(new CustomEvent(AI_ACTIVITY_EVENT, { detail: value }))
  }, [])
  useEffect(() => {
    const receive = () => {
      const value = takeAIHandoff()
      if (!value || typeof value.page !== 'string') return
      setHandoff(value); setHandoffAccount(false)
    }
    window.addEventListener(AI_HANDOFF_EVENT, receive); receive()
    return () => window.removeEventListener(AI_HANDOFF_EVENT, receive)
  }, [])
  function mergeHandoff(value: AIHandoff) {
    if (locked || draft.conflict) return
    const nextDatasets = [...new Set([...draft.data.datasetIds, ...(value.datasetId ? [value.datasetId] : [])])]
    const nextRuns = [...new Set([...draft.data.runIds, ...(value.researchRunId ? [value.researchRunId] : [])])]
    const nextArtifacts = [...draft.data.artifactSources]
    if (value.artifact && !nextArtifacts.some(item => item.type === value.artifact?.type && item.id === value.artifact.id)) nextArtifacts.push(value.artifact)
    if (nextDatasets.length > 5 || nextRuns.length > 10 || nextArtifacts.length > 3) { setError('加入后超过上下文数量上限，请先移除部分来源。'); return }
    draft.setData(current => ({ ...current, page: aiPage(value.page), datasetIds: nextDatasets, runIds: nextRuns, artifactSources: nextArtifacts }))
    setHandoff(null); setAfterScope(null); setPanel('chat'); setNotice('所选来源已加入草稿；请核对截止时间并预览。尚未发送。')
  }
  function adoptHandoff() {
    if (!handoff || locked) return
    if (handoffAccount && handoff.accountId && handoff.accountId !== scope) {
      setAfterScope(handoff); setScope(handoff.accountId); return
    }
    mergeHandoff(handoff)
  }
  useEffect(() => {
    if (afterScope && scope === afterScope.accountId && session === null && !locked) mergeHandoff(afterScope)
  }, [afterScope, scope, session, locked])

  useEffect(() => {
    mounted.current = true
    let live = true
    Promise.allSettled([api<Config>('/ai/config'), api<Template[]>('/ai/templates'), api<Dataset[]>('/market/datasets'), api<ResearchRun[]>('/research/runs'), api<Artifact[]>('/ai/context-sources')]).then(results => {
      if (!live) return
      const [configuration, prompts, market, runs, extraSources] = results
      if (configuration.status === 'fulfilled') { setConfig(configuration.value); setSettings(configDraft(configuration.value)) }
      if (prompts.status === 'fulfilled') setTemplates(prompts.value)
      if (market.status === 'fulfilled') setDatasets(market.value)
      if (runs.status === 'fulfilled') setResearch(runs.value)
      if (extraSources.status === 'fulfilled') setArtifacts(extraSources.value)
      const failures = results.filter(item => item.status === 'rejected').map(item => item.reason instanceof Error ? item.reason.message : 'AI 工作台读取失败')
      if (failures.length) setError(failures.join('；'))
    })
    return () => { live = false; mounted.current = false; streamController.current?.abort() }
  }, [])

  useEffect(() => {
    let live = true
    selectedSession.current = null
    setSession(null); setRun(null); setPreview(null); setPreviewBody(null); setDeleteSession(null); setSessions([]); setUsage(null); setCalls([]); setCallDetail(null)
    Promise.all([api<Session[]>(scoped('/ai/sessions')), api<Usage>(scoped('/ai/usage')), api<Run[]>(scoped('/ai/runs'))]).then(([items, summary, history]) => {
      if (live) { setSessions(items); setUsage(summary); setCalls(history) }
    }).catch(err => { if (live) setError(err instanceof Error ? err.message : '会话读取失败') })
    return () => { live = false }
  }, [scope])

  useEffect(() => { setPreview(null); setPreviewBody(null) }, [message, channel, manual, datasetIds, runIds, decisionAt, accountDate, strict, templateId, artifactSources, draft.conflict])

  useEffect(() => {
    if (!run || !livePhase(run.status) || run.status === 'queued' || streaming) return
    let live = true, inFlight = false
    const refresh = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const next = await api<Run>(scoped(`/ai/runs/${encodeURIComponent(run.id)}`, run.account_id || ''))
        if (!live) return
        if (!livePhase(next.status) && next.session_id && selectedSession.current === next.session_id) {
          const nextSession = await api<Session>(scoped(`/ai/sessions/${encodeURIComponent(next.session_id)}`, next.account_id || ''))
          if (live) { setSession(nextSession); setSessions(current => [nextSession, ...current.filter(item => item.id !== nextSession.id)]) }
        }
        if (live) setRun(next)
      } catch (err) { if (live) setError(err instanceof Error ? err.message : '响应状态读取失败') }
      finally { inFlight = false }
    }
    const timer = window.setInterval(refresh, 2000)
    return () => { live = false; window.clearInterval(timer) }
  }, [run?.id, run?.status, streaming])

  useEffect(() => {
    if (!testRun || testRun.status !== 'running' || streaming) return
    let live = true, inFlight = false
    const timer = window.setInterval(async () => {
      if (inFlight) return
      inFlight = true
      try {
        const next = await api<Run>(scoped(`/ai/runs/${encodeURIComponent(testRun.id)}`, testRun.account_id || ''))
        if (live) setTestRun(next)
      } catch (err) { if (live) setError(err instanceof Error ? err.message : '连接测试状态读取失败') }
      finally { inFlight = false }
    }, 2000)
    return () => { live = false; window.clearInterval(timer) }
  }, [testRun?.id, testRun?.status, streaming])

  async function report(err: unknown) {
    if (!mounted.current) return
    const caught = err as Error & { code?: string }
    setError(caught.message || 'AI 操作失败')
    if (caught.code === 'AI_CONTEXT_CHANGED') { setPreview(null); setPreviewBody(null); setNotice('上下文已改变，请重新预览后发送。输入内容已保留。') }
    if (caught.code?.includes('REVISION') || caught.code?.includes('CONFLICT')) setNotice('输入内容已保留，请载入最新版本后核对修改。')
  }

  async function reloadLists() {
    setBusy(true); setError('')
    try {
      const [nextSessions, summary, market, runs, prompts, history, extraSources] = await Promise.all([api<Session[]>(scoped('/ai/sessions')), api<Usage>(scoped('/ai/usage')), api<Dataset[]>('/market/datasets'), api<ResearchRun[]>('/research/runs'), api<Template[]>('/ai/templates'), api<Run[]>(scoped('/ai/runs')), api<Artifact[]>('/ai/context-sources')])
      setArtifacts(extraSources)
      setSessions(nextSessions); setUsage(summary); setDatasets(market); setResearch(runs); setTemplates(prompts); setCalls(history)
      setPreview(null); setPreviewBody(null)
      setNotice('会话、来源、模板与用量已刷新。')
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function saveConfig(event: FormEvent) {
    event.preventDefault(); if (!config) return
    setBusy(true); setError(''); setNotice('')
    try {
      for (const [key, label, min, max, integer] of [['temperature', '温度', 0, 2, false], ['max_tokens', '最大输出 token', 16, 8192, true], ['timeout_seconds', '超时秒数', 5, 120, true]] as const) {
        const value = Number(settings[key])
        if (!settings[key].trim() || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) throw new Error(`${label}须为 ${min} 至 ${max} ${integer ? '的整数' : '之间的数值'}`)
      }
      const next = await api<Config>('/ai/config', 'PUT', { expected_revision: config.revision, text: settings.text, vision: settings.vision, temperature: Number(settings.temperature), max_tokens: Number(settings.max_tokens), timeout_seconds: Number(settings.timeout_seconds) })
      setConfig(next); setSettings(configDraft(next)); setConfigDirty(false); setPreview(null); setPreviewBody(null)
      setNotice('模型配置已保存，连接测试由你主动执行。')
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function reloadConfig() {
    if (configDirty && !window.confirm('重新载入配置将替换当前未保存的输入，确认载入？')) return
    setBusy(true); setError('')
    try { const next = await api<Config>('/ai/config'); setConfig(next); setSettings(configDraft(next)); setConfigDirty(false); setPreview(null); setPreviewBody(null) }
    catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function openSession(id: string) {
    setBusy(true); setError(''); setPreview(null); setPreviewBody(null); setDeleteSession(null)
    try {
      const next = await api<Session>(scoped(`/ai/sessions/${encodeURIComponent(id)}`))
      selectedSession.current = next.id; setSession(next); setRun(null)
      if (next.active_run_id) setRun(await api<Run>(scoped(`/ai/runs/${encodeURIComponent(next.active_run_id)}`)))
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function createSession(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const next = await api<Session>('/ai/sessions', 'POST', { title: sessionTitle, ...(scope ? { account_id: scope } : {}) })
      seedAIDraft(scope, next.id, draft.data)
      setSessions(current => [next, ...current]); setSession(next); selectedSession.current = next.id; setRun(null); setPreview(null); setPreviewBody(null)
      setNotice(scope ? '会话已绑定所选账户。选择日期后会加入账户事实，账户标识始终随绑定会话携带。' : '已创建不绑定账户的会话。')
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function removeSession() {
    if (!deleteSession) return
    setBusy(true); setError('')
    try {
      await api(scoped(`/ai/sessions/${encodeURIComponent(deleteSession.id)}?expected_revision=${deleteSession.revision}`), 'DELETE')
      setSessions(current => current.filter(item => item.id !== deleteSession.id))
      if (session?.id === deleteSession.id) { setSession(null); selectedSession.current = null; setRun(null); setPreview(null); setPreviewBody(null) }
      setDeleteSession(null); setNotice('会话已删除。')
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  function promptBody(): PromptBody {
    if (!config) throw new Error('请先载入模型配置')
    if (!message.trim()) throw new Error('请填写本次问题')
    if (draft.conflict) throw new Error('请先处理另一页面的本机草稿冲突')
    if ((datasetIds.length || artifactSources.length) && !decisionAt) throw new Error('选择行情时须填写明确的数据截止时间')
    const context: PromptBody['context'] = { manual_text: manual, dataset_ids: datasetIds, run_ids: runIds, artifact_sources: artifactSources, strict }
    if (decisionAt) context.decision_at = new Date(decisionAt).toISOString()
    if (scope && accountDate) context.account_date = accountDate
    const template = templates.find(item => item.id === templateId)
    return { message, channel, config_revision: config.revision, context, ...(template ? { template_id: template.id, template_revision: template.revision } : {}) }
  }

  async function previewPrompt(event: FormEvent) {
    event.preventDefault(); if (!session) return
    setBusy(true); setError(''); setNotice('')
    try {
      const body = promptBody()
      const next = await api<Preview>(scoped(`/ai/sessions/${encodeURIComponent(session.id)}/preview`), 'POST', body)
      setPreview(next); setPreviewBody(body); setNotice('本次提示词和来源已生成预览，尚未调用模型。')
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function execute(frozen: Run, testing = false) {
    const controller = new AbortController()
    streamController.current = controller
    setStreaming(true); setError('')
    const update = testing ? setTestRun : setRun
    update(frozen)
    try {
      await apiStream(scoped(`/ai/runs/${encodeURIComponent(frozen.id)}/stream`, frozen.account_id || ''), {}, event => {
        if (!mounted.current) return
        const data = event.data as { type?: string; content?: string; usage?: TokenUsage; run?: Run }
        const kind = event.event === 'message' ? data.type : event.event
        if (data.run) update(data.run)
        else if (kind === 'started') update(current => current ? { ...current, status: 'running' } : current)
        else if (kind === 'delta' && typeof data.content === 'string') update(current => current ? { ...current, status: 'running', output: current.output + data.content } : current)
        else if (kind === 'usage') update(current => current ? { ...current, usage: data.usage || data as unknown as TokenUsage } : current)
      }, controller.signal)
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) await report(err)
    } finally {
      if (mounted.current) {
        try {
          const final = await api<Run>(scoped(`/ai/runs/${encodeURIComponent(frozen.id)}`, frozen.account_id || ''))
          update(final)
          if (final.session_id && selectedSession.current === final.session_id) {
            const latest = await api<Session>(scoped(`/ai/sessions/${encodeURIComponent(final.session_id)}`, final.account_id || ''))
            setSession(latest); setSessions(current => [latest, ...current.filter(item => item.id !== latest.id)])
          }
          setUsage(await api<Usage>(scoped('/ai/usage')))
          setCalls(await api<Run[]>(scoped('/ai/runs')))
        } catch (err) { await report(err) }
        setStreaming(false)
      }
      if (streamController.current === controller) streamController.current = null
    }
  }

  async function send() {
    if (!session || !preview || !previewBody) return
    setBusy(true); setError(''); setNotice('')
    try {
      const frozen = await api<Run>(scoped(`/ai/sessions/${encodeURIComponent(session.id)}/runs`), 'POST', { ...previewBody, expected_input_sha256: preview.input_sha256 })
      setRun(frozen); setPreview(null); setPreviewBody(null); setMessage('')
      const latest = await api<Session>(scoped(`/ai/sessions/${encodeURIComponent(session.id)}`))
      setSession(latest); setSessions(current => [latest, ...current.filter(item => item.id !== latest.id)])
      setBusy(false)
      await execute(frozen)
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function testConnection(testChannel: Channel) {
    if (!config) return
    setBusy(true); setError(''); setNotice('')
    try {
      const frozen = await api<Run>('/ai/test-connection', 'POST', { channel: testChannel, config_revision: config.revision })
      setTestRun(frozen); setBusy(false)
      await execute(frozen, true)
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function stop(active: Run) {
    setError('')
    try {
      const next = await api<Run>(scoped(`/ai/runs/${encodeURIComponent(active.id)}/cancel`, active.account_id || ''), 'POST', {})
      if (active.kind === 'connection_test') setTestRun(next); else setRun(next)
      streamController.current?.abort(); setNotice('已请求停止，已接收的内容将保留，并标明未完成状态。')
    } catch (err) { await report(err) }
  }

  function editTemplate(value: Template | null) {
    setTemplateEditing(value); setTemplateName(value?.name || ''); setTemplateContent(value?.content || ''); setDeleteTemplate(false)
  }

  async function reloadTemplate() {
    if (!templateEditing || !window.confirm('重新载入提示词将替换当前未保存的正文，确认载入？')) return
    setBusy(true); setError('')
    try {
      const latest = await api<Template[]>('/ai/templates')
      setTemplates(latest)
      const item = latest.find(value => value.id === templateEditing.id)
      if (!item) throw new Error('该提示词已被删除；当前草稿已保留，可复制为新的本地提示词。')
      editTemplate(item)
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function saveTemplate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const next = await api<Template>(templateEditing ? `/ai/templates/${encodeURIComponent(templateEditing.id)}` : '/ai/templates', templateEditing ? 'PUT' : 'POST', { name: templateName, content: templateContent, ...(templateEditing ? { expected_revision: templateEditing.revision } : {}) })
      setTemplates(current => [next, ...current.filter(item => item.id !== next.id)]); editTemplate(next); setPreview(null); setPreviewBody(null); setNotice('本地提示词已保存。')
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  async function removeTemplate() {
    if (!templateEditing) return
    setBusy(true); setError('')
    try {
      await api(`/ai/templates/${encodeURIComponent(templateEditing.id)}?expected_revision=${templateEditing.revision}`, 'DELETE')
      setTemplates(current => current.filter(item => item.id !== templateEditing.id)); if (templateId === templateEditing.id) setTemplateId('')
      editTemplate(null); setNotice('本地提示词已删除。')
    } catch (err) { await report(err) }
    finally { setBusy(false) }
  }

  const matchingDatasets = datasets.filter(item => `${item.symbol} ${item.id}`.toLowerCase().includes(contextSearch.toLowerCase()))
  const matchingResearch = research.filter(item => `${item.result.candidate?.symbol || ''} ${item.strategy_id} ${item.id}`.toLowerCase().includes(contextSearch.toLowerCase()))
  const previewTemplate = templates.find(item => item.id === templateId)
  const running = [run, testRun].find(item => item && livePhase(item.status))
  const configMissing = config && (!config[channel].configured || Boolean(config[channel].secret_ref && !config[channel].secret_configured))

  return <div>
    <div className="research-tabs" role="tablist" aria-label="AI 工作台页签"><button type="button" role="tab" aria-selected={panel === 'chat'} className={panel === 'chat' ? 'button primary' : 'button secondary'} disabled={locked || generationBusy || run?.status === 'running' || testRun?.status === 'running'} onClick={() => setPanel('chat')}>对话与配置</button><button type="button" role="tab" aria-selected={panel === 'generations'} className={panel === 'generations' ? 'button primary' : 'button secondary'} disabled={locked || generationBusy || run?.status === 'running' || testRun?.status === 'running'} onClick={() => { setGenerationsOpened(true); setPanel('generations') }}>识别与复盘</button></div>
    {handoff && <section className="card"><h3>待加入的页面上下文</h3><p>{handoff.label || aiPage(handoff.page)} · 行情 {handoff.datasetId || '未选择'} · 研究 {handoff.researchRunId || '未选择'}{handoff.artifact && ` · ${handoff.artifact.type} ${handoff.artifact.id}`}</p><p className="muted">只加入明确选择的来源，问题和背景保留。切换账户范围时载入该账户自己的草稿。</p>{handoff.accountId && <label className="check-field"><input type="checkbox" checked={handoffAccount} disabled={locked} onChange={event => setHandoffAccount(event.target.checked)} />同时绑定来源账户 {handoff.accountId.slice(0, 8)}</label>}<div className="form-actions"><button className="button primary" disabled={locked || Boolean(draft.conflict)} onClick={adoptHandoff}>加入草稿，稍后预览</button><button className="button secondary" onClick={() => { setHandoff(null); setAfterScope(null) }}>忽略此次上下文</button></div>{locked && <p className="muted">当前任务结束或明确停止后可加入。</p>}</section>}
    <div hidden={panel !== 'chat'}>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    {draft.error && <p className="alert error" role="alert">{draft.error}</p>}
    {draft.conflict && <section className="card"><h3>另一页面修改了本机 AI 草稿</h3><p>当前输入已保留。请选择采用另一页面，或以当前输入继续编辑。</p><div className="form-actions"><button className="button secondary" disabled={locked} onClick={() => draft.resolve(true)}>采用另一页面草稿</button><button className="button secondary" disabled={locked} onClick={() => draft.resolve(false)}>保留当前输入继续编辑</button></div></section>}
    <AIQuickPrompts page={draft.data.page} disabled={locked} onUse={setQuickQuestion} />
    {quickQuestion && <section className="card"><h3>快捷问题草稿</h3><pre style={plainStyle}>{quickQuestion}</pre><div className="form-actions"><button className="button secondary" disabled={locked || Boolean(draft.conflict) || (message.length + quickQuestion.length + 2 > 12000)} onClick={() => { setMessage(current => current ? `${current}\n\n${quickQuestion}` : quickQuestion); setQuickQuestion(null) }}>追加到问题</button><button className="button secondary" disabled={locked || Boolean(draft.conflict)} onClick={() => { setMessage(quickQuestion); setQuickQuestion(null) }}>用此问题替换输入</button><button className="button secondary" onClick={() => setQuickQuestion(null)}><Icon name="close" />取消</button></div>{!session && <p className="muted">问题已保存在当前账户范围的本机草稿，创建会话后可继续编辑。</p>}</section>}
    <section className="card"><details><summary><strong>全局模型配置 · 文本 / 视觉</strong>{config ? ` · 版本 ${config.revision}` : ' · 载入中'}</summary><p className="muted">保存环境变量引用后可主动测试连接。密钥由服务运行环境提供，请填写 TRADE_AI_ 开头的环境变量名称。显式本地接口允许不引用密钥。</p>
      <form className="form" noValidate onSubmit={saveConfig}><div className="two-col">{(['text', 'vision'] as const).map(key => <div key={key}><h3>{key === 'text' ? '文本模型' : '视觉模型'}</h3><p className="muted">模型 {config?.[key].configured ? '已配置' : '未配置'} · 环境密钥 {config?.[key].secret_configured ? '可用' : config?.[key].secret_ref ? '未配置' : '未引用'}</p>{(['base_url', 'model', 'secret_ref'] as const).map(field => <label className="field" key={field} style={{ marginBottom: 10 }}><span>{field === 'base_url' ? '兼容接口基础地址' : field === 'model' ? '模型名称' : '密钥环境变量引用'}</span><input disabled={locked} type="text" autoComplete="off" maxLength={field === 'base_url' ? 2048 : field === 'model' ? 128 : 89} placeholder={field === 'secret_ref' ? key === 'text' ? 'TRADE_AI_TEXT_KEY' : 'TRADE_AI_VISION_KEY' : undefined} value={settings[key][field]} onChange={event => { setSettings({ ...settings, [key]: { ...settings[key], [field]: event.target.value } }); setConfigDirty(true) }} /></label>)}<button className="button secondary" type="button" disabled={locked || !config || configDirty} onClick={() => testConnection(key)}>测试{key === 'text' ? '文本' : '视觉'}模型连接</button></div>)}</div>
        <div className="form-grid"><label className="field"><span>温度（0–2）</span><input type="number" step="0.1" min="0" max="2" disabled={locked} value={settings.temperature} onChange={event => { setSettings({ ...settings, temperature: event.target.value }); setConfigDirty(true) }} /></label><label className="field"><span>最大输出 token（16–8192）</span><input type="number" min="16" max="8192" step="1" disabled={locked} value={settings.max_tokens} onChange={event => { setSettings({ ...settings, max_tokens: event.target.value }); setConfigDirty(true) }} /></label><label className="field"><span>请求超时（5–120 秒）</span><input type="number" min="5" max="120" step="1" disabled={locked} value={settings.timeout_seconds} onChange={event => { setSettings({ ...settings, timeout_seconds: event.target.value }); setConfigDirty(true) }} /></label></div><div className="form-actions"><button className="button primary" disabled={locked || !config}><Icon name="save" />保存模型配置</button><button className="button secondary" type="button" disabled={locked} onClick={reloadConfig}>载入最新配置</button></div></form>
      <p className="muted">视觉配置支持独立连接测试和文本对话；图像输入将在图片分析功能中提供。</p>{testRun && <div className="market-detail"><RunStatus run={testRun} />{testRun.output && <pre style={plainStyle}>{testRun.output}</pre>}{livePhase(testRun.status) && <button className="button secondary" type="button" onClick={() => stop(testRun)}>停止连接测试</button>}</div>}
    </details></section>
    <section className="card"><h2 className="title-with-icon"><Icon name="bot" />会话与上下文范围</h2><div className="form-grid"><label className="field"><span>账户范围</span><select aria-label="账户范围" disabled={locked} value={scope} onChange={event => setScope(event.target.value)}><option value="">不绑定账户</option>{accountId && <option value={accountId}>当前{accountKind === 'sim' ? '模拟' : '真实'}账户 · {accountId.slice(0, 8)}</option>}{scope && scope !== accountId && <option value={scope}>先前选择账户 · {scope.slice(0, 8)}</option>}</select></label><label className="field"><span>已有会话</span><select aria-label="已有会话" disabled={locked} value={session?.id || ''} onChange={event => { if (event.target.value) openSession(event.target.value) }}><option value="">选择会话</option>{sessions.map(item => <option key={item.id} value={item.id}>{item.title} · {new Date(item.updated_at).toLocaleDateString()}</option>)}</select></label></div><form className="form" onSubmit={createSession} style={{ marginTop: 16 }}><label className="field"><span>新会话标题</span><input disabled={locked} maxLength={128} value={sessionTitle} onChange={event => setSessionTitle(event.target.value)} /></label><div className="form-actions"><button className="button secondary" disabled={locked}><Icon name="add" />新建会话</button><button className="button secondary" type="button" disabled={locked} onClick={reloadLists}><Icon name="refresh" />刷新来源与记录</button>{session && <><button className="button secondary" type="button" disabled={locked} onClick={() => openSession(session.id)}><Icon name="refresh" />重新载入会话</button><button className="button secondary danger" type="button" disabled={locked || livePhase(run?.status)} onClick={() => setDeleteSession(session)}><Icon name="delete" />删除会话</button></>}</div></form>{deleteSession && <div className="market-detail"><p>确认删除会话“{deleteSession.title}”？</p><div className="form-actions"><button className="button secondary danger" disabled={locked} type="button" onClick={removeSession}><Icon name="check" />确认删除会话</button><button className="button secondary" disabled={locked} type="button" onClick={() => setDeleteSession(null)}><Icon name="close" />取消</button></div></div>}</section>
    {session && <section className="card"><h2>{session.title}</h2><p className="muted">{session.account_id ? `绑定账户 ${session.account_id}` : '未绑定账户'} · 最近完整对话会作为上下文，实际携带的历史与资料可在发送前预览。</p>
      {Boolean(session.omitted_message_count) && <p className="muted">当前展示最近消息，另有 {session.omitted_message_count} 条未在此列表载入。历史调用可从下方记录查看。</p>}<div aria-label="会话消息" style={{ maxHeight: 540, overflowY: 'auto' }}>{session.messages?.map(item => <article key={item.id} style={{ borderBottom: '1px solid var(--border-subtle)', padding: '12px 0' }}><strong>{item.role === 'user' ? '你' : 'AI'} · {phaseNames[item.status] || item.status}</strong><pre style={plainStyle}>{item.content || (item.role === 'assistant' ? '尚未收到响应内容' : '')}</pre>{item.run_id && <button className="link-button" type="button" disabled={locked || livePhase(run?.status)} onClick={async () => { setBusy(true); try { setRun(await api<Run>(scoped(`/ai/runs/${encodeURIComponent(item.run_id)}`))) } catch (err) { await report(err) } finally { setBusy(false) } }}>查看调用记录</button>}</article>)}</div>
      {run && <div className="market-detail"><RunStatus run={run} />{run.output && <pre aria-live={streaming ? 'polite' : 'off'} style={plainStyle}>{run.output}</pre>}<details><summary>本次冻结来源与提示词</summary><p className="muted" style={{ overflowWrap: 'anywhere' }}>输入摘要：{run.input_sha256} · 配置版本 {run.config_revision}</p><pre style={plainStyle}>{pretty({ context: run.context, messages: run.request?.messages, template: run.request?.template })}</pre></details>{!livePhase(run.status) && run.request?.user_message && <button className="button secondary" type="button" disabled={locked} onClick={() => setMessage(run.request!.user_message!)}><Icon name="copy" />复制问题到输入区</button>}{run.status === 'queued' && <button className="button primary" type="button" disabled={locked} onClick={() => execute(run)}>执行已冻结请求</button>}{livePhase(run.status) && <button className="button secondary" type="button" onClick={() => stop(run)}>停止生成</button>}</div>}
      <form className="form" onSubmit={previewPrompt} style={{ marginTop: 20 }}><div className="form-grid"><label className="field"><span>任务模型</span><select disabled={locked || Boolean(running)} value={channel} onChange={event => setChannel(event.target.value as Channel)}><option value="text">文本模型</option><option value="vision">视觉模型（文本输入）</option></select></label><label className="field"><span>本地提示词 / 策略 playbook</span><select aria-label="本地提示词 / 策略 playbook" disabled={locked || Boolean(running)} value={templateId} onChange={event => setTemplateId(event.target.value)}><option value="">不使用模板</option>{templates.map(item => <option key={item.id} value={item.id}>{item.readonly ? '策略' : '自定义'} · {item.name} · v{item.revision}</option>)}</select></label></div>{previewTemplate && <details><summary>查看所选提示词</summary><pre style={plainStyle}>{previewTemplate.content}</pre></details>}
        <label className="field"><span>本次问题</span><textarea aria-label="本次问题" disabled={locked || Boolean(running)} maxLength={12000} value={message} onChange={event => setMessage(event.target.value)} style={{ minHeight: 120 }} /></label>
        <details><summary>选择本次携带的上下文 · 行情 {datasetIds.length} / 5 项 · 研究 {runIds.length} / 10 项 · 额外产物 {artifactSources.length} / 3 项</summary><div className="form-grid" style={{ marginTop: 12 }}><label className="field"><span>数据截止时间（行情 / 额外产物必填，浏览器本地时区）</span><input type="datetime-local" disabled={locked || Boolean(running)} value={decisionAt} onChange={event => setDecisionAt(event.target.value)} /></label>{scope && <label className="field"><span>账户观察日（留空仅附账户标识）</span><input type="date" disabled={locked || Boolean(running)} value={accountDate} onChange={event => setAccountDate(event.target.value)} /></label>}<label className="check-field"><input type="checkbox" disabled={locked || Boolean(running)} checked={strict} onChange={event => setStrict(event.target.checked)} />严格检查行情可得时间</label></div><label className="field" style={{ margin: '12px 0' }}><span>查找行情与研究来源</span><input type="search" value={contextSearch} onChange={event => setContextSearch(event.target.value)} /></label>
          <div className="two-col"><div className="table-wrap" style={{ maxHeight: 240, overflowY: 'auto' }}><table><thead><tr><th>选择行情</th><th>证券 / 日期</th></tr></thead><tbody>{matchingDatasets.map(item => <tr key={item.id}><td><input type="checkbox" aria-label={`携带行情 ${item.symbol} ${item.id.slice(0, 8)}`} disabled={locked || Boolean(running) || (!datasetIds.includes(item.id) && datasetIds.length >= 5)} checked={datasetIds.includes(item.id)} onChange={event => setDatasetIds(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} /></td><td>{item.symbol} · {item.adjustment}<br /><small>{item.first_date} 至 {item.last_date} · {item.id.slice(0, 8)}</small></td></tr>)}</tbody></table></div><div className="table-wrap" style={{ maxHeight: 240, overflowY: 'auto' }}><table><thead><tr><th>选择研究</th><th>策略 / 日期</th></tr></thead><tbody>{matchingResearch.map(item => <tr key={item.id}><td><input type="checkbox" aria-label={`携带研究 ${item.id.slice(0, 8)}`} disabled={locked || Boolean(running) || (!runIds.includes(item.id) && runIds.length >= 10)} checked={runIds.includes(item.id)} onChange={event => setRunIds(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} /></td><td>{item.result.candidate?.symbol || '—'} · {item.strategy_id}<br /><small>{item.result.source_date || item.decision_at} · {item.id.slice(0, 8)}</small></td></tr>)}</tbody></table></div></div>
          <h3>已保存的估值与回测产物</h3><p className="muted">最多 3 项；只发送摘要、末尾 20 笔成交和 10 个权益点，并标明省略量。回测是事后计算，不能证明当时已知选参结论；估值假设以记录创建时间为准。</p><div className="table-wrap" style={{ maxHeight: 240, overflowY: 'auto' }}><table><thead><tr><th>选择</th><th>类型 / 来源</th><th>状态</th></tr></thead><tbody>{artifacts.filter(item => `${item.label} ${item.id}`.toLowerCase().includes(contextSearch.toLowerCase())).map(item => { const checked = artifactSources.some(value => value.type === item.type && value.id === item.id); return <tr key={`${item.type}:${item.id}`}><td><input type="checkbox" aria-label={`携带产物 ${item.label}`} checked={checked} disabled={locked || Boolean(running) || (!checked && (artifactSources.length >= 3 || item.state !== 'succeeded'))} onChange={event => setArtifactSources(current => event.target.checked ? [...current, { type: item.type, id: item.id }] : current.filter(value => value.type !== item.type || value.id !== item.id))} /></td><td>{{valuation:'情景估值',backtest:'单股回测',portfolio:'组合回测'}[item.type]} · {item.label}<br /><small>{item.created_at} · {item.id.slice(0, 8)}</small></td><td>{item.state}</td></tr> })}</tbody></table></div>{artifactSources.map(item => <p key={`${item.type}:${item.id}`} className="muted">已选 {item.type} · {item.id} <button type="button" className="link-button" disabled={locked || Boolean(running)} onClick={() => setArtifactSources(current => current.filter(value => value.type !== item.type || value.id !== item.id))}>移除此产物</button></p>)}
          <label className="field" style={{ marginTop: 12 }}><span>手动补充的背景</span><textarea aria-label="手动补充的背景" disabled={locked || Boolean(running)} maxLength={12000} value={manual} onChange={event => setManual(event.target.value)} /></label></details>
        {configMissing && <p className="muted">当前模型或密钥引用尚未配置完成。可先编辑问题与本地预览，在配置完成后发送。</p>}
        <div className="form-actions"><button className="button secondary" disabled={locked || Boolean(running)}><Icon name="document" />预览本次提示词</button><button className="button primary" type="button" disabled={locked || Boolean(running) || !preview || !previewBody || configDirty || Boolean(configMissing)} onClick={send}><Icon name="check" />确认发送到所选模型</button></div>
      </form>
      {preview && <details open style={{ marginTop: 16 }}><summary>待发送预览 · {preview.model || '未配置模型'} · 配置版本 {preview.config_revision}</summary><p className="muted" style={{ overflowWrap: 'anywhere' }}>{preview.provider} · 输入摘要 {preview.input_sha256} · 携带 {preview.history_run_count} 轮已完成历史</p><details><summary>来源范围与版本</summary><pre style={plainStyle}>{pretty(preview.context)}</pre></details>{preview.messages.map((item, index) => <div key={index}><strong>{item.role}</strong><pre style={plainStyle}>{item.content}</pre></div>)}</details>}
    </section>}
    <section className="card"><details><summary><strong>提示词与本地覆盖</strong></summary><p className="muted">内置策略 playbook 只读；复制后可编辑为本地提示词，在对话中显式选择使用。</p><label className="field"><span>查看或编辑提示词</span><select aria-label="查看或编辑提示词" disabled={locked} value={templateEditing?.id || ''} onChange={event => editTemplate(templates.find(item => item.id === event.target.value) || null)}><option value="">新建本地提示词</option>{templates.map(item => <option key={item.id} value={item.id}>{item.readonly ? '内置' : '自定义'} · {item.name} · v{item.revision}</option>)}</select></label><form className="form" onSubmit={saveTemplate} style={{ marginTop: 12 }}><label className="field"><span>提示词名称</span><input aria-label="提示词名称" maxLength={128} disabled={locked || templateEditing?.readonly} value={templateName} onChange={event => setTemplateName(event.target.value)} /></label><label className="field"><span>提示词正文</span><textarea aria-label="提示词正文" maxLength={16000} disabled={locked || templateEditing?.readonly} value={templateContent} onChange={event => setTemplateContent(event.target.value)} style={{ minHeight: 180 }} /></label><div className="form-actions">{!templateEditing?.readonly && <button className="button primary" disabled={locked}>{templateEditing ? '保存提示词修改' : '创建本地提示词'}</button>}{templateEditing && <><button className="button secondary" disabled={locked} type="button" onClick={reloadTemplate}>载入提示词最新版本</button><button className="button secondary" disabled={locked} type="button" onClick={() => { setTemplateEditing(null); setTemplateName(templateName.slice(0, 118) + ' · 副本'); setDeleteTemplate(false) }}><Icon name="copy" />复制为本地提示词</button>{!templateEditing.readonly && <button className="button secondary danger" disabled={locked} type="button" onClick={() => setDeleteTemplate(true)}><Icon name="delete" />删除提示词</button>}</>}</div></form>{deleteTemplate && <div className="market-detail"><p>确认删除自定义提示词“{templateName}”？</p><div className="form-actions"><button className="button secondary danger" disabled={locked} type="button" onClick={removeTemplate}><Icon name="check" />确认删除提示词</button><button className="button secondary" disabled={locked} type="button" onClick={() => setDeleteTemplate(false)}><Icon name="close" />取消</button></div></div>}</details></section>
    <section className="card"><details><summary><strong>调用记录与用量 · {scope ? '所选账户' : '未绑定账户'}</strong></summary>{usage ? <><p>共 {usage.call_count} 次 · 完成 {usage.completed_count} 次 · 失败 {usage.failed_count} 次 · 用量未知 {usage.unknown_usage_count} 次</p><p>输入 token {token(usage.prompt_tokens)} · 输出 token {token(usage.completion_tokens)} · 合计 {token(usage.total_tokens)}</p><p className="muted">提供方未返回的 token 数量显示为未知。{usage.tokens_are_partial && ' 当前合计仅覆盖提供方已报告的部分用量。'}</p><div className="table-wrap"><table><thead><tr><th>模型</th><th>调用 / 完成 / 失败</th><th>输入 / 输出 / 合计 token</th><th>未知用量次数</th></tr></thead><tbody>{usage.by_model.map((item, index) => <tr key={`${item.provider}:${item.model}:${index}`}><td>{item.model}<br /><small>{item.provider}</small></td><td>{item.call_count} / {item.completed_count} / {item.failed_count}</td><td>{token(item.prompt_tokens)} / {token(item.completion_tokens)} / {token(item.total_tokens)}{item.tokens_are_partial ? '（部分）' : ''}</td><td>{item.unknown_usage_count}</td></tr>)}</tbody></table></div></> : <p className="muted">暂无用量摘要。</p>}<h3>历史调用</h3><div className="table-wrap"><table><thead><tr><th>时间 / 类型</th><th>模型</th><th>状态</th><th>合计 token</th><th>记录</th></tr></thead><tbody>{calls.map(item => <tr key={item.id}><td>{new Date(item.created_at).toLocaleString()}<br /><small>{{ connection_test: '连接测试', chat: '会话', stock_analysis: '个股研究', review_draft: '复盘草稿', ocr_trades: '成交识别', ocr_assets: '资产识别', review_scores: '评分建议' }[item.kind]}</small></td><td>{item.model}</td><td>{phaseNames[item.status] || item.status}</td><td>{token(item.usage?.total_tokens)}</td><td><button className="link-button" type="button" disabled={locked} onClick={async () => { setBusy(true); try { setCallDetail(await api<Run>(scoped(`/ai/runs/${encodeURIComponent(item.id)}`))) } catch (err) { await report(err) } finally { setBusy(false) } }}>查看完整调用</button></td></tr>)}</tbody></table></div>{!calls.length && <p className="muted">当前范围暂无调用记录。</p>}{callDetail && <div className="market-detail"><RunStatus run={callDetail} /><pre style={plainStyle}>{callDetail.output || '没有响应正文'}</pre><details><summary>冻结上下文与请求</summary><pre style={plainStyle}>{pretty({ input_sha256: callDetail.input_sha256, context: callDetail.context, request: callDetail.request })}</pre></details></div>}</details></section>
    </div>
    <div hidden={panel !== 'generations'}>{generationsOpened && <Suspense fallback={<section className="card">正在载入识别与复盘…</section>}><AIGenerationEditor accountId={accountId} accountKind={accountKind} onBusyChange={setGenerationBusy} /></Suspense>}</div>
  </div>
}
