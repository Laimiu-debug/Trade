import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { DirectoryPicker } from './directory-picker'
import { Icon } from './workspace-icons'

type Operation = { id: string; kind: 'switch' | 'exit'; state: string; source_data_dir: string; destination?: string; recovery_path?: string; error?: string | null }
type Transition = { id: string; state: string; active_data_dir?: string; error?: string | null }
type Lifecycle = { supported: boolean; instance_id: string; data_dir: string; state: string; operation: Operation | null; last_transition: Transition | null; capabilities: { exit: boolean; switch: boolean }; active_writes: number; active_background: Record<string, number> }
type Preview = { verification_id: string; fingerprint: string; destination: string; source_data_dir: string; total_bytes: number; dataset_count: number; attachment_count: number; expires_at: string }
type Pending = { id: string; kind: 'switch' | 'exit'; instance: string; started: number; reloaded_for?: string }
const key = 'trade-rebuild:lifecycle-pending'
const labels: Record<string, string> = { running: '服务运行中', draining: '等待当前写入和后台计算结束', snapshotting: '保存原目录恢复点', restarting: '正在启动目标目录', exiting: '正在关闭服务', completed: '切换完成', rolled_back: '切换失败，已回到原目录', failed: '操作失败' }
function readPending(): Pending | null {
  try { const value = JSON.parse(sessionStorage.getItem(key) || 'null'); return value && typeof value.id === 'string' && ['switch', 'exit'].includes(value.kind) && typeof value.instance === 'string' && typeof value.started === 'number' ? value : null } catch { return null }
}

export function LifecycleControl({ suggestedDirectory = '' }: { suggestedDirectory?: string }) {
  const [status, setStatus] = useState<Lifecycle | null>(null)
  const [destination, setDestination] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [exitReview, setExitReview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<Pending | null>(readPending)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [check, setCheck] = useState(0)
  const savedOperation = useRef<Operation | null>(null)
  function remember(value: Pending | null) { try { if (value) sessionStorage.setItem(key, JSON.stringify(value)); else sessionStorage.removeItem(key) } catch { /* Live status remains available if browser storage is disabled. */ } setPending(value) }
  useEffect(() => {
    let live = true, loading = false
    function reloadFor(instance: string) {
      if (!pending || pending.reloaded_for === instance) return false
      try { sessionStorage.setItem(key, JSON.stringify({ ...pending, reloaded_for: instance })) } catch { /* Reload reconnects even without persistence. */ }
      window.location.reload()
      return true
    }
    const poll = async () => {
      if (loading || !live) return
      loading = true
      try {
        const next = await api<Lifecycle>('/system/lifecycle')
        if (!live) return
        setStatus(next)
        if (next.operation) savedOperation.current = next.operation
        if (pending) {
          if (next.instance_id !== pending.instance && reloadFor(next.instance_id)) return
          const transition = next.last_transition
          if (transition?.id === pending.id && ['completed', 'rolled_back', 'failed'].includes(transition.state)) {
            setNotice(`${labels[transition.state]}${transition.active_data_dir ? '：' + transition.active_data_dir : ''}`)
            setError(transition.error || ''); remember(null)
          } else if (next.operation?.id === pending.id && next.operation.state === 'failed') {
            setError(next.operation.error || '操作失败，当前服务继续使用原目录'); remember(null)
          } else if (pending.kind === 'exit' && next.instance_id !== pending.instance) {
            setNotice('服务已重新启动。'); remember(null)
          } else if (Date.now() - pending.started > 180_000) {
            setError('尚未确认操作结果。请查看启动器输出或重新加载页面，确认实际使用的数据目录。')
          }
        }
      } catch (err) {
        if (!live) return
        if (!pending) { setError(err instanceof Error ? err.message : '无法读取服务状态'); return }
        setNotice(pending.kind === 'switch' ? '正在等待本地服务重新连接…' : '服务连接已断开；重新启动后可再次打开本页面。')
        try {
          const response = await fetch('/health', { cache: 'no-store', signal: AbortSignal.timeout(2500) })
          const health = response.ok ? await response.json() : null
          if (live && health?.instance_id && health.instance_id !== pending.instance) reloadFor(health.instance_id)
        } catch { /* A stopped or restarting local service cannot respond yet. */ }
      } finally { loading = false }
    }
    void poll()
    const timer = window.setInterval(() => { if (pending && Date.now() - pending.started <= 180_000) void poll() }, 1500)
    return () => { live = false; window.clearInterval(timer) }
  }, [pending, check])

  async function work(action: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await action() } catch (err) { setError(err instanceof Error ? err.message : '服务操作失败') } finally { setBusy(false) } }
  async function submit(kind: 'switch' | 'exit') {
    if (!status) return
    const operation = await api<Operation>('/system/lifecycle/' + kind, 'POST', kind === 'switch' ? { verification_id: preview?.verification_id, expected_fingerprint: preview?.fingerprint } : {})
    savedOperation.current = operation
    remember({ id: operation.id, kind, instance: status.instance_id, started: Date.now() })
    setPreview(null); setConfirmed(false); setExitReview(false)
  }
  const operation = status?.operation || savedOperation.current
  const blocked = busy || Boolean(pending)
  return <section className="card span-all" aria-label="服务与目录切换"><h2 className="title-with-icon"><Icon name="open" />服务与目录切换</h2>
    {error && <div role="alert" className="alert error">{error}</div>}{notice && <p role="status" style={{ overflowWrap: 'anywhere' }}>{notice}</p>}
    {status && <><p>{labels[status.state] || status.state} · 当前写入 {status.active_writes} 项 · 后台工作 {Object.values(status.active_background).reduce((sum, value) => sum + value, 0)} 项</p><p style={{ overflowWrap: 'anywhere' }}>当前目录：{status.data_dir}</p>
      {!status.supported ? <p className="muted">当前为独立服务进程。使用项目启动器 <code>python scripts/run_trade_rebuild.py</code> 启动后，可在这里切换目录和退出应用。</p> : <>
        <fieldset disabled={blocked} style={{ border: 0, padding: 0, minWidth: 0 }}><label className="field"><span>切换到已有数据目录（完整路径）</span><input aria-label="切换到已有数据目录" value={destination} onChange={event => { setDestination(event.target.value); setPreview(null); setConfirmed(false) }} /></label>
          {suggestedDirectory && <button className="button secondary" onClick={() => { setDestination(suggestedDirectory); setPreview(null); setConfirmed(false) }}>使用刚恢复或复制的目录</button>}
          <DirectoryPicker disabled={blocked} label="选择切换目录" onSelect={path => { setDestination(path); setPreview(null); setConfirmed(false) }} />
          <button className="button secondary" disabled={!destination.trim() || !status.capabilities.switch} onClick={() => work(async () => { setPreview(await api<Preview>('/system/lifecycle/switch-preview', 'POST', { destination })); setConfirmed(false) })}>校验切换目标</button>
          {preview && <div className="market-detail"><h3>确认切换范围</h3><p style={{ overflowWrap: 'anywhere' }}>原目录：{preview.source_data_dir}<br />目标目录：{preview.destination}</p><p>目标包含 {preview.dataset_count} 份行情、{preview.attachment_count} 个附件，共 {(preview.total_bytes / 1024 / 1024).toFixed(2)} MiB。预检有效至 {preview.expires_at}。</p><p>将停止接收新写入，等待当前工作结束并保存原目录恢复点，再启动目标目录。目标启动失败时会尝试恢复原目录。请先保存其他页面中尚未提交的编辑。</p><label className="check-field"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我已保存编辑，并确认使用上述目标目录</label><button className="button primary" disabled={!confirmed} onClick={() => work(() => submit('switch'))}><Icon name="check" />确认切换并重启</button></div>}
          <hr /><button className="button secondary" disabled={!status.capabilities.exit} onClick={() => setExitReview(true)}>准备退出应用</button>
          {exitReview && <div className="market-detail"><p>请先保存尚未提交的编辑。退出会等待当前写入与后台工作结束。重新打开启动器即可继续使用。</p><button className="button danger" onClick={() => work(() => submit('exit'))}><Icon name="check" />确认退出应用</button><button className="button secondary" onClick={() => setExitReview(false)}>返回</button></div>}
        </fieldset>
      </>}
    </>}
    {pending && <div className="market-detail" role="status"><p>{labels[operation?.state || 'draining'] || operation?.state} · 操作 {pending.id.slice(0, 12)}</p>{operation?.recovery_path && <p style={{ overflowWrap: 'anywhere' }}>恢复点：{operation.recovery_path}</p>}<p>页面会检查重启结果；连接恢复后会重新加载当前数据。</p></div>}
    <div className="form-actions"><button className="link-button" disabled={busy} onClick={() => { setError(''); setCheck(value => value + 1) }}>检查服务状态</button>{pending && <button className="link-button" onClick={() => window.location.reload()}>重新加载页面</button>}</div>
  </section>
}
