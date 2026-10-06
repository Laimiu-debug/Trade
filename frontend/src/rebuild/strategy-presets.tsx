import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Params = Record<string, string>
type Change = { parameter: string; before: string | null; after: string | null }
type Preset = { id: string; name: string; strategy_id: string; strategy_version: string; revision: number; params: Params; favorite: boolean; compatible: boolean; updated_at: string }
type Revision = { id: string; revision: number; action: string; snapshot: Preset & { deleted?: boolean }; created_at: string }
type Proposal = { id: string; preset_id: string; base_revision: number; proposal_sha256: string; params: Params; diff: Change[]; status: string; source: { kind: string; source_ai_run_id?: string }; applied_revision: number | null }
type ImportPreview = { name: string; strategy_id: string; strategy_version: string; params: Params; diff: Change[]; preview_sha256: string }
type FormPreview = { name: string; params: Params; diff: Change[]; baseStamp: string; presetId?: string; revision?: number; shareCode?: string }
type AICall = { id: string; account_id: string | null; kind: string; status: string; model: string; created_at: string }
type Props = { strategyId: string; params: Params; onApply: (params: Params) => void; accountId?: string }
const revisionLabels: Record<string, string> = { create: '创建', update: '修改', delete: '删除', apply_proposal: '确认参数建议' }
const stamp = (params: Params) => JSON.stringify(Object.entries(params).sort(([a], [b]) => a.localeCompare(b)))
const diff = (before: Params, after: Params): Change[] => [...new Set([...Object.keys(before), ...Object.keys(after)])].sort().filter(key => before[key] !== after[key]).map(parameter => ({ parameter, before: before[parameter] ?? null, after: after[parameter] ?? null }))
const display = (value: string | null) => value === 'true' ? '启用' : value === 'false' ? '关闭' : value ?? '—'

function ParameterDiff({ changes, labels }: { changes: Change[]; labels: Record<string, string> }) {
  return changes.length ? <div className="table-wrap"><table><thead><tr><th>参数</th><th>当前值</th><th>建议值</th></tr></thead><tbody>{changes.map(item => <tr key={item.parameter}><td>{labels[item.parameter] || item.parameter}</td><td>{display(item.before)}</td><td>{display(item.after)}</td></tr>)}</tbody></table></div> : <p className="muted">参数值相同。</p>
}

export function StrategyPresets(props: Props) {
  return <PresetPanel key={props.strategyId} {...props} />
}

function PresetPanel({ strategyId, params, onApply, accountId }: Props) {
  const [presets, setPresets] = useState<Preset[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [name, setName] = useState('我的参数预设')
  const [favoritesOnly, setFavoritesOnly] = useState(false)
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [history, setHistory] = useState<Revision[] | null>(null)
  const [deletePending, setDeletePending] = useState(false)
  const [formPreview, setFormPreview] = useState<FormPreview | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [proposalText, setProposalText] = useState('')
  const [shareCode, setShareCode] = useState('')
  const [importCode, setImportCode] = useState('')
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null)
  const [importName, setImportName] = useState('')
  const [calls, setCalls] = useState<AICall[] | null>(null)
  const [callId, setCallId] = useState('')
  const live = useRef(true)
  const currentStamp = useRef(stamp(params))
  currentStamp.current = stamp(params)
  const currentAccount = useRef(accountId)
  currentAccount.current = accountId
  const selected = presets.find(item => item.id === selectedId)
  const visible = favoritesOnly ? presets.filter(item => item.favorite || item.id === selectedId) : presets
  const formStale = formPreview && formPreview.baseStamp !== stamp(params)

  useEffect(() => {
    live.current = true
    Promise.all([
      api<Preset[]>(`/research/presets?strategy_id=${encodeURIComponent(strategyId)}`),
      api<Array<{ id: string; params_schema: Record<string, { title?: string }> }>>('/research/strategies'),
    ]).then(([items, catalog]) => {
      if (!live.current) return
      setPresets(items)
      setLabels(Object.fromEntries(Object.entries(catalog.find(item => item.id === strategyId)?.params_schema || {}).map(([key, spec]) => [key, spec.title || key])))
    }).catch(err => { if (live.current) setError(err instanceof Error ? err.message : '参数预设读取失败') })
    return () => { live.current = false }
  }, [strategyId])

  useEffect(() => { setCalls(null); setCallId(''); setProposal(null) }, [accountId])

  async function refresh() {
    const items = await api<Preset[]>(`/research/presets?strategy_id=${encodeURIComponent(strategyId)}`)
    if (live.current) setPresets(items)
    return items
  }

  async function perform(operation: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try { await operation() }
    catch (caught) {
      if (!live.current) return
      const failure = caught as Error & { code?: string }
      setError(failure.message || '参数预设操作失败')
      if (failure.code?.includes('CONFLICT') || failure.code?.includes('CHANGED')) {
        try { await refresh() } catch { /* Preserve the original conflict and local inputs. */ }
      }
    } finally { if (live.current) setBusy(false) }
  }

  function choose(item?: Preset) {
    setSelectedId(item?.id || ''); setName(item?.name || '我的参数预设')
    setHistory(null); setDeletePending(false); setProposal(null); setFormPreview(null); setShareCode('')
    setError(''); setNotice('')
  }

  async function createPreset(candidate: Params, title: string) {
    const saved = await api<Preset>('/research/presets', 'POST', { name: title, strategy_id: strategyId, params: candidate })
    await refresh()
    if (live.current) { choose(saved); setNotice(`已保存“${saved.name}” · 版本 ${saved.revision}。`) }
  }

  async function updateMetadata(favorite?: boolean) {
    if (!selected) return
    const saved = await api<Preset>(`/research/presets/${encodeURIComponent(selected.id)}`, 'PUT', {
      name: favorite === undefined ? name : selected.name, strategy_id: strategyId, params: selected.params,
      favorite: favorite ?? selected.favorite, expected_revision: selected.revision,
    })
    await refresh()
    if (live.current) { choose(saved); if (favorite !== undefined) setName(name); setNotice(`已保存“${saved.name}” · 版本 ${saved.revision}。`) }
  }

  async function previewPreset() {
    if (!selected) return
    const fresh = await api<Preset>(`/research/presets/${encodeURIComponent(selected.id)}`)
    if (!fresh.compatible) throw new Error('策略参数规范已更新，请检查后重新保存预设。')
    if (fresh.revision !== selected.revision) { await refresh(); throw new Error('预设已修改，请重新选择最新版本。') }
    if (live.current) setFormPreview({ name: fresh.name, params: fresh.params, diff: diff(params, fresh.params),
      baseStamp: stamp(params), presetId: fresh.id, revision: fresh.revision })
  }

  async function applyToForm() {
    if (!formPreview || formPreview.baseStamp !== currentStamp.current) throw new Error('表单参数已改变，请重新预览差异。')
    if (formPreview.presetId) {
      const fresh = await api<Preset>(`/research/presets/${encodeURIComponent(formPreview.presetId)}`)
      if (fresh.revision !== formPreview.revision || !fresh.compatible) throw new Error('预设版本已改变，请重新预览。')
    }
    if (formPreview.shareCode) {
      const fresh = await api<ImportPreview>('/research/presets/preview-import', 'POST', { share_code: formPreview.shareCode, current_params: params })
      if (fresh.strategy_id !== strategyId || stamp(fresh.params) !== stamp(formPreview.params)) throw new Error('分享参数规范已改变，请重新预览。')
    }
    if (!live.current) return
    if (formPreview.baseStamp !== currentStamp.current) throw new Error('表单参数已改变，请重新预览差异。')
    onApply({ ...formPreview.params }); setFormPreview(null)
    setNotice(`已将“${formPreview.name}”填入当前参数表单，可继续检查并运行。`)
  }

  async function createProposal(candidate?: Params, sourceId?: string) {
    if (!selected) throw new Error('请先选择目标预设。')
    const scope = accountId
    const preview = await api<Proposal>('/research/parameter-proposals', 'POST', {
      preset_id: selected.id, expected_revision: selected.revision,
      ...(sourceId ? { source_ai_run_id: sourceId, account_id: scope } : { params: candidate }),
    })
    if (live.current && (!sourceId || currentAccount.current === scope)) { setProposal(preview); setFormPreview(null) }
  }

  async function applyProposal() {
    if (!proposal) return
    const result = await api<{ proposal: Proposal; preset: Preset; already_applied: boolean; current_revision: number }>(
      `/research/parameter-proposals/${encodeURIComponent(proposal.id)}/apply`, 'POST', {
        expected_revision: proposal.base_revision, expected_proposal_hash: proposal.proposal_sha256,
      })
    await refresh()
    if (live.current) {
      setProposal(result.proposal)
      setNotice(`已确认参数建议，预设版本为 ${result.current_revision}。可点击“预览填入表单”继续使用。`)
    }
  }

  function parseProposal() {
    if (new TextEncoder().encode(proposalText).length > 65536) throw new Error('参数 JSON 不能超过 64 KiB。')
    const parsed: unknown = JSON.parse(proposalText)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('请粘贴参数键和值组成的 JSON 对象。')
    return parsed as Params
  }

  return <details className="card span-all" aria-label="策略参数预设" style={{ marginTop: 16 }}>
    <summary><strong>参数预设与分享</strong></summary>
    <p className="muted">保存当前策略参数，比较差异后填入表单。预设只包含参数；事件模板、账户和执行设置需在各自页面选择。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    <div className="form-grid">
      <label className="field"><span>已保存参数预设</span><select value={selectedId} disabled={busy} onChange={event => choose(presets.find(item => item.id === event.target.value))}><option value="">选择预设或另存当前参数</option>{visible.map(item => <option key={item.id} value={item.id}>{item.favorite ? '★ ' : ''}{item.name} · 版本 {item.revision}{item.compatible ? '' : ' · 规范已更新'}</option>)}</select></label>
      <label className="field"><span>预设名称</span><input maxLength={80} value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label>
    </div>
    <div className="toolbar" style={{ flexWrap: 'wrap', marginTop: 12 }}>
      <label className="check-field"><input type="checkbox" checked={favoritesOnly} onChange={event => setFavoritesOnly(event.target.checked)} />仅显示收藏</label>
      <button type="button" className="button secondary" disabled={busy} onClick={() => perform(async () => { await refresh(); setNotice('预设列表已刷新。') })}><Icon name="refresh" />刷新预设</button>
      <button type="button" className="button primary" disabled={busy || !name.trim()} onClick={() => perform(() => createPreset(params, name))}><Icon name="save" />保存当前参数为新预设</button>
    </div>
    {!presets.length && <p className="muted">此策略暂无参数预设。</p>}
    {selected && <>
      <p className="muted">已选“{selected.name}” · 版本 {selected.revision} · {new Date(selected.updated_at).toLocaleString()}</p>
      {!selected.compatible && <p className="danger">参数规范已更新，请检查参数后另存新预设。</p>}
      <div className="toolbar" style={{ flexWrap: 'wrap' }}>
        <button type="button" className="button secondary" disabled={busy || !selected.compatible} onClick={() => perform(previewPreset)}><Icon name="document" />预览填入表单</button>
        <button type="button" className="button secondary" disabled={busy || !selected.compatible} onClick={() => perform(() => createProposal(params))}><Icon name="document" />预览用当前参数更新预设</button>
        <button type="button" className="button secondary" disabled={busy || !name.trim() || !selected.compatible} onClick={() => perform(() => updateMetadata())}><Icon name="save" />保存预设名称</button>
        <button type="button" className="button secondary" disabled={busy || !selected.compatible} onClick={() => perform(() => updateMetadata(!selected.favorite))}>{selected.favorite ? '取消收藏预设' : '收藏预设'}</button>
        <button type="button" className="button secondary" disabled={busy} onClick={() => perform(async () => { const items = await api<Revision[]>(`/research/presets/${encodeURIComponent(selected.id)}/history`); if (live.current) setHistory(items) })}>查看预设修订</button>
        <button type="button" className="button secondary" disabled={busy || !selected.compatible} onClick={() => perform(async () => { const result = await api<{ share_code: string }>(`/research/presets/${encodeURIComponent(selected.id)}/export`); if (live.current) setShareCode(result.share_code) })}>生成参数分享码</button>
        <button type="button" className="link-button danger" disabled={busy} onClick={() => setDeletePending(true)}>删除预设</button>
      </div>
    </>}
    {deletePending && selected && <div className="alert error" style={{ marginTop: 12 }}><p>删除“{selected.name}”？历史修订仍保留。</p><div className="toolbar"><button type="button" className="button secondary danger" disabled={busy} onClick={() => perform(async () => {
      await api(`/research/presets/${encodeURIComponent(selected.id)}?expected_revision=${selected.revision}`, 'DELETE')
      await refresh(); if (live.current) { choose(); setNotice('预设已删除，历史修订仍保留。') }
    })}><Icon name="check" />确认删除预设</button><button type="button" className="button secondary" disabled={busy} onClick={() => setDeletePending(false)}><Icon name="close" />取消删除预设</button></div></div>}
    {formPreview && <section aria-label="填入表单差异" style={{ marginTop: 16 }}><h4>填入表单前确认 · {formPreview.name}</h4><ParameterDiff changes={formPreview.diff} labels={labels} />{formStale && <p className="danger">表单参数已改变，请重新预览。</p>}<div className="toolbar"><button type="button" className="button primary" disabled={busy || Boolean(formStale)} onClick={() => perform(applyToForm)}><Icon name="check" />确认填入当前表单</button><button type="button" className="button secondary" disabled={busy} onClick={() => setFormPreview(null)}><Icon name="close" />取消填入</button></div></section>}
    {selected && <details style={{ marginTop: 16 }}><summary>参数建议与人工确认</summary><p className="muted">建议按参数键合并到所选预设；未提供的参数保持原值。先检查差异，再确认保存。</p>
      <label className="field"><span>参数建议 JSON</span><textarea rows={4} maxLength={65536} value={proposalText} placeholder={'{"min_score":"65"}'} disabled={busy} onChange={event => { setProposalText(event.target.value); setProposal(null) }} /></label>
      <button type="button" className="button secondary" disabled={busy || !proposalText.trim() || !selected.compatible} onClick={() => perform(() => createProposal(parseProposal()))}><Icon name="document" />预览 JSON 参数建议</button>
      {accountId ? <div style={{ marginTop: 12 }}><button type="button" className="button secondary" disabled={busy} onClick={() => perform(async () => {
        const scope = accountId
        const items = await api<AICall[]>(`/ai/runs?account_id=${encodeURIComponent(scope)}`)
        if (live.current && currentAccount.current === scope) { setCalls(items.filter(item => item.account_id === scope && item.status === 'completed' && item.kind === 'chat')); setCallId('') }
      })}>读取当前账户的已完成 AI 调用</button>
        {calls && <><label className="field" style={{ marginTop: 12 }}><span>参数建议来源 AI 调用</span><select value={callId} disabled={busy} onChange={event => { setCallId(event.target.value); setProposal(null) }}><option value="">选择已完成调用</option>{calls.map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString()} · {item.model} · {item.id.slice(0, 8)}</option>)}</select></label><p className="muted">AI 回复须是包含 strategy_id、params 的 JSON 对象，可包含 strategy_version。</p>{!calls.length && <p className="muted">当前账户暂无已完成的 AI 对话调用。</p>}<button type="button" className="button secondary" disabled={busy || !callId || !selected.compatible} onClick={() => perform(() => createProposal(undefined, callId))}><Icon name="document" />预览 AI 参数建议</button></>}
      </div> : <p className="muted">选择账户后，可从该账户已完成的 AI 调用中预览参数建议。</p>}
    </details>}
    {proposal && <section aria-label="参数建议差异" style={{ marginTop: 16 }}><h4>参数建议差异 · 基于预设版本 {proposal.base_revision}</h4><p className="muted">来源：{proposal.source.kind === 'ai' ? `AI 调用 ${proposal.source.source_ai_run_id?.slice(0, 8)}` : '人工提交'} · {proposal.status === 'applied' ? `已确认 · 版本 ${proposal.applied_revision}` : '等待人工确认'}</p><ParameterDiff changes={proposal.diff} labels={labels} />
      {proposal.status === 'pending' && <><p className="muted">确认后保存为预设新版本，再通过“预览填入表单”使用。</p>{selected?.revision !== proposal.base_revision && <p className="danger">所选预设版本已改变，请重新生成建议。</p>}<div className="toolbar"><button type="button" className="button primary" disabled={busy || selected?.revision !== proposal.base_revision} onClick={() => perform(applyProposal)}><Icon name="check" />确认建议并保存预设</button><button type="button" className="button secondary" disabled={busy} onClick={() => setProposal(null)}><Icon name="close" />关闭建议预览</button></div></>}
    </section>}
    {shareCode && <div style={{ marginTop: 16 }}><label className="field"><span>参数分享码</span><textarea readOnly rows={3} value={shareCode} onFocus={event => event.target.select()} /></label><button type="button" className="button secondary" disabled={busy} onClick={() => perform(async () => { await navigator.clipboard.writeText(shareCode); setNotice('参数分享码已复制。') })}><Icon name="copy" />复制参数分享码</button></div>}
    <details style={{ marginTop: 16 }}><summary>导入参数分享码</summary><label className="field" style={{ marginTop: 12 }}><span>待导入参数分享码</span><textarea rows={3} maxLength={65536} value={importCode} disabled={busy} onChange={event => { setImportCode(event.target.value); setImportPreview(null) }} /></label><button type="button" className="button secondary" disabled={busy || !importCode.trim()} onClick={() => perform(async () => {
      const result = await api<ImportPreview>('/research/presets/preview-import', 'POST', { share_code: importCode.trim(), current_params: params })
      if (result.strategy_id !== strategyId) throw new Error(`分享码属于 ${result.strategy_id}，请先切换至对应策略。`)
      if (live.current) { setImportPreview(result); setImportName(result.name) }
    })}>检查分享码并预览差异</button>
      {importPreview && <div style={{ marginTop: 12 }}><h4>分享参数 · {importPreview.name}</h4><ParameterDiff changes={importPreview.diff} labels={labels} /><label className="field"><span>导入预设名称</span><input maxLength={80} value={importName} disabled={busy} onChange={event => setImportName(event.target.value)} /></label><div className="toolbar" style={{ flexWrap: 'wrap' }}><button type="button" className="button primary" disabled={busy || !importName.trim()} onClick={() => perform(async () => { await createPreset(importPreview.params, importName); if (live.current) { setImportPreview(null); setImportCode('') } })}><Icon name="check" />确认另存导入预设</button><button type="button" className="button secondary" disabled={busy} onClick={() => setFormPreview({ name: importName || importPreview.name, params: importPreview.params, diff: diff(params, importPreview.params), baseStamp: stamp(params), shareCode: importCode.trim() })}><Icon name="document" />预览导入参数填入表单</button></div></div>}
    </details>
    {history && <section aria-label="预设修订历史" style={{ marginTop: 16 }}><h4>预设修订历史</h4>{history.map(item => <details key={item.id} style={{ marginTop: 8 }}><summary>版本 {item.revision} · {revisionLabels[item.action] || item.action} · {new Date(item.created_at).toLocaleString()}</summary><p>{item.snapshot.name}{item.snapshot.favorite ? ' · 已收藏' : ''}{item.snapshot.deleted ? ' · 已删除' : ''}</p><div className="table-wrap"><table><thead><tr><th>参数</th><th>当时的值</th></tr></thead><tbody>{Object.entries(item.snapshot.params).map(([key, value]) => <tr key={key}><td>{labels[key] || key}</td><td>{display(value)}</td></tr>)}</tbody></table></div></details>)}</section>}
  </details>
}
