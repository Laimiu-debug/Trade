import { useEffect, useState } from 'react'
import { api } from './api'

type Source = { key: string; source_path: string; review_date: string; status: 'needs_file' | 'blocked' | 'mapped'; reason: string; mapping?: { id: string } }
type Catalog = { expected_revision: number; account_id: string | null; items: Source[]; notes: string[] }
type FileData = { source_key: string; filename: string; content_base64: string; byte_size: number }
type Preview = Catalog & { preview_sha256: string; can_apply: boolean; selected: Array<Source & { target_revision: number; file: { filename: string; sha256: string; byte_size: number; mime_type: string; width: number; height: number }; existing: unknown[] }> }

export function LegacyAttachmentEditor({ importId, onChanged, onBusy }: { importId: string; onChanged: () => Promise<unknown>; onBusy?: (busy: boolean) => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [files, setFiles] = useState<Record<string, FileData>>({})
  const [preview, setPreview] = useState<Preview | null>(null)
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState(0)
  const root = `/legacy-imports/${importId}/attachments`
  useEffect(() => { let alive = true; api<Catalog>(root).then(value => { if (alive) setCatalog(value) }).catch(err => { if (alive) setError(err.message) }); return () => { alive = false } }, [root])
  async function work(action: () => Promise<void>) {
    setBusy(true); onBusy?.(true); setError('')
    try { await action() } catch (err) { setError(err instanceof Error ? err.message : '附件迁入失败') } finally { setBusy(false); onBusy?.(false) }
  }
  async function read(key: string, file: File) {
    if (!file.size || file.size > 8 * 1024 * 1024) throw new Error('每张图片须为 1 B–8 MiB')
    const bytes = new Uint8Array(await file.arrayBuffer()); let binary = ''
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768))
    const next = { ...files, [key]: { source_key: key, filename: file.name, content_base64: btoa(binary), byte_size: file.size } }
    if (Object.keys(next).length > 20 || Object.values(next).reduce((sum, item) => sum + item.byte_size, 0) > 24 * 1024 * 1024) throw new Error('每批最多 20 张图片、合计 24 MiB')
    setFiles(next); setPreview(null); setAck(false); setNotice('')
  }
  function request() { return { expected_revision: catalog?.expected_revision, files: Object.values(files).map(({ source_key, filename, content_base64 }) => ({ source_key, filename, content_base64 })) } }
  return <section className="market-detail"><h3>旧复盘图片 · 显式文件对应</h3>{error && <p role="alert" className="alert error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {catalog?.notes.map(note => <p key={note} className="subtle">{note}</p>)}
    <button className="button secondary" disabled={busy} onClick={() => void work(async () => { setCatalog(await api<Catalog>(root)); setPreview(null); setAck(false) })}>刷新附件来源与目标版本</button>
    {catalog && <><p>旧路径 {catalog.items.length} 项 · 本批已选择 {Object.keys(files).length} 个文件。未选择的文件不会上传。</p>
      <div className="table-wrap"><table><thead><tr><th>旧日期 / 路径（仅文字）</th><th>文件对应与状态</th></tr></thead><tbody>{catalog.items.slice(page * 25, (page + 1) * 25).map(item => <tr key={item.key}><td style={{ overflowWrap: 'anywhere', maxWidth: '24rem' }}>{item.review_date}<br />{item.source_path}</td><td>{item.status === 'needs_file' ? <><label className="field"><span>为 {item.key} 选择图片</span><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) void work(() => read(item.key, file)); event.target.value = '' }} /></label>{files[item.key] ? <p>{files[item.key].filename} · {files[item.key].byte_size} B <button className="link-button" disabled={busy} onClick={() => { setFiles(current => Object.fromEntries(Object.entries(current).filter(([key]) => key !== item.key))); setPreview(null); setAck(false) }}>取消选择</button></p> : <span className="muted">尚未提供文件</span>}</> : item.status === 'mapped' ? '已映射 · ' + item.mapping?.id : item.reason}</td></tr>)}</tbody></table></div>
      {catalog.items.length > 25 && <div className="toolbar"><button className="button secondary" disabled={page === 0} onClick={() => setPage(current => current - 1)}>上一组图片</button><span>{page + 1} / {Math.ceil(catalog.items.length / 25)}</span><button className="button secondary" disabled={(page + 1) * 25 >= catalog.items.length} onClick={() => setPage(current => current + 1)}>下一组图片</button></div>}
      <button className="button secondary" disabled={busy || !Object.keys(files).length} onClick={() => void work(async () => { setPreview(await api<Preview>(root + '/preview', 'POST', request())); setAck(false) })}>预览所选图片映射</button>
    </>}
    {preview && <div><h4>图片映射预览</h4>{preview.selected.map(item => <div key={item.key} style={{ overflowWrap: 'anywhere' }}><p>{item.source_path} → {item.file.filename} → {item.review_date} 日复盘 v{item.target_revision}</p><p className="subtle">{item.file.mime_type} · {item.file.width}×{item.file.height} · {item.file.byte_size} B<br />SHA256：{item.file.sha256}</p></div>)}
      <label className="check-field"><input type="checkbox" disabled={busy} checked={ack} onChange={event => setAck(event.target.checked)} />我已核对文件内容与旧路径对应，确认迁入所选图片</label>
      <button className="button primary" disabled={busy || !ack || !preview.can_apply} onClick={() => void work(async () => {
        await api(root + '/apply', 'POST', { ...request(), expected_preview_sha256: preview.preview_sha256, acknowledge_limitations: ack })
        setFiles({}); setPreview(null); setAck(false); setNotice('所选图片已迁入，原文件和复盘正文保持。'); setCatalog(await api<Catalog>(root)); await onChanged()
      })}>确认迁入这批图片</button>
    </div>}
  </section>
}
