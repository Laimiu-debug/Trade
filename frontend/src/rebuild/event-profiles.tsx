import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

export type EventProfileDimension = { dimension_id: string; label: string; metric_key: string; weight: number; invert: boolean; enabled: boolean }
export type EventProfileRuleOption = { rule_key: string; label: string; description: string; category: string; value_type: 'number' | 'integer' | 'boolean'; min_value: number | null; max_value: number | null; step: number | null; recommended_min: number | null; recommended_max: number | null; default_value: number | boolean }
export type EventProfileSnapshot = { profile_id: string; name: string; description: string; score_mode: 'legacy_formula' | 'dimension_weighted'; is_system: boolean; updated_at: string; dimensions: EventProfileDimension[]; rule_values: Array<{ rule_key: string; value: number | boolean }> }
export type EventProfile = EventProfileSnapshot & { revision: number; sha256: string }
export type EventProfileCatalog = { default_profile_id: string; active_profile_id: string; active_revision: number; profiles: EventProfile[]; metric_options: Array<{ metric_key: string; label: string; description: string }>; rule_options: EventProfileRuleOption[]; version: string; source_sha256: string }
type DimensionDraft = Omit<EventProfileDimension, 'weight'> & { weight: string }
type ProfileDraft = { name: string; description: string; score_mode: EventProfileSnapshot['score_mode']; dimensions: DimensionDraft[]; rules: Record<string, string | boolean> }
type HistoryRow = { id: string; action: string; revision: number; snapshot: EventProfileSnapshot; created_at: string }
const actionNames: Record<string, string> = { create: '创建', update: '修改', apply: '设为当前模板', delete: '删除' }

function toDraft(profile: EventProfileSnapshot): ProfileDraft {
  return { name: profile.name, description: profile.description, score_mode: profile.score_mode,
    dimensions: profile.dimensions.map(item => ({ ...item, weight: String(item.weight) })),
    rules: Object.fromEntries(profile.rule_values.map(item => [item.rule_key, typeof item.value === 'boolean' ? item.value : String(item.value)])) }
}

function numeric(value: string | boolean | undefined, label: string, integer = false): number {
  if (typeof value !== 'string' || value.trim() === '' || !Number.isFinite(Number(value)) || (integer && !Number.isInteger(Number(value)))) {
    throw new Error(`${label}需要填写${integer ? '整数' : '有效数字'}`)
  }
  return Number(value)
}

function HistorySnapshot({ row, catalog }: { row: HistoryRow; catalog: EventProfileCatalog }) {
  const labels = new Map(catalog.rule_options.map(item => [item.rule_key, item.label]))
  return <details><summary>查看当时配置</summary>
    <p>{row.snapshot.name} · {row.snapshot.score_mode === 'legacy_formula' ? '经典综合公式' : '维度加权'}</p>
    {row.snapshot.description && <p className="muted">{row.snapshot.description}</p>}
    {row.snapshot.dimensions.length > 0 && <div className="table-wrap"><table><thead><tr><th>维度</th><th>权重</th><th>启用</th><th>反向</th></tr></thead><tbody>{row.snapshot.dimensions.map(item => <tr key={item.dimension_id}><td>{item.label}</td><td>{item.weight}</td><td>{item.enabled ? '是' : '否'}</td><td>{item.invert ? '是' : '否'}</td></tr>)}</tbody></table></div>}
    <details><summary>判定规则 · {row.snapshot.rule_values.length} 项</summary><div className="table-wrap"><table><thead><tr><th>规则</th><th>值</th></tr></thead><tbody>{row.snapshot.rule_values.map(item => <tr key={item.rule_key}><td>{labels.get(item.rule_key) || item.rule_key}</td><td>{typeof item.value === 'boolean' ? item.value ? '启用' : '关闭' : item.value}</td></tr>)}</tbody></table></div></details>
  </details>
}

export function EventProfileEditor({ onChanged }: { onChanged?: (catalog: EventProfileCatalog) => void | Promise<void> }) {
  const [catalog, setCatalog] = useState<EventProfileCatalog | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [baseRevision, setBaseRevision] = useState<number | null>(null)
  const [draft, setDraft] = useState<ProfileDraft | null>(null)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [ruleSearch, setRuleSearch] = useState('')
  const [deletePending, setDeletePending] = useState(false)
  const [history, setHistory] = useState<HistoryRow[] | null>(null)

  useEffect(() => {
    let live = true
    api<EventProfileCatalog>('/research/event-profiles').then(next => {
      if (!live) return
      setCatalog(next)
      const initial = next.profiles.find(item => item.profile_id === next.active_profile_id) || next.profiles[0]
      if (initial) { setSelectedId(initial.profile_id); setBaseRevision(initial.revision); setDraft(toDraft(initial)) }
    }).catch(err => { if (live) setError(err instanceof Error ? err.message : '模板读取失败') })
    return () => { live = false }
  }, [])

  const selected = catalog?.profiles.find(item => item.profile_id === selectedId)
  const readonly = Boolean(selected?.is_system)
  const conflict = Boolean(selected && baseRevision !== selected.revision)
  const active = catalog?.profiles.find(item => item.profile_id === catalog.active_profile_id)
  const groups = useMemo(() => {
    const grouped = new Map<string, EventProfileRuleOption[]>()
    const term = ruleSearch.trim().toLowerCase()
    for (const item of catalog?.rule_options || []) {
      if (term && !`${item.label} ${item.rule_key} ${item.description} ${item.category}`.toLowerCase().includes(term)) continue
      grouped.set(item.category, [...(grouped.get(item.category) || []), item])
    }
    return [...grouped.entries()]
  }, [catalog, ruleSearch])

  function choose(profile: EventProfile) {
    setSelectedId(profile.profile_id); setBaseRevision(profile.revision); setDraft(toDraft(profile))
    setDirty(false); setHistory(null); setDeletePending(false); setError(''); setNotice('')
  }

  function canDiscard() { return !dirty || window.confirm('当前模板有未保存的修改，确认放弃这些修改？') }

  function duplicate() {
    if (!draft) return
    setDraft({ ...draft, name: (draft.name.slice(0, 59) + ' · 副本'),
      dimensions: draft.dimensions.map(item => ({ ...item })), rules: { ...draft.rules } })
    setSelectedId(''); setBaseRevision(null); setDirty(true); setHistory(null)
    setDeletePending(false); setError(''); setNotice('已复制为自定义草稿，保存后可设为当前模板。')
  }

  function change(update: Partial<ProfileDraft>) {
    setDraft(current => current ? { ...current, ...update } : current)
    setDirty(true); setDeletePending(false); setNotice('')
  }

  function changeDimension(index: number, update: Partial<DimensionDraft>) {
    if (!draft) return
    change({ dimensions: draft.dimensions.map((item, itemIndex) => itemIndex === index ? { ...item, ...update } : item) })
  }

  async function refreshCatalog(notify = false) {
    const next = await api<EventProfileCatalog>('/research/event-profiles')
    setCatalog(next)
    if (notify) await onChanged?.(next)
    return next
  }

  async function refresh() {
    setBusy(true); setError('')
    try {
      const next = await refreshCatalog(true)
      if (!draft) {
        const first = next.profiles.find(item => item.profile_id === next.active_profile_id) || next.profiles[0]
        if (first) choose(first)
      }
      setNotice('已刷新模板列表，编辑区保留当前内容。')
    } catch (err) { setError(err instanceof Error ? err.message : '模板刷新失败') }
    finally { setBusy(false) }
  }

  async function failed(err: unknown) {
    const caught = err as Error & { code?: string }
    const isConflict = caught.code === 'EVENT_PROFILE_VERSION_CONFLICT' || caught.code === 'EVENT_PROFILE_SELECTION_CONFLICT'
    setError((caught.message || '操作失败') + (isConflict ? '。编辑内容已保留，可重新载入最新版本，或另存为副本。' : ''))
    if (isConflict) {
      try { await refreshCatalog(true) } catch { /* Keep the original error and the local draft. */ }
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || !catalog || readonly || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      if (!draft.name.trim()) throw new Error('请填写模板名称')
      const dimensions = draft.score_mode === 'legacy_formula' ? [] : draft.dimensions.map(item => ({ ...item, weight: numeric(item.weight, `${item.label}权重`) }))
      if (draft.score_mode === 'dimension_weighted' && !dimensions.some(item => item.enabled && item.weight > 0)) throw new Error('至少启用一个权重大于零的评分维度')
      const profile = { name: draft.name, description: draft.description, score_mode: draft.score_mode,
        dimensions, rule_values: catalog.rule_options.map(spec => ({ rule_key: spec.rule_key,
          value: spec.value_type === 'boolean' ? draft.rules[spec.rule_key] === true : numeric(draft.rules[spec.rule_key], spec.label, spec.value_type === 'integer') })) }
      const saved = await api<EventProfile>(selectedId ? `/research/event-profiles/${encodeURIComponent(selectedId)}` : '/research/event-profiles', selectedId ? 'PUT' : 'POST', { profile, ...(selectedId ? { expected_revision: baseRevision } : {}) })
      choose(saved)
      await refreshCatalog(true)
      setNotice('模板已保存。使用“设为当前模板”选择后续研究采用的配置。')
    } catch (err) { await failed(err) }
    finally { setBusy(false) }
  }

  async function apply() {
    if (!selected || !catalog || dirty || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/research/event-profiles/${encodeURIComponent(selected.profile_id)}/apply`, 'POST', { expected_revision: selected.revision, expected_active_revision: catalog.active_revision })
      await refreshCatalog(true)
      setNotice(`当前模板已设为“${selected.name}”。已有研究记录保留各自的配置快照。`)
    } catch (err) { await failed(err) }
    finally { setBusy(false) }
  }

  async function remove() {
    if (!selected || !catalog || selected.is_system || busy || !deletePending) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/research/event-profiles/${encodeURIComponent(selected.profile_id)}`, 'DELETE', { expected_revision: baseRevision, expected_active_revision: catalog.active_revision })
      const next = await refreshCatalog(true)
      const replacement = next.profiles.find(item => item.profile_id === next.active_profile_id) || next.profiles[0]
      if (replacement) choose(replacement)
      setNotice('自定义模板已删除，历史研究中的配置快照仍可查看。')
    } catch (err) { await failed(err) }
    finally { setBusy(false); setDeletePending(false) }
  }

  async function loadHistory() {
    if (!selected || busy) return
    setBusy(true); setError('')
    try { setHistory(await api<HistoryRow[]>(`/research/event-profiles/${encodeURIComponent(selected.profile_id)}/history`)) }
    catch (err) { await failed(err) }
    finally { setBusy(false) }
  }

  return <section className="card span-all" aria-label="事件判定模板">
    <h3>事件判定模板</h3>
    <p className="muted">配置维科夫事件的判定规则与评分维度。当前模板：{active?.name || '读取中'}。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    <div className="toolbar" style={{ flexWrap: 'wrap', marginBottom: 16 }}>
      <button type="button" className="button secondary" disabled={busy} onClick={refresh}><Icon name="refresh" />刷新模板列表</button>
      {draft && <button type="button" className="button secondary" disabled={busy} onClick={duplicate}><Icon name="copy" />复制为自定义模板</button>}
    </div>
    {catalog && draft && <>
      <label className="field"><span>查看或编辑模板</span><select value={selectedId} disabled={busy} onChange={event => {
        const profile = catalog.profiles.find(item => item.profile_id === event.target.value)
        if (profile && canDiscard()) choose(profile)
      }}>{!selectedId && <option value="">新建自定义草稿</option>}{catalog.profiles.map(item => <option key={item.profile_id} value={item.profile_id}>{item.name}{item.is_system ? ' · 系统只读' : ` · 版本 ${item.revision}`}{item.profile_id === catalog.active_profile_id ? ' · 当前' : ''}</option>)}</select></label>
      {readonly && <p className="muted">系统预设只读，点击“复制为自定义模板”后调整。</p>}
      {selectedId && !selected && <p className="danger" role="alert">此模板已被删除。当前输入已保留，可复制为新模板。</p>}
      {conflict && <div className="alert error" role="alert">编辑基于版本 {baseRevision}，最新版本为 {selected?.revision}。<button type="button" className="link-button" disabled={busy} onClick={() => { if (selected && canDiscard()) choose(selected) }}>重新载入最新版本</button></div>}
      <form className="form" noValidate onSubmit={save} style={{ marginTop: 16 }}>
        <fieldset disabled={readonly || busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className="form-grid">
            <label className="field"><span>模板名称</span><input value={draft.name} maxLength={64} required onChange={event => change({ name: event.target.value })} /></label>
            <label className="field"><span>计分方式</span><select value={draft.score_mode} onChange={event => change({ score_mode: event.target.value as ProfileDraft['score_mode'] })}><option value="legacy_formula">经典综合公式</option><option value="dimension_weighted">维度加权</option></select></label>
            <label className="field" style={{ gridColumn: '1 / -1' }}><span>说明</span><textarea value={draft.description} maxLength={200} onChange={event => change({ description: event.target.value })} /></label>
          </div>
          {draft.score_mode === 'dimension_weighted' && <details open><summary>评分维度 · {draft.dimensions.length} 项</summary>
            <p className="muted">启用的维度按权重计算；反向维度使用 100 − 原分值。</p>
            <div className="form">{draft.dimensions.map((item, index) => <div className="form-grid" key={item.dimension_id}>
              <label className="field"><span>维度 {index + 1}</span><select value={item.metric_key} onChange={event => {
                const metric = catalog.metric_options.find(option => option.metric_key === event.target.value)
                changeDimension(index, { metric_key: event.target.value, label: metric?.label || event.target.value })
              }}>{catalog.metric_options.map(metric => <option key={metric.metric_key} value={metric.metric_key}>{metric.label}</option>)}</select></label>
              <label className="field"><span>{item.label}权重</span><input type="number" min="0" max="10" step="any" value={item.weight} required onChange={event => changeDimension(index, { weight: event.target.value })} /></label>
              <label className="field"><span>维度名称</span><input maxLength={64} value={item.label} required onChange={event => changeDimension(index, { label: event.target.value })} /></label>
              <div className="toolbar" style={{ flexWrap: 'wrap' }}><label className="check-field"><input type="checkbox" checked={item.enabled} onChange={event => changeDimension(index, { enabled: event.target.checked })} />启用</label><label className="check-field"><input type="checkbox" checked={item.invert} onChange={event => changeDimension(index, { invert: event.target.checked })} />反向</label>{!readonly && <button className="link-button danger" type="button" onClick={() => change({ dimensions: draft.dimensions.filter((_, row) => row !== index) })}>移除维度 {index + 1}</button>}</div>
            </div>)}</div>
            {!readonly && <button type="button" className="button secondary" style={{ marginTop: 12 }} disabled={draft.dimensions.length >= 24} onClick={() => {
              const metric = catalog.metric_options.find(option => !draft.dimensions.some(item => item.metric_key === option.metric_key)) || catalog.metric_options[0]
              if (metric) change({ dimensions: [...draft.dimensions, { dimension_id: `dim_${crypto.randomUUID()}`, metric_key: metric.metric_key, label: metric.label, weight: '1', enabled: true, invert: false }] })
            }}><Icon name="add" />添加评分维度</button>}
          </details>}
        </fieldset>
        <details><summary>事件判定规则 · {catalog.rule_options.length} 项</summary>
          <label className="field" style={{ margin: '12px 0' }}><span>搜索规则</span><input type="search" placeholder="按事件、规则名称或关键词搜索" value={ruleSearch} onChange={event => setRuleSearch(event.target.value)} /></label>
          <fieldset disabled={readonly || busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
            {groups.map(([category, rules]) => <details key={category} open={ruleSearch ? true : undefined} style={{ marginBottom: 12 }}><summary>{category} · {rules.length} 项</summary><div className="form-grid" style={{ marginTop: 12 }}>{rules.map(spec => <label className="field" key={spec.rule_key}><span>{spec.label}</span>
              {spec.value_type === 'boolean' ? <select value={draft.rules[spec.rule_key] === true ? 'true' : 'false'} onChange={event => change({ rules: { ...draft.rules, [spec.rule_key]: event.target.value === 'true' } })}><option value="true">启用</option><option value="false">关闭</option></select> : <input type="number" required min={spec.min_value ?? undefined} max={spec.max_value ?? undefined} step={spec.value_type === 'integer' ? '1' : 'any'} value={String(draft.rules[spec.rule_key] ?? '')} onChange={event => change({ rules: { ...draft.rules, [spec.rule_key]: event.target.value } })} />}
              <small className="muted">{spec.description}{spec.value_type !== 'boolean' && `；范围 ${spec.min_value}–${spec.max_value}`}</small>
            </label>)}</div></details>)}
          </fieldset>
          {!groups.length && <p className="muted">没有符合搜索条件的规则。</p>}
        </details>
        {!readonly && <div className="toolbar" style={{ flexWrap: 'wrap' }}><button className="button primary" disabled={busy || conflict || Boolean(selectedId && !selected)}>{selectedId ? '保存模板修改' : '保存自定义模板'}</button><span className="muted">{dirty ? '有未保存的修改' : '内容已保存'}</span></div>}
      </form>
      {selected && <div className="toolbar" style={{ flexWrap: 'wrap', marginTop: 16 }}>
        <button type="button" className="button secondary" disabled={busy || dirty || conflict || selected.profile_id === catalog.active_profile_id} onClick={apply}>设为当前模板</button>
        <button type="button" className="button secondary" disabled={busy} onClick={loadHistory}>查看修改历史</button>
        {!selected.is_system && <button type="button" className="link-button danger" disabled={busy || conflict} onClick={() => setDeletePending(true)}>删除自定义模板</button>}
      </div>}
      {deletePending && selected && <div className="alert error" role="alert" style={{ marginTop: 12 }}>
        <p>确认删除“{selected.name}”？{dirty ? '未保存的修改也将丢弃。' : ''}{selected.profile_id === catalog.active_profile_id ? '当前模板将恢复为经典综合判别。' : ''}</p>
        <div className="toolbar"><button type="button" className="button secondary danger" disabled={busy} onClick={remove}><Icon name="check" />确认删除模板</button><button type="button" className="button secondary" disabled={busy} onClick={() => setDeletePending(false)}><Icon name="close" />取消删除</button></div>
      </div>}
      {history && <div style={{ marginTop: 16 }}><h4>修改历史 · {selected?.name}</h4>{history.length ? history.map(row => <article key={row.id} style={{ marginBottom: 16 }}><p>{actionNames[row.action] || row.action} · 版本 {row.revision} · {new Date(row.created_at).toLocaleString()}</p><HistorySnapshot row={row} catalog={catalog} /></article>) : <p className="muted">暂无修改记录。</p>}</div>}
    </>}
  </section>
}
