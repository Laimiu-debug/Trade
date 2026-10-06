import { useCallback, useEffect, useState } from 'react'
import { api, type SimFill } from './api'
import { Icon } from './workspace-icons'

type Tag = { id: string; type: 'emotion' | 'reason'; name: string; active: boolean; revision: number }
type Assignment = { fill_id: string; emotion_tag_id: string | null; reason_tag_ids: string[]; revision: number }
type Stat = Tag & { fill_count: number; sell_count: number; win_count: number; win_rate_pct: string | null; gross: string; realized_pnl: string; realized_return_pct: string | null }
type Stats = { data: Stat[]; method: string }

export function SimReviewTags({ accountId, fills, frozen }: { accountId: string; fills: SimFill[]; frozen: boolean }) {
  const root = `/sim-accounts/${accountId}`
  const [tags, setTags] = useState<Tag[]>([])
  const [assignments, setAssignments] = useState<Assignment[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [name, setName] = useState('')
  const [type, setType] = useState<'emotion' | 'reason'>('reason')
  const [selectedFill, setSelectedFill] = useState('')
  const [emotion, setEmotion] = useState('')
  const [reasons, setReasons] = useState<string[]>([])
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const refresh = useCallback(async () => {
    const query = new URLSearchParams()
    if (dateFrom) query.set('date_from', dateFrom)
    if (dateTo) query.set('date_to', dateTo)
    const [nextTags, nextAssignments, nextStats] = await Promise.all([
      api<Tag[]>(root + '/review-tags'), api<Assignment[]>(root + '/fill-tags'),
      api<Stats>(root + '/tag-stats' + (query.size ? '?' + query.toString() : '')),
    ])
    setTags(nextTags); setAssignments(nextAssignments); setStats(nextStats)
  }, [root, dateFrom, dateTo])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])

  function chooseFill(fillId: string) {
    const current = assignments.find(row => row.fill_id === fillId)
    setSelectedFill(fillId)
    setEmotion(current?.emotion_tag_id || '')
    setReasons(current?.reason_tag_ids || [])
  }
  async function mutate(label: string, operation: () => Promise<unknown>) {
    setError(''); setMessage('')
    try { await operation(); await refresh(); setMessage(label) }
    catch (err) { setError(err instanceof Error ? err.message : '操作失败') }
  }
  const selected = fills.find(row => row.id === selectedFill)
  const currentAssignment = assignments.find(row => row.fill_id === selectedFill)

  return <section className="card">
    <h2 className="title-with-icon"><Icon name="flag" />模拟成交复盘标签</h2>
    <p className="muted">每笔成交可选一个情绪标签和最多 16 个原因标签。删除标签后保留历史关联与统计，成交记录不会删除。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    {message && <div className="alert success" role="status">{message}</div>}
    {!frozen && <form className="form" onSubmit={event => { event.preventDefault(); mutate('标签已创建', async () => { await api(root + '/review-tags', 'POST', { type, name }); setName('') }) }}>
      <div className="form-grid"><label className="field"><span>类别</span><select value={type} onChange={event => setType(event.target.value as 'emotion' | 'reason')}><option value="emotion">情绪</option><option value="reason">原因</option></select></label><label className="field"><span>新标签名称</span><input value={name} maxLength={80} onChange={event => setName(event.target.value)} required /></label></div>
      <button className="button secondary"><Icon name="add" />添加标签</button>
    </form>}
    <div className="form-actions">{tags.filter(row => row.active).map(row => <span key={row.id} className="tag-chip">{row.type === 'emotion' ? '情绪' : '原因'} · {row.name}{!frozen && <button className="link-button danger" type="button" aria-label={`删除标签 ${row.name}`} onClick={() => { if (window.confirm(`删除标签“${row.name}”？历史成交关联仍会保留。`)) mutate('标签已停用', () => api(root + `/review-tags/${row.id}?expected_revision=${row.revision}`, 'DELETE')) }}>×</button>}</span>)}</div>
    <label className="field"><span>选择成交记录</span><select value={selectedFill} onChange={event => chooseFill(event.target.value)}><option value="">选择成交</option>{fills.map(row => <option key={row.id} value={row.id}>{row.fill_date} · {row.fill_price} · ¥ {row.gross} · {row.id.slice(0, 8)}</option>)}</select></label>
    {selected && <div className="market-detail">
      <p>{selected.fill_date} · {selected.fill_price} · ¥ {selected.gross} · 已实现盈亏 {selected.realized_pnl ?? '未平仓'}</p>
      <label className="field"><span>情绪标签</span><select disabled={frozen} value={emotion} onChange={event => setEmotion(event.target.value)}><option value="">不设置</option>{tags.filter(row => row.type === 'emotion' && (row.active || row.id === emotion)).map(row => <option key={row.id} value={row.id}>{row.name}{row.active ? '' : '（已删除）'}</option>)}</select></label>
      <div className="form-grid">{tags.filter(row => row.type === 'reason' && (row.active || reasons.includes(row.id))).map(row => <label className="check-field" key={row.id}><input type="checkbox" disabled={frozen || (!row.active && !reasons.includes(row.id))} checked={reasons.includes(row.id)} onChange={event => setReasons(current => event.target.checked ? [...current, row.id] : current.filter(id => id !== row.id))} />{row.name}{row.active ? '' : '（已删除）'}</label>)}</div>
      {!frozen && <button className="button primary" disabled={reasons.length > 16} onClick={() => mutate('成交标签已保存', () => api(root + `/fills/${selected.id}/tags`, 'PUT', { expected_revision: currentAssignment?.revision || 0, emotion_tag_id: emotion || null, reason_tag_ids: reasons }))}><Icon name="save" />保存成交标签</button>}
    </div>}
    <h3>标签统计</h3>
    <p className="muted">{stats?.method}</p>
    <div className="form-grid"><label className="field"><span>开始日期</span><input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label><label className="field"><span>结束日期</span><input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label></div>
    <div className="table-wrap"><table><thead><tr><th>类型 / 标签</th><th>成交笔数</th><th>卖出笔数</th><th>胜率</th><th>成交额</th><th>已实现盈亏</th><th>已实现收益率</th></tr></thead><tbody>{stats?.data.filter(row => row.fill_count > 0 || row.active).map(row => <tr key={row.id}><td>{row.type === 'emotion' ? '情绪' : '原因'} · {row.name}{row.active ? '' : '（已删除）'}</td><td>{row.fill_count}</td><td>{row.sell_count}</td><td>{row.win_rate_pct === null ? '—' : `${row.win_rate_pct}%`}</td><td>¥ {row.gross}</td><td>¥ {row.realized_pnl}</td><td>{row.realized_return_pct === null ? '—' : `${row.realized_return_pct}%`}</td></tr>)}</tbody></table></div>
  </section>
}
