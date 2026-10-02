import { useCallback, useEffect, useRef, useState } from 'react'
import { api, apiUpload, type Review } from './api'
import { ReviewScores } from './review-scores'
import { PlanComparison } from './plan-comparison'
import { PlanDateControl, PositionRehearsal } from './plan-rehearsal'
import { printPreviewUrl } from './print-preview'
import { useReviewBuffer } from './review-drafts'

type Field = 'title' | 'market_observation' | 'decision_review' | 'mistakes' | 'tomorrow_plan' | 'overall_summary' | 'reflection' | 'next_market_forecast' | 'next_position_plan' | 'next_risk_plan'
type Draft = Pick<Review, Field | 'tags' | 'next_watchlist' | 'next_position_rehearsal' | 'next_target_date' | 'revision' | 'review_date'>
type Stored = { draft: Draft; saved_at: string }
type Gap = { date: string; has_trades: boolean; has_snapshot: boolean }
type History = { date: string; title: string; tags: string[]; revision: number }
type Attachment = { id: string; url: string; original_name: string; byte_size: number; revision: number }

const empty = (day: string): Draft => ({ review_date: day, revision: 0, title: '',
  market_observation: '', decision_review: '', mistakes: '', tomorrow_plan: '', overall_summary: '',
  reflection: '', tags: [], next_market_forecast: '', next_watchlist: [],
  next_position_plan: '', next_risk_plan: '', next_position_rehearsal: [], next_target_date: null })
const keyFor = (accountId: string, day: string) => `trade-rebuild:daily-draft:${accountId}:${day}`

function parseStored(raw: string): Stored {
  const value = JSON.parse(raw) as Stored
  const item = value?.draft
  if (!item || typeof item.review_date !== 'string' || !Number.isInteger(item.revision) || item.revision < 0 ||
      Object.keys(empty(item.review_date)).some(key => typeof empty(item.review_date)[key as Field] === 'string' && typeof item[key as Field] !== 'string') ||
      !Array.isArray(item.tags) || item.tags.some(tag => typeof tag !== 'string') ||
      !Array.isArray(item.next_watchlist) || item.next_watchlist.some(row => !row || ['code', 'name', 'condition', 'action'].some(key => typeof row[key as 'code'] !== 'string')) ||
      !Array.isArray(item.next_position_rehearsal) || item.next_position_rehearsal.some(row => !row || typeof row.code !== 'string')) throw new Error('日复盘草稿格式无效')
  return { ...value, draft: { ...empty(item.review_date), ...item } }
}

export function DailyReviewEditor({ accountId, initialDate }: { accountId: string; initialDate?: string }) {
  const [day, setDay] = useState(() => initialDate || new Date().toLocaleDateString('sv-SE'))
  const [draft, setDraft] = useState<Draft | null>(null)
  const [gaps, setGaps] = useState<Gap[]>([])
  const [history, setHistory] = useState<History[]>([])
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [tagText, setTagText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [retryPaused, setRetryPaused] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState<Review | null>(null)
  const sequence = useRef(0)
  const requestScope = useRef(0)
  const storageKey = keyFor(accountId, day)
  const buffer = useReviewBuffer<Stored>(storageKey, parseStored)
  const bufferRef = useRef(buffer); bufferRef.current = buffer
  const [formal, setFormal] = useState<Review | null>(null)
  const [verified, setVerified] = useState(false)
  const [reload, setReload] = useState(0)
  const loadedKey = useRef('')
  const refreshGaps = useCallback(async () => {
    const stamp = requestScope.current
    const rows = await api<Gap[]>(`/accounts/${accountId}/review-gaps`)
    if (requestScope.current === stamp) setGaps(rows)
  }, [accountId])
  const refreshHistory = useCallback(async () => {
    const stamp = requestScope.current
    const rows = await api<History[]>(`/accounts/${accountId}/daily-reviews?limit=100`)
    if (requestScope.current === stamp) setHistory(rows)
  }, [accountId])
  const refreshAttachments = useCallback(async () => {
    const stamp = requestScope.current
    const rows = await api<Attachment[]>(`/accounts/${accountId}/daily-reviews/${day}/attachments`)
    if (requestScope.current === stamp) setAttachments(rows)
  }, [accountId, day])

  useEffect(() => {
    const stamp = ++requestScope.current
    loadedKey.current = storageKey
    const local = bufferRef.current.get().value
    const fields = local?.draft.review_date === day ? { ...empty(day), ...local.draft } : null
    setDraft(fields); setTagText(fields?.tags.join('，') || ''); setDirty(Boolean(fields))
    setBusy(false); setUploading(false); setRetryPaused(false); setConflict(null); setFormal(null); setVerified(false); setError('')
    setStatus(fields ? '已恢复本机草稿，正在核对正式版本…' : '正在读取…')
    setGaps([]); setHistory([]); setAttachments([])
    api<Review | null>(`/accounts/${accountId}/daily-reviews/${day}`).then(server => {
      if (requestScope.current !== stamp) return
      const latest = bufferRef.current.get().value
      const current = latest?.draft.review_date === day ? { ...empty(day), ...latest.draft } : null
      setFormal(server); setVerified(true)
      if (current) {
        setDraft(current); setTagText(current.tags.join('，')); setDirty(true)
        if (current.revision !== (server?.revision ?? 0)) { setConflict(server || empty(day) as Review); setStatus('版本冲突，请核对后继续') }
        else setStatus('本机草稿待保存')
      } else { const saved = { ...empty(day), ...server }; setDraft(saved); setTagText(saved.tags.join('，')); setStatus(server ? '已保存' : '尚未填写') }
    }).catch(err => { if (requestScope.current === stamp) { setError(err.message); setStatus('正式记录读取失败；本机草稿仍可编辑，联网后先核对版本再保存') } })
    void Promise.allSettled([refreshGaps(), refreshHistory(), refreshAttachments()])
    return () => { ++requestScope.current; loadedKey.current = '' }
  }, [accountId, day, storageKey, reload, refreshGaps, refreshHistory, refreshAttachments])

  const save = useCallback(async (overrideRevision?: number) => {
    const cache = bufferRef.current
    if (!draft || busy || !verified || cache.conflict || (conflict && overrideRevision === undefined) || loadedKey.current !== storageKey) return
    if (draft.next_watchlist.some(row => !row.code.trim()) || draft.next_position_rehearsal.some(row => !row.code.trim())) {
      setStatus('关注股或持仓预演有未填写代码的行，草稿已保留')
      return
    }
    const started = sequence.current, stamp = requestScope.current, sentRaw = cache.get().raw
    setBusy(true); setError(''); setStatus('正在保存…')
    try {
      const saved = await api<Review>(`/accounts/${accountId}/daily-reviews/${day}`, 'PUT',
        { ...draft, expected_revision: overrideRevision ?? draft.revision })
      if (requestScope.current !== stamp || loadedKey.current !== storageKey) return
      setFormal(saved); setConflict(null); setRetryPaused(false)
      const latest = bufferRef.current
      if (latest.get().conflict) { setStatus('正式正文已保存；另一页面草稿变更待核对'); return }
      if (sequence.current === started && latest.clear(sentRaw)) {
        setDraft({ ...empty(day), ...saved }); setDirty(false); setStatus('已保存')
      } else {
        const current = latest.get().value?.draft
        if (current) { const next = { ...current, revision: saved.revision }; latest.write({ draft: next, saved_at: new Date().toISOString() }); setDraft(next) }
        setStatus('新输入待保存')
      }
      void Promise.allSettled([refreshGaps(), refreshHistory()])
    } catch (err) {
      if (requestScope.current !== stamp) return
      const failure = err as Error & { code?: string }
      if (failure.code === 'REVISION_CONFLICT') {
        setRetryPaused(true)
        try { const server = await api<Review | null>(`/accounts/${accountId}/daily-reviews/${day}`); if (requestScope.current === stamp) { setFormal(server); setConflict(server || empty(day) as Review) } }
        catch { if (requestScope.current === stamp) setVerified(false) }
        if (requestScope.current === stamp) setStatus('版本冲突，草稿已保留')
      } else { setStatus('保存失败，草稿已保留'); setRetryPaused(true) }
      if (requestScope.current === stamp) setError(failure.message)
    } finally { if (requestScope.current === stamp) setBusy(false) }
  }, [accountId, day, draft, busy, verified, conflict, storageKey, refreshGaps, refreshHistory])

  useEffect(() => {
    if (!dirty || busy || conflict || buffer.conflict || retryPaused || !verified || !draft || loadedKey.current !== storageKey) return
    const timer = window.setTimeout(() => { void save() }, 1200)
    return () => window.clearTimeout(timer)
  }, [dirty, busy, conflict, buffer.conflict, retryPaused, verified, draft, storageKey, save])
  useEffect(() => {
    const retry = () => { if (!verified) setReload(value => value + 1); else if (dirty && !conflict && !buffer.conflict) { setRetryPaused(false); void save() } }
    window.addEventListener('online', retry)
    return () => window.removeEventListener('online', retry)
  }, [dirty, conflict, buffer.conflict, verified, save])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  function update(next: Draft) {
    if (!draft) return
    sequence.current += 1
    setDraft(next); setDirty(true); setStatus('本机草稿待保存')
    buffer.write({ draft: next, saved_at: new Date().toISOString() })
  }
  function edit(field: Field, value: string) { if (draft) update({ ...draft, [field]: value }) }
  function loadServer() {
    if (!conflict || buffer.conflict) return
    buffer.clear(buffer.get().raw)
    sequence.current += 1
    setDraft({ ...empty(day), ...conflict }); setConflict(null); setDirty(false); setError(''); setStatus('已加载服务器版本')
    setTagText(conflict.tags.join('，'))
  }
  function resolveLocal(keep: boolean) {
    if (keep && draft) { buffer.write({ draft, saved_at: new Date().toISOString() }, true); return }
    const other = buffer.resolve(false)
    if (other === undefined) return
    const next = { ...empty(day), ...(other?.draft || formal) }
    setDraft(next); setTagText(next.tags.join('，')); setDirty(Boolean(other)); ++sequence.current
    setConflict(other && verified && next.revision !== (formal?.revision ?? 0) ? formal || empty(day) as Review : null)
  }

  async function upload(file: File) {
    const stamp = requestScope.current
    setUploading(true); setError('')
    try {
      await apiUpload(`/accounts/${accountId}/daily-reviews/${day}/attachments`, file)
      if (requestScope.current !== stamp) return
      await refreshAttachments()
      if (requestScope.current === stamp) setStatus('图片已保存')
    } catch (err) { if (requestScope.current === stamp) setError(err instanceof Error ? err.message : '图片上传失败') }
    finally { if (requestScope.current === stamp) setUploading(false) }
  }

  async function removeAttachment(item: Attachment) {
    const stamp = requestScope.current
    setError('')
    try {
      await api(`/accounts/${accountId}/review-attachments/${item.id}?expected_revision=${item.revision}`, 'DELETE')
      if (requestScope.current !== stamp) return
      await refreshAttachments()
      if (requestScope.current === stamp) setStatus('图片已移除')
    } catch (err) { if (requestScope.current === stamp) setError(err instanceof Error ? err.message : '图片移除失败') }
  }

  return <><div className="two-col wide-left"><section className="card reading" onPaste={event => { const file = [...event.clipboardData.files].find(item => item.type.startsWith('image/')); if (file) { event.preventDefault(); upload(file).catch(() => {}) } }}><div className="section-heading"><div><h2>每日复盘</h2><p>输入会先保存在本机，并自动同步到正式记录。</p></div><input aria-label="复盘日期" type="date" value={day} onChange={event => setDay(event.target.value)} /></div>
    <p role="status" className="muted">{status}</p>{error && <p role="alert" className="danger">{error}</p>}
    {buffer.warning && <p role="alert" className="danger">{buffer.warning}</p>}
    {buffer.conflict && <div className="alert error"><p>另一页面的本机草稿已变化；自动同步暂停，当前输入仍保留。</p><button className="button" onClick={() => resolveLocal(false)}>采用另一页面日草稿</button><button className="button" onClick={() => resolveLocal(true)}>保留本页日草稿</button></div>}
    {!verified && <button className="button secondary" disabled={busy} onClick={() => setReload(value => value + 1)}>重新读取正式日复盘</button>}
    {conflict && <div className="alert error"><span>服务器上有更新，请核对两份内容。当前输入已保留。</span><details><summary>查看服务器日复盘（第 {conflict.revision} 版）</summary>{(['title', 'overall_summary', 'market_observation', 'decision_review', 'reflection', 'mistakes', 'tomorrow_plan', 'next_market_forecast', 'next_position_plan', 'next_risk_plan'] as const).map(field => <p key={field} style={{ whiteSpace: 'pre-wrap' }}>{conflict[field]}</p>)}<pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ tags: conflict.tags, next_target_date: conflict.next_target_date, next_watchlist: conflict.next_watchlist, next_position_rehearsal: conflict.next_position_rehearsal }, null, 2)}</pre></details><div className="form-actions"><button className="button secondary" disabled={busy || buffer.conflict} onClick={loadServer}>加载服务器版本</button><button className="button secondary" disabled={busy || buffer.conflict || !verified} onClick={() => save(conflict.revision)}>用本机草稿覆盖</button></div></div>}
    {draft && <form className="form" onSubmit={event => { event.preventDefault(); save().catch(() => {}) }}>
      <label className="field">标题<input value={draft.title} onChange={event => edit('title', event.target.value)} /></label>
      {([['overall_summary', '当日总述'], ['market_observation', '市场观察'], ['decision_review', '决策复盘'], ['reflection', '通用反思'], ['mistakes', '错误与改进'], ['tomorrow_plan', '明日计划']] as const).map(([field, label]) => <label className="field" key={field}>{label}<textarea rows={4} value={draft[field]} onChange={event => edit(field, event.target.value)} /></label>)}
      <label className="field">复盘标签（逗号分隔）<input value={tagText} onChange={event => { setTagText(event.target.value); update({ ...draft, tags: event.target.value.split(/[,，]/).map(value => value.trim()).filter(Boolean) }) }} /></label>
      <h3>次日预案</h3>
      <PlanDateControl day={day} value={draft.next_target_date} onChange={value => update({ ...draft, next_target_date: value })} />
      {([['next_market_forecast', '大盘预判'], ['next_position_plan', '仓位计划'], ['next_risk_plan', '风险预案']] as const).map(([field, label]) => <label className="field" key={field}>{label}<textarea rows={3} value={draft[field]} onChange={event => edit(field, event.target.value)} /></label>)}
      <div className="section-heading"><h3>关注股与触发动作</h3><button className="button secondary" type="button" onClick={() => update({ ...draft, next_watchlist: [...draft.next_watchlist, { code: '', name: '', condition: '', action: '' }] })}>添加关注股</button></div>
      {draft.next_watchlist.map((row, index) => <div className="form-grid" key={index}>{(['code', 'name', 'condition', 'action'] as const).map(key => <label className="field" key={key}>{({ code: '代码', name: '名称', condition: '触发条件', action: '动作' })[key]}<input value={row[key]} onChange={event => update({ ...draft, next_watchlist: draft.next_watchlist.map((item, i) => i === index ? { ...item, [key]: event.target.value } : item) })} /></label>)}<button className="link-button danger" type="button" onClick={() => update({ ...draft, next_watchlist: draft.next_watchlist.filter((_, i) => i !== index) })}>移除</button></div>)}
      <PositionRehearsal key={`${accountId}:${day}`} accountId={accountId} day={day} targetDate={draft.next_target_date} rows={draft.next_position_rehearsal} onChange={rows => update({ ...draft, next_position_rehearsal: rows })} />
      <button className="button primary" disabled={busy || !dirty || Boolean(conflict) || buffer.conflict || !verified}>立即保存</button>{draft.revision > 0 && <a className="button" href={`/api/v1/accounts/${accountId}/exports/review/daily/${day}.md`} download>导出已保存的 Markdown</a>}{draft.revision > 0 && <a className="button" href={`/api/v1/accounts/${accountId}/exports/review/daily/${day}.pdf`} download>导出复盘 PDF</a>}{draft.revision > 0 && <a className="button" href={printPreviewUrl(accountId, 'daily', day)} target="_blank" rel="noopener noreferrer">浏览器打印</a>}
    </form>}
    <div className="market-detail"><h3>复盘截图</h3><p className="muted">可选择图片或直接粘贴截图；每张最多 8 MB，支持 PNG、JPEG、WebP、GIF。图片按当前账户和复盘日期保存。</p><label className="field">选择截图<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={uploading} onChange={event => { const file = event.target.files?.[0]; if (file) upload(file).catch(() => {}); event.target.value = '' }} /></label><div className="inspiration-grid">{attachments.map(item => <figure className="review-image" key={item.id}><img src={item.url} alt={item.original_name} loading="lazy" /><figcaption>{item.original_name} · {(item.byte_size / 1024).toFixed(1)} KB <button className="link-button danger" type="button" onClick={() => { if (window.confirm(`移除截图“${item.original_name}”？`)) removeAttachment(item).catch(() => {}) }}>移除</button></figcaption></figure>)}</div></div>
  </section><div><section className="card"><h2>待补复盘日期</h2><p className="muted">有交易或资产快照，但尚无每日复盘的日期。</p><div className="table-wrap"><table><thead><tr><th>日期</th><th>来源</th><th>操作</th></tr></thead><tbody>{gaps.map(row => <tr key={row.date}><td>{row.date}</td><td>{[row.has_trades && '交易', row.has_snapshot && '资产'].filter(Boolean).join('、')}</td><td><button className="link-button" onClick={() => setDay(row.date)}>填写</button></td></tr>)}</tbody></table></div>{!gaps.length && <p className="muted">当前没有待补日期</p>}</section><section className="card"><h2>历史日复盘</h2><div className="table-wrap"><table><thead><tr><th>日期</th><th>标题</th></tr></thead><tbody>{history.map(row => <tr key={row.date}><td><button className="link-button" onClick={() => setDay(row.date)}>{row.date}</button></td><td>{row.title || '未命名'}</td></tr>)}</tbody></table></div>{!history.length && <p className="muted">暂无已保存日复盘</p>}</section></div></div>
  <ReviewScores accountId={accountId} day={day} />
  <PlanComparison accountId={accountId} day={day} />
  </>
}
