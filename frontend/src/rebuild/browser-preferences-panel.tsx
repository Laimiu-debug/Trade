import { useState } from 'react'
import { applyPreferences, exportPreferences, preferenceNames, previewPreferences, type PreferencePreview } from './browser-preferences'
import { Icon } from './workspace-icons'

export function BrowserPreferencesPanel({ onThemeChange, onDensityChange }: { onThemeChange: (value: 'light' | 'dark' | 'system') => void; onDensityChange: (value: 'comfortable' | 'compact') => void }) {
  const [preview, setPreview] = useState<PreferencePreview | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function work(fn: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await fn() } catch (reason) { setError(reason instanceof Error ? reason.message : '偏好操作失败') }
    finally { setBusy(false) }
  }
  return <section className="card span-all"><h2 className="title-with-icon"><Icon name="settings" />本机页面偏好导出与恢复</h2>
    <p>保存主题、列表密度、已持久化的研究参数、样本选择、显示筛选和最近结果编号。只作用于当前浏览器；研究页面下次打开时采用恢复值。</p>
    <p className="muted">账户、行情、正式预设和观察池随数据目录备份。复盘及 AI 未保存草稿、会话凭据和旧软件本机键不在此文件中。最近结果编号需在当前数据目录存在，恢复偏好不会自动提交研究或联网。</p>
    <div className="toolbar"><button className="button secondary" disabled={busy} onClick={() => void work(async () => {
      const result = await exportPreferences()
      const url = URL.createObjectURL(new Blob([result.content], { type: 'application/json' }))
      const link = document.createElement('a'); link.href = url; link.download = 'trade-browser-preferences-v1.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
      setMessage(`已导出 ${result.count} 组偏好。${result.skipped.length ? `格式不兼容的本机项已保留并跳过：${result.skipped.join('、')}` : ''}`)
    })}><Icon name="download" />导出本机偏好 JSON</button><label className="field"><span>读取偏好文件并预览</span><input type="file" accept=".json,application/json" disabled={busy} onChange={event => {
      const file = event.target.files?.[0]; setPreview(null)
      if (file) void work(async () => { if (file.size > 1024 * 1024) throw new Error('偏好文件最多 1 MiB'); setPreview(await previewPreferences(await file.text())) })
      event.target.value = ''
    }} /></label></div>
    {error && <p className="alert error" role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {preview && <div className="market-detail"><h3>恢复差异 · {preview.changed.length} 组</h3><p>导出时间 {preview.bundle.created_at}；文件未包含的偏好保留。</p>
      <div className="table-wrap"><table><thead><tr><th>项目</th><th>当前值</th><th>恢复值</th></tr></thead><tbody>{preview.changed.map(key => <tr key={key}><td>{preferenceNames[key]}</td><td><details><summary>查看当前</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxWidth: 320 }}>{preview.before[key] ?? '未保存'}</pre></details></td><td><details><summary>查看文件值</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxWidth: 320 }}>{preview.bundle.entries[key]}</pre></details></td></tr>)}</tbody></table></div>
      <button className="button primary" disabled={busy || !preview.changed.length} onClick={() => void work(async () => { const result = applyPreferences(preview); onThemeChange(result.theme); onDensityChange(result.density); setPreview(null); setMessage('已恢复本机偏好。主题与密度已更新，研究页面下次打开时采用恢复参数。') })}><Icon name="check" />确认恢复这些偏好</button><button className="button secondary" onClick={() => setPreview(null)}><Icon name="close" />取消预览</button></div>}
  </section>
}
