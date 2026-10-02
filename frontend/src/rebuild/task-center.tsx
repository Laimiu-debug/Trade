import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

type Page = 'backtest' | 'market' | 'research' | 'ai' | 'overview' | 'events'
type Task = { id: string; kind: string; title: string; page: Page; state: string; progress: { done: number; total: number } | null; created_at: string; updated_at: string; error_code: string | null; note: string | null; actions: { cancel?: string; retry?: string; pause?: string; resume?: string }; pause_supported: boolean }
type Summary = { items: Task[]; total_by_kind: Record<string, number>; limit_per_kind: number; active_count: number }
const kinds: Record<string, string> = { backtest: '历史回测', scan: '策略扫描', plateau: '收益平原', walk_forward: '滚动样本外验证', portfolio: '多证券组合回测', portfolio_experiment: '组合参数平原', portfolio_walk_forward: '组合滚动样本外验证', portfolio_analysis: '组合高级分析', event_store: '事件仓回填', market: '行情同步', universe: '全市场筛选', ai: 'AI 调用', analytics: '账户重算' }
const states: Record<string, string> = { queued: '等待执行', pausing: '等待暂停', paused: '已暂停', running: '执行中', finalizing: '保存结果中', cancelling: '正在取消', succeeded: '已完成', completed: '已完成', done: '已完成', superseded: '已由新版本替代', failed: '失败', partial_failed: '部分失败', cancelled: '已取消', interrupted: '已中断' }
const taskErrors: Record<string, string> = { TDX_SOURCE_SELECTION_REQUIRED: '等待选择原通达信目录：请在系统设置 → 行情来源中选择本任务原来使用的目录，随后自动继续。' }
const active = (row: Task) => ['queued', 'running', 'finalizing', 'cancelling', 'pausing'].includes(row.state)

export function TaskCenter({ accountId, onOpen }: { accountId?: string; onOpen: (page: Page, id: string, kind: string) => void }) {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [kind, setKind] = useState('')
  const [state, setState] = useState('all')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [expanded, setExpanded] = useState('')
  const refresh = useCallback(async () => {
    const result = await api<Summary>('/tasks' + (accountId ? '?account_id=' + encodeURIComponent(accountId) : ''))
    setSummary(result)
  }, [accountId])
  useEffect(() => {
    let live = true, pending = false
    setSummary(null)
    const poll = async () => {
      if (pending || document.visibilityState === 'hidden') return
      pending = true
      try {
        const result = await api<Summary>('/tasks' + (accountId ? '?account_id=' + encodeURIComponent(accountId) : ''))
        if (live) { setSummary(result); setError('') }
      } catch (err) { if (live) setError(err instanceof Error ? err.message : '任务读取失败') }
      finally { pending = false }
    }
    void poll()
    const timer = window.setInterval(poll, 2500)
    return () => { live = false; window.clearInterval(timer) }
  }, [accountId])

  async function action(task: Task, name: 'cancel' | 'retry' | 'pause' | 'resume') {
    const path = task.actions[name]
    if (!path) return
    setBusy(task.id); setError(''); setNotice('')
    try { await api(path, 'POST', {}); await refresh(); setNotice({ cancel: '取消请求已提交，任务结束后状态会更新', retry: '重试请求已提交', pause: '暂停请求已提交，当前计算批次完成后停止', resume: '继续请求已提交，已完成结果会保留' }[name]) }
    catch (err) { setError(err instanceof Error ? err.message : '任务操作失败') }
    finally { setBusy('') }
  }

  const rows = (summary?.items || []).filter(row => (!kind || row.kind === kind) && (state === 'all' || (state === 'active' ? active(row) : ['failed', 'partial_failed', 'interrupted'].includes(row.state))))
  return <section className="card"><div className="section-heading"><div><h2>任务中心</h2><p>行情、筛选和回测为共享研究任务；AI 与重算记录按当前账户显示。</p></div><button className="button secondary" onClick={() => refresh().catch(err => setError(err.message))}>刷新任务</button></div>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    <div className="form-grid"><label className="field"><span>任务类别</span><select value={kind} onChange={event => setKind(event.target.value)}><option value="">全部类别</option>{Object.entries(kinds).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label><label className="field"><span>任务状态</span><select value={state} onChange={event => setState(event.target.value)}><option value="all">全部状态</option><option value="active">正在处理</option><option value="failed">需要检查</option></select></label></div>
    <p className="muted">当前显示 {rows.length} 项 · 正在处理 {summary?.active_count ?? '—'} 项。每类保留最近 {summary?.limit_per_kind ?? 100} 项预览，运行中的任务优先显示。</p>
    <div className="table-wrap"><table><thead><tr><th>任务</th><th>状态 / 进度</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.kind + row.id}><td>{kinds[row.kind]}<br /><small>{row.title}</small><br /><small className="muted">{row.id.slice(0, 12)}</small></td><td>{states[row.state] || row.state}{row.progress && <><br /><progress max={Math.max(1, row.progress.total)} value={row.progress.done} aria-label="任务进度" /> {row.progress.done} / {row.progress.total}</>}{row.note && <p className="muted">{row.note}</p>}{row.error_code && <p className="danger">{taskErrors[row.error_code] || row.error_code}</p>}</td><td>{row.updated_at.slice(0, 19).replace('T', ' ')}</td><td><div className="form-actions"><button className="link-button" onClick={() => onOpen(row.page, row.id, row.kind)}>打开工作区</button>{row.actions.cancel && <button className="link-button danger" disabled={Boolean(busy)} onClick={() => action(row, 'cancel')}>取消</button>}{row.actions.pause && <button className="link-button" disabled={Boolean(busy)} onClick={() => action(row, 'pause')}>暂停</button>}{row.actions.resume && <button className="link-button" disabled={Boolean(busy)} onClick={() => action(row, 'resume')}>继续</button>}{row.actions.retry && <button className="link-button" disabled={Boolean(busy)} onClick={() => action(row, 'retry')}>重试</button>}<button className="link-button" onClick={() => setExpanded(expanded === row.id ? '' : row.id)}>记录信息</button></div>{expanded === row.id && <p className="muted" style={{ overflowWrap: 'anywhere' }}>{row.id}<br />创建：{row.created_at}<br />{row.pause_supported ? '可暂停' : '此类任务不支持暂停'}</p>}</td></tr>)}</tbody></table></div>
    {summary && !rows.length && <p className="muted">暂无符合条件的任务。</p>}
  </section>
}
