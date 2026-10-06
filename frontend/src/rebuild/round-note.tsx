import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { useReviewBuffer } from './review-drafts'
import { Icon } from './workspace-icons'

type Note = { round_id: string; summary: string; revision: number; round_exists: boolean;
  association_changed: boolean; linked_trade_ids: string[]; current_trade_ids: string[];
  projection_status: string; related: { daily: Array<{ date: string; title: string; decision_review: string; mistakes: string }>;
    periods: Array<{ kind: string; period_key: string; sections: Record<string, string> }> } }
type LocalNote = { base_revision: number; summary: string }
function parseLocal(raw: string): LocalNote {
  const value = JSON.parse(raw) as LocalNote
  if (!value || !Number.isInteger(value.base_revision) || value.base_revision < 0 || typeof value.summary !== 'string') throw new Error('回合草稿格式无效')
  return value
}

export function RoundNoteEditor({ accountId, roundId, onSaved }: { accountId: string; roundId: string; onSaved: () => void }) {
  const [note, setNote] = useState<Note | null>(null)
  const [summary, setSummary] = useState('')
  const [revision, setRevision] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [conflict, setConflict] = useState(false)
  const [reload, setReload] = useState(0)
  const scope = useRef(0)
  const path = `/accounts/${accountId}/round-notes/${encodeURIComponent(roundId)}`
  const buffer = useReviewBuffer<LocalNote>('trade-rebuild:round-draft:' + path, parseLocal)
  const current = useRef(buffer); current.current = buffer
  useEffect(() => {
    const stamp = ++scope.current, local = current.current.get().value
    setNote(null); setBusy(false); setError(''); setStatus(local ? '已恢复本机回合草稿，正在核对正式版本…' : '')
    setSummary(local?.summary || ''); setRevision(local?.base_revision || 0); setDirty(Boolean(local)); setConflict(false)
    api<Note>(path).then(value => {
      if (scope.current !== stamp) return
      const latest = current.current.get().value
      setNote(value); setSummary(latest?.summary ?? value.summary); setRevision(latest?.base_revision ?? value.revision)
      setDirty(Boolean(latest)); setConflict(Boolean(latest && latest.base_revision !== value.revision))
    }).catch(err => { if (scope.current === stamp) setError('正式回合摘要读取失败；本机草稿仍可编辑。' + err.message) })
    return () => { ++scope.current }
  }, [path, reload])
  useEffect(() => {
    if (!dirty) return
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  async function save() {
    if (!note || busy || conflict || buffer.conflict || note.projection_status !== 'fresh') return
    const stamp = scope.current, raw = buffer.get().raw
    setBusy(true); setError(''); setStatus('正在保存回合摘要…')
    try {
      const saved = await api<Note>(path, 'PUT', { expected_revision: revision, summary })
      if (scope.current !== stamp) return
      setNote(saved)
      if (current.current.clear(raw)) { setSummary(saved.summary); setRevision(saved.revision); setDirty(false); setStatus('回合摘要已保存') }
      else { setConflict(true); setStatus('正文已保存，本机草稿已变化，请核对后继续。') }
      onSaved()
    } catch (err) {
      if (scope.current !== stamp) return
      setError(err instanceof Error ? err.message : '保存失败，草稿已保留')
      if ((err as Error & { code?: string }).code?.includes('CONFLICT')) {
        setConflict(true)
        try { const saved = await api<Note>(path); if (scope.current === stamp) setNote(saved) }
        catch { if (scope.current === stamp) setNote(null) }
      }
    } finally { if (scope.current === stamp) setBusy(false) }
  }
  return <div className="market-detail"><h3>回合摘要</h3>{error && <p role="alert" className="danger">{error}</p>}{status && <p role="status">{status}</p>}
    {buffer.warning && <p role="alert" className="danger">{buffer.warning}</p>}
    {buffer.conflict && <div className="alert error"><p>另一页面的回合草稿已变化；当前输入保留。</p><button className="button" onClick={() => {
      const other = buffer.resolve(false)
      if (other === undefined) return
      setSummary(other?.summary ?? note?.summary ?? ''); setRevision(other?.base_revision ?? note?.revision ?? 0); setDirty(Boolean(other)); setConflict(Boolean(other && note && other.base_revision !== note.revision))
    }}>采用另一页面回合草稿</button><button className="button" onClick={() => buffer.write({ base_revision: revision, summary }, true)}>保留本页回合草稿</button></div>}
    {!note && <button className="button secondary" onClick={() => setReload(value => value + 1)}><Icon name="refresh" />重新读取正式回合摘要</button>}
    {note && <><p className="muted">摘要版本 {note.revision} · 关联成交 {note.current_trade_ids.length} 笔 · {note.round_exists ? '当前回合仍存在' : '原回合已因交易修订消失，摘要保留'}</p>{note.association_changed && <p className="danger">交易修订改变了该回合的关联成交。原关联 {note.linked_trade_ids.length} 笔，当前关联 {note.current_trade_ids.length} 笔，请核对后重新保存摘要。</p>}{note.projection_status !== 'fresh' && <p className="muted">交易统计正在更新，待重算完成后可保存。</p>}</>}
    {conflict && <div className="alert error"><p>正式摘要已更新，请比较后明确继续。</p><p style={{ whiteSpace: 'pre-wrap' }}>{note?.summary}</p><button className="button" disabled={!note || buffer.conflict} onClick={() => { if (note) { buffer.clear(buffer.get().raw); setSummary(note.summary); setRevision(note.revision); setDirty(false); setConflict(false) } }}>采用正式回合摘要</button><button className="button" disabled={!note || buffer.conflict} onClick={() => { if (note) { setRevision(note.revision); buffer.write({ base_revision: note.revision, summary }); setConflict(false) } }}>以最新版本继续编辑回合草稿</button></div>}
    {(note || buffer.value) && <><label className="field">人工回合摘要<textarea rows={5} disabled={busy} value={summary} onChange={event => { setSummary(event.target.value); setDirty(true); buffer.write({ base_revision: revision, summary: event.target.value }); setStatus('回合草稿已保留，尚未提交') }} /></label><div className="form-actions"><button className="button primary" disabled={busy || !note || conflict || buffer.conflict || note.projection_status !== 'fresh'} onClick={save}><Icon name="save" />保存回合摘要</button></div></>}
    {note && (note.related.daily.length > 0 || note.related.periods.length > 0) && <details><summary>相关复盘摘录</summary>{note.related.daily.map(row => <div key={row.date}><strong>{row.date} · {row.title}</strong><p>{row.decision_review || row.mistakes || '该日尚无决策正文'}</p></div>)}{note.related.periods.map(row => <div key={`${row.kind}:${row.period_key}`}><strong>{row.kind === 'weekly' ? '周' : '月'} · {row.period_key}</strong><p>{Object.values(row.sections).filter(Boolean).join('；').slice(0, 800) || '暂无正文'}</p></div>)}</details>}
  </div>
}
