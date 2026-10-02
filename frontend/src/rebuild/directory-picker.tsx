import { useEffect, useState } from 'react'
import { api } from './api'

export function DirectoryPicker({ onSelect, disabled = false, label = '选择目录', selectedMessage = '目录已选中，尚未复制、恢复或切换数据。' }: { onSelect: (path: string) => void; disabled?: boolean; label?: string; selectedMessage?: string }) {
  const [supported, setSupported] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => {
    let active = true
    api<{ supported: boolean }>('/system/directory-picker').then(value => { if (active) setSupported(value.supported) }).catch(() => {})
    return () => { active = false }
  }, [])
  if (!supported) return null
  async function choose() {
    setBusy(true); setMessage('请在桌面窗口中选择已有目录；取消会保留当前路径。')
    try {
      const result = await api<{ path: string | null; cancelled: boolean }>('/system/directory-picker', 'POST', {})
      if (result.cancelled) setMessage('已取消，当前路径保持不变。')
      else if (result.path) { onSelect(result.path); setMessage(selectedMessage) }
    } catch (err) { setMessage(err instanceof Error ? err.message : '目录选择失败') }
    finally { setBusy(false) }
  }
  return <div><button className="button secondary" type="button" disabled={disabled || busy} onClick={choose}>{busy ? '等待桌面选择…' : label}</button>{message && <p className="muted" role="status">{message}</p>}</div>
}
