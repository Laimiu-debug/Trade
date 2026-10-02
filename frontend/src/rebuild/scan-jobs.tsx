import { useEffect, useRef, useState } from 'react'
import { api } from './api'

export type ScanJob = { id: string; state: string; completed_count: number; total_count: number; error: string | null; scan_id: string | null; created_at: string; attempt_number: number; capabilities: { cancel: boolean; retry: boolean } }
const stateName: Record<string, string> = { queued: '等待执行', running: '正在扫描', cancelling: '正在取消', cancelled: '已取消', failed: '失败', succeeded: '已完成' }

export function ScanJobs({ focusId, onOpen }: { focusId: string; onOpen: (scanId: string) => void }) {
  const [jobs, setJobs] = useState<ScanJob[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [refresh, setRefresh] = useState(0)
  const onOpenRef = useRef(onOpen)
  const opened = useRef('')
  onOpenRef.current = onOpen
  useEffect(() => {
    let live = true, pending = false
    const poll = async () => {
      if (pending || document.visibilityState === 'hidden') return
      pending = true
      try {
        const rows = await api<ScanJob[]>('/research/scan-jobs')
        if (!live) return
        setJobs(rows); setError('')
        const finished = rows.find(row => row.id === focusId && row.state === 'succeeded' && row.scan_id)
        if (finished?.scan_id && opened.current !== focusId) { opened.current = focusId; onOpenRef.current(finished.scan_id) }
      } catch (err) { if (live) setError(err instanceof Error ? err.message : '扫描任务读取失败') }
      finally { pending = false }
    }
    void poll()
    const timer = window.setInterval(poll, 1500)
    return () => { live = false; window.clearInterval(timer) }
  }, [focusId, refresh])

  async function action(job: ScanJob, name: 'cancel' | 'retry') {
    setBusy(job.id); setError('')
    try { await api(`/research/scan-jobs/${job.id}/${name}`, 'POST', {}); setRefresh(value => value + 1) }
    catch (err) { setError(err instanceof Error ? err.message : '任务操作失败') }
    finally { setBusy('') }
  }
  return <details open={Boolean(focusId)}><summary>后台扫描任务 · {jobs.length} 次</summary><p className="muted">可离开页面或刷新。每批判断保存检查点，全部完成后才发布扫描结果；与回测和平原轮流使用计算资源。</p>{error && <p className="danger" role="alert">{error}</p>}<div className="table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>已完成判断</th><th>操作</th></tr></thead><tbody>{jobs.map(job => <tr key={job.id}><td>{job.created_at.slice(0, 19)}<br /><small>{job.id.slice(0, 12)}</small></td><td>{stateName[job.state] || job.state}{job.error && <p className="danger">{job.error}</p>}</td><td><progress max={Math.max(1, job.total_count)} value={job.completed_count} aria-label="扫描任务进度" /> {job.completed_count} / {job.total_count}</td><td>{job.scan_id && <button type="button" className="link-button" onClick={() => onOpen(job.scan_id!)}>查看扫描结果</button>}{job.capabilities.cancel && <button type="button" className="link-button danger" disabled={Boolean(busy)} onClick={() => action(job, 'cancel')}>取消扫描</button>}{job.capabilities.retry && <button type="button" className="link-button" disabled={Boolean(busy)} onClick={() => action(job, 'retry')}>继续未完成扫描</button>}</td></tr>)}</tbody></table></div>{!jobs.length && <p className="muted">暂无后台扫描任务。</p>}</details>
}
