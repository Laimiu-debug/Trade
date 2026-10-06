import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { marketSymbolKey } from './market-symbols'
import { Icon } from './workspace-icons'

type Annotation = { symbol: string; start_date: string; stage: 'Early' | 'Mid' | 'Late'; trend_class: 'A' | 'A_B' | 'B' | 'Unknown'; decision: '保留' | '排除'; notes: string; revision: number; updated_at: string }
type Form = Pick<Annotation, 'start_date' | 'stage' | 'trend_class' | 'decision' | 'notes'>
type LocalDraft = { form: Form; base_revision: number }
const initialForm = (lastDate: string): Form => ({ start_date: lastDate, stage: 'Early', trend_class: 'Unknown', decision: '保留', notes: '' })
const fromSaved = (saved: Annotation | null, lastDate: string): Form => saved ? { start_date: saved.start_date, stage: saved.stage, trend_class: saved.trend_class, decision: saved.decision, notes: saved.notes } : initialForm(lastDate)
function storedDraft(key: string): LocalDraft | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null')
    if (value && Number.isInteger(value.base_revision) && value.base_revision >= 0 && typeof value.form?.start_date === 'string' && typeof value.form.notes === 'string' && ['Early', 'Mid', 'Late'].includes(value.form.stage) && ['A', 'A_B', 'B', 'Unknown'].includes(value.form.trend_class) && ['保留', '排除'].includes(value.form.decision)) return value
  } catch { /* Broken or unavailable local storage does not replace the saved record. */ }
  return null
}

export function StockAnnotationEditor({ symbol, lastDate, onStartDate }: { symbol: string; lastDate: string; onStartDate?: (day: string | null) => void }) {
  const [saved, setSaved] = useState<Annotation | null>(null)
  const [form, setForm] = useState<Form>({ start_date: lastDate, stage: 'Early', trend_class: 'Unknown', decision: '保留', notes: '' })
  const [loading, setLoading] = useState(true)
  const [baseRevision, setBaseRevision] = useState(0)
  const [conflict, setConflict] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const sequence = useRef(0)
  const storageKey = `trade-rebuild:stock-annotation-draft:${marketSymbolKey(symbol) || symbol}`
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const dirty = JSON.stringify(form) !== JSON.stringify(fromSaved(saved, lastDate))
  function persist(next: Form, revision = baseRevision) {
    try { localStorage.setItem(storageKey, JSON.stringify({ form: next, base_revision: revision })); return true }
    catch { setError('浏览器草稿存储不可用，请保存标注后再切换页面。'); return false }
  }
  function clearDraft() { try { localStorage.removeItem(storageKey) } catch { /* A stale local copy will be compared against the saved revision on reload. */ } }
  useEffect(() => {
    const stamp = ++sequence.current
    const local = storedDraft(storageKey)
    setLoading(true); setBusy(false); setError(''); setNotice(''); setConflict(false)
    setForm(local?.form || initialForm(lastDate)); setSaved(null); setBaseRevision(local?.base_revision || 0)
    api<Annotation | null>('/market/annotations/' + encodeURIComponent(symbol)).then(row => {
      if (stamp !== sequence.current) return
      setSaved(row); onStartDate?.(row?.start_date ?? null)
      setForm(local?.form || fromSaved(row, lastDate))
      setBaseRevision(local?.base_revision ?? row?.revision ?? 0)
      setConflict(Boolean(local && local.base_revision !== (row?.revision || 0)))
      if (local) setNotice('已恢复这只证券的本机草稿，保存后会同步为正式标注')
    }).catch(err => { if (stamp === sequence.current) { setError(err.message); setConflict(true) } })
      .finally(() => { if (stamp === sequence.current) setLoading(false) })
    return () => { sequence.current += 1 }
  }, [symbol, lastDate, storageKey, onStartDate, refresh])
  function edit<K extends keyof Form>(key: K, value: Form[K]) {
    const next = { ...form, [key]: value }
    setForm(next); if (persist(next)) setNotice('草稿已保存在本机，尚未同步正式标注')
  }
  function resolve(useLocal: boolean) {
    if (useLocal) { setBaseRevision(saved?.revision || 0); persist(form, saved?.revision || 0); setNotice('已确认使用本机内容，请点击保存人工标注') }
    else { setForm(fromSaved(saved, lastDate)); setBaseRevision(saved?.revision || 0); clearDraft(); setNotice('已采用正式标注') }
    setConflict(false)
  }
  async function save() {
    const stamp = sequence.current
    let sentDraft: string | null = null
    try { sentDraft = localStorage.getItem(storageKey) } catch { /* Keep live editing available. */ }
    setBusy(true); setError('')
    try {
      const row = await api<Annotation>('/market/annotations/' + encodeURIComponent(symbol), 'PUT', { ...form, expected_revision: baseRevision })
      try { if (localStorage.getItem(storageKey) === sentDraft) clearDraft() } catch { /* Preserve any newer draft. */ }
      if (stamp !== sequence.current) return
      setSaved(row); setForm(fromSaved(row, lastDate)); setBaseRevision(row.revision); onStartDate?.(row.start_date); setNotice('人工标注已保存')
    } catch (err) { if (stamp === sequence.current) { setError(err instanceof Error ? err.message : '标注保存失败'); if ((err as Error & { code?: string }).code === 'REVISION_CONFLICT') setRefresh(value => value + 1) } }
    finally { if (stamp === sequence.current) setBusy(false) }
  }
  async function remove() {
    if (!saved || !window.confirm('删除这只股票的人工标注？')) return
    const stamp = sequence.current
    let sentDraft: string | null = null
    try { sentDraft = localStorage.getItem(storageKey) } catch { /* Storage is optional. */ }
    setBusy(true); setError('')
    try {
      await api('/market/annotations/' + encodeURIComponent(symbol) + '?expected_revision=' + saved.revision, 'DELETE')
      try { if (localStorage.getItem(storageKey) === sentDraft) clearDraft() } catch { /* Preserve any newer draft. */ }
      if (stamp !== sequence.current) return
      setBaseRevision(0); setSaved(null); onStartDate?.(null); setForm({ start_date: lastDate, stage: 'Early', trend_class: 'Unknown', decision: '保留', notes: '' }); setNotice('人工标注已删除')
    } catch (err) { if (stamp === sequence.current) setError(err instanceof Error ? err.message : '标注删除失败') }
    finally { if (stamp === sequence.current) setBusy(false) }
  }
  return <div className="market-detail"><h3>人工启动日与阶段标注</h3><p className="muted">按证券代码独立保存，人工记录不会被研究运行或 AI 结果覆盖。{loading ? '读取中…' : dirty ? '有未保存修改' : saved ? `已保存 · 版本 ${saved.revision} · ${saved.updated_at}` : '尚未保存'}</p>{error && <p className="alert error">{error}</p>}{notice && <p className="alert success">{notice}</p>}<fieldset disabled={loading || busy} style={{ border: 0, padding: 0, minWidth: 0 }}><div className="form-grid"><label className="field">人工启动日<input type="date" value={form.start_date} onChange={event => edit('start_date', event.target.value)} /></label><label className="field">阶段<select value={form.stage} onChange={event => edit('stage', event.target.value as Form['stage'])}><option value="Early">早期</option><option value="Mid">中期</option><option value="Late">后期</option></select></label><label className="field">形态<select value={form.trend_class} onChange={event => edit('trend_class', event.target.value as Form['trend_class'])}><option value="Unknown">未明</option><option value="A">A</option><option value="A_B">A/B</option><option value="B">B</option></select></label><label className="field">决定<select value={form.decision} onChange={event => edit('decision', event.target.value as Form['decision'])}><option value="保留">保留</option><option value="排除">排除</option></select></label></div><label className="field">人工备注<textarea rows={3} value={form.notes} onChange={event => edit('notes', event.target.value)} /></label><div className="form-actions"><button className="button secondary" disabled={conflict || loading || busy || !form.start_date || (!dirty && Boolean(saved))} onClick={save}><Icon name="save" />保存人工标注</button>{saved && <button className="button ghost danger" disabled={busy || conflict} onClick={remove}><Icon name="delete" />删除标注</button>}</div>{conflict && <div className="alert error" role="alert"><p>本机草稿与正式记录版本不同，或正式记录暂时无法读取。请核对后选择；未保存的内容仍保留在本机。</p><details><summary>查看正式标注</summary><p>{saved ? `${saved.start_date} · ${saved.stage} · ${saved.trend_class} · ${saved.decision}` : '尚无正式标注或读取失败'}</p><p>{saved?.notes}</p></details><button type="button" className="button secondary" onClick={() => setRefresh(value => value + 1)}><Icon name="refresh" />重新读取</button>{!error && <><button type="button" className="button secondary" onClick={() => resolve(false)}>采用正式标注</button><button type="button" className="button secondary" onClick={() => resolve(true)}>以最新版本继续编辑本机草稿</button></>}</div>}</fieldset></div>
}
