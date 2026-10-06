import { useEffect, useState } from 'react'
import { api, apiUpload } from './api'
import { DirectoryPicker } from './directory-picker'
import { LifecycleControl } from './lifecycle-control'
import { Icon } from './workspace-icons'

type Storage = { data_dir: string; free_bytes: number; usage: Record<string, { file_count: number; bytes: number }>; migration_versions: string[]; background_errors: Record<string, string> }
type Preview = { fingerprint: string; format: string; database_bytes: number; total_bytes: number; dataset_count: number; attachment_count: number; destination?: string }
const size = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(2)} MiB`
function BackupSummary({ value }: { value: Preview }) { return <div className="market-detail"><p>已校验 {value.format} · 数据合计 {size(value.total_bytes)}</p><p>{value.dataset_count} 份行情 · {value.attachment_count} 个附件 · 业务记录与研究结果随数据库保存</p><small style={{ overflowWrap: 'anywhere' }}>内容摘要 {value.fingerprint}</small></div> }

export function SystemSettings() {
  const [storage, setStorage] = useState<Storage | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [backup, setBackup] = useState<Preview | null>(null)
  const [copy, setCopy] = useState<Preview | null>(null)
  const [restorePath, setRestorePath] = useState('')
  const [copyPath, setCopyPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [completePath, setCompletePath] = useState('')
  useEffect(() => { api<Storage>('/system/storage').then(setStorage).catch(err => setError(err.message)) }, [])
  async function work(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (err) { setError(err instanceof Error ? err.message : '存储操作失败') } finally { setBusy(false) } }
  return <div className="two-col wide-left"><section className="card span-all"><h2 className="title-with-icon"><Icon name="data" />数据与备份</h2><p>查看当前存储，下载完整备份，或将已校验的数据恢复到独立空目录。</p>{error && <div role="alert" className="alert error">{error}</div>}{storage && <><p style={{ overflowWrap: 'anywhere' }}>当前数据目录：<strong>{storage.data_dir}</strong></p><div className="metrics">{Object.entries(storage.usage).map(([key, value]) => <div className="metric" key={key}><span>{{ database: '数据库与日志', market: '冻结行情', attachments: '复盘附件' }[key]}</span><strong>{size(value.bytes)}</strong><small>{value.file_count} 个文件</small></div>)}<div className="metric"><span>磁盘剩余</span><strong>{size(storage.free_bytes)}</strong></div></div><a className="button primary" href="/api/v1/backups/export" download>下载完整备份 ZIP</a><details><summary>服务与数据版本</summary><p>已应用 {storage.migration_versions.length} 次数据库迁移</p><p>{storage.migration_versions.at(-1)}</p>{Object.entries(storage.background_errors).map(([key, value]) => <p key={key} className="danger">{key}：{value}</p>)}</details></>}</section>
    <section className="card"><h2 className="title-with-icon"><Icon name="download" />预检与恢复备份</h2><p className="muted">页面支持上传 256 MiB 以内、展开后 512 MiB 以内的备份。原目录会保留，目标目录必须为空。</p><fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}><label className="field"><span>选择备份 ZIP</span><input aria-label="选择备份 ZIP" type="file" accept=".zip" onChange={event => { setFile(event.target.files?.[0] || null); setBackup(null); setCompletePath('') }} /></label><button className="button secondary" disabled={!file} onClick={() => work(async () => { if (file) setBackup(await apiUpload<Preview>('/system/backups/preview', file)) })}>校验备份内容</button>{backup && <><BackupSummary value={backup} /><label className="field"><span>恢复到空目录（完整路径）</span><input aria-label="恢复到空目录" value={restorePath} onChange={event => setRestorePath(event.target.value)} placeholder="D:\TradeRestored" /></label><DirectoryPicker disabled={busy} label="选择恢复目录" onSelect={path => setRestorePath(path)} /><button className="button primary" disabled={!restorePath} onClick={() => work(async () => { if (file) { const result = await apiUpload<Preview>('/system/backups/restore-new', file, { destination: restorePath, expected_fingerprint: backup.fingerprint }); setCompletePath(result.destination || restorePath) } })}>恢复到此空目录</button></>}</fieldset></section>
    <section className="card"><h2 className="title-with-icon"><Icon name="copy" />复制当前数据目录</h2><p className="muted">复制一次完整、一致的数据快照。预览后有新数据写入时，会要求重新预览。</p><fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}><label className="field"><span>复制到空目录（完整路径）</span><input aria-label="复制到空目录" value={copyPath} onChange={event => { setCopyPath(event.target.value); setCopy(null); setCompletePath('') }} placeholder="D:\TradeCopy" /></label><DirectoryPicker disabled={busy} label="选择复制目录" onSelect={path => { setCopyPath(path); setCopy(null); setCompletePath('') }} /><button className="button secondary" disabled={!copyPath} onClick={() => work(async () => setCopy(await api<Preview>('/system/storage/copy-preview', 'POST', { destination: copyPath })))}><Icon name="document" />预览复制范围</button>{copy && <><BackupSummary value={copy} /><button className="button primary" onClick={() => work(async () => { const result = await api<Preview>('/system/storage/copy', 'POST', { destination: copyPath, expected_fingerprint: copy.fingerprint }); setCompletePath(result.destination || copyPath); setCopy(null) })}><Icon name="check" />确认复制到空目录</button></>}</fieldset></section>
    {busy && <p role="status" className="muted">正在校验或复制，请等待完成。</p>}{completePath && <section className="card span-all" role="status"><h2 className="title-with-icon"><Icon name="open" />数据已写入独立目录</h2><p style={{ overflowWrap: 'anywhere' }}>{completePath}</p><p>当前服务仍使用原目录。若要使用这份数据，请停止当前服务，然后在项目目录运行以下命令；重新使用原路径即可回到原数据。</p><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>python scripts/run_trade_rebuild.py --data-dir {"'" + completePath.replaceAll("'", "''") + "'"}</pre></section>}
    <LifecycleControl suggestedDirectory={completePath} />
  </div>
}
