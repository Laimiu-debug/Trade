import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Prompt = { id: string; page: string; label: string; prompt: string; pinned: boolean; readonly?: boolean }
type List = { revision: number; items: Prompt[]; system_items: Prompt[]; pages: string[]; updated_at: string | null }
const names: Record<string, string> = { generic: '通用', market: '行情', research: '研究 / 信号', backtests: '单股回测', portfolio: '组合回测', valuation: '情景估值', reviews: '账户 / 复盘' }
const fresh = (): Prompt => ({ id: crypto.randomUUID().replaceAll('-', ''), page: 'generic', label: '', prompt: '', pinned: false })

export function AIQuickPrompts({ page, disabled, onUse }: { page: string; disabled: boolean; onUse: (prompt: string) => void }) {
  const [saved, setSaved] = useState<List | null>(null)
  const [draft, setDraft] = useState<Prompt>(fresh)
  const [existing, setExisting] = useState(false)
  const [remove, setRemove] = useState<Prompt | null>(null)
  const [latest, setLatest] = useState<List | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [history, setHistory] = useState<Array<{ id: string; created_at: string; after: List }>>([])
  useEffect(() => { let live = true; api<List>('/ai/quick-prompts').then(value => { if (live) setSaved(value) }).catch(err => { if (live) setError(err.message) }); return () => { live = false } }, [])
  const locked = busy || disabled || !saved || Boolean(latest)
  async function persist(items: Prompt[]) {
    if (!saved) return
    setBusy(true); setError(''); setNotice('')
    try {
      setSaved(await api<List>('/ai/quick-prompts', 'PUT', { expected_revision: saved.revision, items: items.map(({ id, page, label, prompt, pinned }) => ({ id, page, label, prompt, pinned })) }))
      setRemove(null); setNotice('快捷提问已保存，不会自动发送到模型。')
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
      if ((err as { code?: string }).code === 'QUICK_PROMPTS_CONFLICT') {
        try { setLatest(await api<List>('/ai/quick-prompts')) } catch { /* Retain draft. */ }
      }
    } finally { setBusy(false) }
    return false
  }
  async function saveDraft() {
    if (!saved) return
    if (existing && !saved.items.some(item => item.id === draft.id)) { setError('原提问已被删除。请明确复制为新提问再保存。'); return }
    const items = existing ? saved.items.map(item => item.id === draft.id ? draft : item) : [...saved.items, draft]
    if (await persist(items)) { setExisting(true); setDraft({ ...draft, label: draft.label.trim(), prompt: draft.prompt.trim() }) }
  }
  function copy(item: Prompt) { setDraft({ ...item, id: fresh().id, readonly: false, label: (item.label + ' · 副本').slice(0, 80) }); setExisting(false) }
  const choices = saved ? [...saved.items, ...saved.system_items].filter(item => item.pinned || item.page === 'generic' || item.page === page) : []
  return <section className="card"><h2>快捷提问 · {names[page] || names.generic}</h2><p className="muted">点击只准备问题草稿；不会自动发送。自定义列表在当前数据目录共享并随备份保存。</p>
    <div className="toolbar" style={{ flexWrap: 'wrap' }}>{choices.map(item => <button type="button" className="button secondary" key={item.id} disabled={disabled} onClick={() => onUse(item.prompt)}>{item.pinned ? '★ ' : ''}{item.label}{item.readonly ? ' · 内置' : ''}</button>)}</div>
    {error && <p role="alert" className="alert error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <details><summary>管理页面提问、置顶与顺序{saved && ` · 版本 ${saved.revision}`}</summary>
      {latest && <div className="alert"><p>当前编辑草稿保留，服务端已有版本 {latest.revision}。</p><details><summary>最新列表</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(latest.items, null, 2)}</pre></details><button className="button secondary" type="button" onClick={() => { setSaved(latest); setLatest(null); setRemove(null) }}>载入最新列表，保留编辑草稿</button></div>}
      <div className="table-wrap"><table><thead><tr><th>提问</th><th>页面</th><th>操作</th></tr></thead><tbody>{saved?.items.map((item, index) => <tr key={item.id}><td>{item.pinned ? '★ ' : ''}{item.label}</td><td>{names[item.page]}</td><td><div className="toolbar"><button type="button" className="link-button" disabled={locked} onClick={() => { setDraft(item); setExisting(true); setRemove(null) }}>编辑</button><button type="button" className="link-button" disabled={locked} onClick={() => copy(item)}>复制</button><button type="button" className="link-button" disabled={locked || index === 0} onClick={() => { const values = [...saved.items]; [values[index - 1], values[index]] = [values[index], values[index - 1]]; void persist(values) }}>上移并保存</button><button type="button" className="link-button" disabled={locked || index === saved.items.length - 1} onClick={() => { const values = [...saved.items]; [values[index + 1], values[index]] = [values[index], values[index + 1]]; void persist(values) }}>下移并保存</button><button type="button" className="link-button" disabled={locked} onClick={() => setRemove(item)}>删除</button></div></td></tr>)}</tbody></table></div>
      {remove && <div className="alert"><p>确认删除快捷提问“{remove.label}”？既有调用中的冻结问题不变。</p><button type="button" className="button secondary" disabled={locked} onClick={() => { if (saved) void persist(saved.items.filter(item => item.id !== remove.id)) }}><Icon name="check" />确认删除快捷提问</button><button type="button" className="button secondary" onClick={() => setRemove(null)}><Icon name="close" />取消</button></div>}
      <div className="toolbar"><button type="button" className="button secondary" disabled={locked} onClick={() => { setDraft(fresh()); setExisting(false) }}><Icon name="add" />新建快捷提问</button>{saved?.system_items.map(item => <button key={item.id} type="button" className="link-button" disabled={locked} onClick={() => copy(item)}>复制内置“{item.label}”</button>)}</div>
      <fieldset disabled={locked} style={{ border: 0, padding: 0, minWidth: 0 }}><legend>{existing ? '编辑自定义提问' : '新增自定义提问'}</legend><div className="form-grid"><label className="field"><span>快捷提问名称</span><input value={draft.label} maxLength={80} onChange={event => setDraft(current => ({ ...current, label: event.target.value }))} /></label><label className="field"><span>适用页面</span><select value={draft.page} onChange={event => setDraft(current => ({ ...current, page: event.target.value }))}>{(saved?.pages || Object.keys(names)).map(value => <option key={value} value={value}>{names[value]}</option>)}</select></label><label className="check-field"><input type="checkbox" checked={draft.pinned} onChange={event => setDraft(current => ({ ...current, pinned: event.target.checked }))} />置顶到各页面</label></div><label className="field"><span>快捷提问正文</span><textarea value={draft.prompt} maxLength={12000} rows={4} onChange={event => setDraft(current => ({ ...current, prompt: event.target.value }))} /></label><div className="toolbar"><button type="button" className="button primary" disabled={!draft.label.trim() || !draft.prompt.trim()} onClick={() => void saveDraft()}><Icon name="save" />保存快捷提问</button><button type="button" className="button secondary" onClick={() => copy(draft)}><Icon name="copy" />复制当前草稿为新提问</button></div></fieldset>
      <button type="button" className="button secondary" disabled={busy} onClick={async () => { try { setHistory(await api('/ai/quick-prompts/history')) } catch (err) { setError((err as Error).message) } }}>查看快捷提问历史</button>{history.map(item => <details key={item.id}><summary>版本 {item.after.revision} · {item.created_at}</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 240, overflow: 'auto' }}>{JSON.stringify(item.after.items, null, 2)}</pre></details>)}
    </details>
  </section>
}
