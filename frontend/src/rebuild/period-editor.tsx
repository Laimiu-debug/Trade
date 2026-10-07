import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { printPreviewUrl } from './print-preview'
import { useReviewBuffer } from './review-drafts'
import { Icon } from './workspace-icons'
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10)
function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="field"><span>{label}</span>{children}</label> }

type PeriodRecord = { revision: number; period_key: string; sections: Record<string, string>; derived: { status: string; start_date: string; end_date: string; closed_rounds: number; closed_pnl: string; last_nav: string | null; return_pct: string | null; return_quality: string; confirmed_snapshot_count: number; min_drawdown_pct: string | null; trade_count: number; trades: Array<{ id: string; date: string; symbol: string; side: string; quantity: number }>; rounds: Array<{ id: string; symbol: string; name: string; end_date: string; pnl: string | null; trade_ids: string[] }>; win_rate_pct: string | null; node_events: Array<{ date: string; level: number; kind: string }>; first_achievements: Array<{ first_lit_date: string; level: number }> } }
type PeriodHistory = { period_key: string; revision: number; updated_at: string }[]
type Draft = { base_revision: number; sections: Record<string, string> }
const draftKey = (path: string) => 'trade-rebuild:period-draft:' + path
function parseDraft(raw: string): Draft {
  const value = JSON.parse(raw)
  if (!value || !Number.isInteger(value.base_revision) || value.base_revision < 0 || !value.sections ||
      Array.isArray(value.sections) || Object.values(value.sections).some(item => typeof item !== 'string')) throw new Error('周期草稿格式无效')
  return value
}
const weeklyFields: Array<[string, string]> = [['core_goals', '核心目标'], ['achievements', '本周成果'], ['resource_analysis', '资源分析'], ['market_rhythm', '市场节奏'], ['right_things', '做对的事'], ['wrong_things', '做错的事'], ['market_review', '市场复盘'], ['next_strategy', '交易策略'], ['next_week_strategy', '下周策略'], ['key_insight', '关键认知'], ['tags', '标签']]
const monthlyFields: Array<[string, string]> = [['summary', '月度总结'], ['market_review', '市场复盘'], ['system_iteration', '体系迭代'], ['next_goal', '下月目标'], ['tags', '标签']]

function isoWeek(day: string) {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7))
  const year = date.getUTCFullYear()
  const first = new Date(Date.UTC(year, 0, 1))
  const week = Math.ceil((((date.getTime() - first.getTime()) / 86400000) + 1) / 7)
  return `${year}-W${String(week).padStart(2, '0')}`
}

function weekStart(key: string) {
  const [yearText, weekText] = key.split('-W')
  const jan4 = new Date(Date.UTC(Number(yearText), 0, 4))
  jan4.setUTCDate(jan4.getUTCDate() - (jan4.getUTCDay() || 7) + 1 + (Number(weekText) - 1) * 7)
  return jan4.toISOString().slice(0, 10)
}

export function PeriodEditor({ accountId }: { accountId: string }) {
  const [kind, setKind] = useState<'weekly' | 'monthly'>('weekly')
  const [date, setDate] = useState(today())
  const [record, setRecord] = useState<PeriodRecord | null>(null)
  const [history, setHistory] = useState<PeriodHistory>([])
  const [message, setMessage] = useState('')
  const [loadedPath, setLoadedPath] = useState('')
  const [formal, setFormal] = useState<PeriodRecord | null>(null)
  const [dirty, setDirty] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const [verified, setVerified] = useState(false)
  const sequence = useRef(0)
  const key = kind === 'weekly' ? isoWeek(date) : date.slice(0, 7)
  const path = `/accounts/${accountId}/period-reviews/${kind}/${key}`
  const buffer = useReviewBuffer<Draft>(draftKey(path), parseDraft)
  const bufferRef = useRef(buffer); bufferRef.current = buffer
  useEffect(() => {
    const stamp = ++sequence.current
    const local = bufferRef.current.get().value
    setLoadedPath(path); setVerified(false)
    setRecord(local ? { revision: local.base_revision, period_key: key, sections: local.sections, derived: null } as unknown as PeriodRecord : null)
    setFormal(null); setMessage(local ? '已恢复本机周期草稿，正在核对正式版本…' : ''); setBusy(false); setDirty(Boolean(local)); setConflict(false)
    api<PeriodRecord>(path).then(saved => {
      if (sequence.current !== stamp) return
      const local = bufferRef.current.get().value
      setLoadedPath(path); setFormal(saved); setVerified(true)
      setRecord(local ? { ...saved, sections: local.sections } : saved)
      setDirty(Boolean(local)); setConflict(Boolean(local && local.base_revision !== saved.revision))
      if (local) setMessage('已恢复此账户、周期的本机草稿；导出使用已保存的正文。')
    }).catch(error => { if (sequence.current === stamp) setMessage('正式周期正文读取失败；本机草稿仍可编辑。' + error.message) })
    return () => { ++sequence.current }
  }, [path, reload])
  useEffect(() => {
    let live = true
    setHistory([])
    api<PeriodHistory>(`/accounts/${accountId}/period-reviews/${kind}`).then(rows => { if (live) setHistory(rows) }).catch(error => { if (live) setMessage(error.message) })
    return () => { live = false }
  }, [accountId, kind])
  useEffect(() => {
    if (!dirty) return
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', protect)
    return () => window.removeEventListener('beforeunload', protect)
  }, [dirty])
  function persist(sections: Record<string, string>, revision: number) { buffer.write({ base_revision: revision, sections }) }
  function edit(field: string, value: string) {
    if (!record) return
    const sections = { ...record.sections, [field]: value }
    setRecord({ ...record, sections }); setDirty(true)
    persist(sections, buffer.get().value?.base_revision ?? record.revision)
  }
  function clearDraft() {
    return buffer.clear(buffer.get().raw)
  }
  async function save() {
    if (!record || conflict || buffer.conflict || !verified || busy || loadedPath !== path) return
    const stamp = sequence.current
    const sentDraft = buffer.get().raw
    setBusy(true); setMessage('')
    try {
      const saved = await api<PeriodRecord>(path, 'PUT', { expected_revision: record.revision, sections: record.sections })
      if (sequence.current !== stamp) return
      setFormal(saved)
      if (bufferRef.current.clear(sentDraft)) { setRecord(saved); setDirty(false); setMessage('周期复盘已保存') }
      else { setConflict(true); setMessage('正文已保存，本机草稿发生变化，请核对后继续。') }
      const rows = await api<PeriodHistory>(`/accounts/${accountId}/period-reviews/${kind}`)
      if (sequence.current === stamp) setHistory(rows)
    } catch (error) {
      if (sequence.current !== stamp) return
      setMessage((error as Error).message)
      if ((error as Error & { code?: string }).code?.includes('CONFLICT')) {
        setConflict(true); setFormal(null)
        try { const saved = await api<PeriodRecord>(path); if (sequence.current === stamp) setFormal(saved) } catch { if (sequence.current === stamp) setVerified(false) }
      }
    } finally { if (sequence.current === stamp) setBusy(false) }
  }
  const remove = async () => {
    if (!record?.revision || dirty || conflict || buffer.conflict || !verified || busy || !window.confirm(`删除 ${key} 的复盘正文？统计数据仍从账本计算。`)) return
    const stamp = sequence.current
    setBusy(true)
    try {
      await api(`${path}?expected_revision=${record.revision}`, 'DELETE')
      const empty = await api<PeriodRecord>(path)
      if (sequence.current !== stamp) return
      setRecord(empty); setFormal(empty); setMessage('周期复盘已删除')
      setHistory(rows => rows.filter(row => row.period_key !== key))
    } catch (error) { if (sequence.current === stamp) setMessage((error as Error).message) }
    finally { if (sequence.current === stamp) setBusy(false) }
  }
  const fields = kind === 'weekly' ? weeklyFields : monthlyFields
  return <section className="card reading"><div className="section-heading"><div><h2 className="title-with-icon"><Icon name="period" />{kind === 'weekly' ? '周复盘' : '月复盘'}</h2><p>正文由您编辑，统计随账本重算，并显示所用结果状态。</p></div></div>
    <div className="period-controls"><select aria-label="复盘周期" value={kind} onChange={event => setKind(event.target.value as 'weekly' | 'monthly')}><option value="weekly">周复盘</option><option value="monthly">月复盘</option></select><input aria-label="选择日期" type="date" value={date} onChange={event => { if (event.target.value) setDate(event.target.value) }} /><strong>{key}</strong></div>
    {message && <div className="alert" role="status">{message}</div>}{!verified && <button className="button secondary" onClick={() => setReload(value => value + 1)}><Icon name="refresh" />重新读取周期正文</button>}
    {buffer.warning && <p className="danger" role="alert">{buffer.warning}</p>}
    {buffer.conflict && <div className="alert error"><p>另一页面的周期草稿已变化；当前输入保留，请明确选择。</p><button className="button" onClick={() => {
      const other = buffer.resolve(false)
      if (other === undefined) return
      if (other && record) { setRecord({ ...record, revision: other.base_revision, sections: other.sections }); setDirty(true); setConflict(verified && other.base_revision !== formal?.revision) }
      else if (formal) { setRecord(formal); setDirty(false); setConflict(false) }
    }}>采用另一页面周期草稿</button><button className="button" disabled={!record} onClick={() => { if (record) buffer.write({ base_revision: record.revision, sections: record.sections }, true) }}>保留本页周期草稿</button></div>}
    {history.length > 0 && <div className="period-summary" aria-label="周期复盘历史">{history.map(item => <button key={item.period_key} className="button" type="button" onClick={() => setDate(kind === 'weekly' ? weekStart(item.period_key) : `${item.period_key}-01`)}>{item.period_key} · 第 {item.revision} 版</button>)}</div>}
    {record && loadedPath === path && <>{record.derived && <><div className="period-summary"><span>日期 {record.derived.start_date} 至 {record.derived.end_date}</span><span>交易 {record.derived.trade_count} 笔</span><span>已结束回合 {record.derived.closed_rounds}</span><span>回合盈亏 ¥ {record.derived.closed_pnl}</span><span>回合胜率 {record.derived.win_rate_pct ?? '—'}{record.derived.win_rate_pct ? '%' : ''}</span><span>末次净值 {record.derived.last_nav ? Number(record.derived.last_nav).toFixed(4) : '—'}</span><span>周期收益 {record.derived.return_pct !== null ? `${record.derived.return_pct}%` : '缺少已确认期初/期末快照'}</span><span>最大回撤 {record.derived.min_drawdown_pct === null ? '—' : `${record.derived.min_drawdown_pct}%`}</span><span>节点触达 {record.derived.node_events.filter(item => item.kind === 'lit').length} 次 · 首达 {record.derived.first_achievements.length} 个</span><span>已确认快照 {record.derived.confirmed_snapshot_count} 条</span><span>统计状态 {record.derived.status}</span></div>
      <details className="market-detail"><summary>查看本周期交易、回合与节点事件</summary><h3>交易</h3>{record.derived.trades.length ? <ul>{record.derived.trades.map(item => <li key={item.id}>{item.date} · {item.symbol} · {item.side === 'buy' ? '买入' : '卖出'} {item.quantity} 股 · {item.id}</li>)}</ul> : <p className="muted">本周期无交易</p>}<h3>已结束回合</h3>{record.derived.rounds.length ? <ul>{record.derived.rounds.map(item => <li key={item.id}>{item.end_date} · {item.symbol} {item.name} · 盈亏 ¥ {item.pnl ?? '—'} · {item.id}</li>)}</ul> : <p className="muted">本周期无已结束回合</p>}<h3>目标节点变化</h3>{record.derived.node_events.length ? <ul>{record.derived.node_events.map((item, index) => <li key={`${item.date}-${item.level}-${index}`}>{item.date} · 第 {item.level} 级 · {item.kind === 'lit' ? '点亮' : '熄灭'}</li>)}</ul> : <p className="muted">本周期无节点变化</p>}</details></>}
      {dirty && <p role="status" className="muted">有尚未提交的本机草稿，切换周期或账户后仍可恢复。</p>}
      {conflict && <div className="alert error"><p>正式正文已更新，请先比较版本；本机草稿不会被覆盖。</p><details><summary>查看正式正文（第 {formal?.revision} 版）</summary>{fields.map(([field, label]) => <p key={field} style={{ whiteSpace: 'pre-wrap' }}><strong>{label}：</strong>{formal?.sections[field] || '空'}</p>)}</details><button className="button secondary" disabled={busy || !formal || buffer.conflict} onClick={() => { if (formal) { clearDraft(); setRecord(formal); setDirty(false); setConflict(false) } }}>采用正式周期正文</button><button className="button secondary" disabled={busy || !formal || buffer.conflict} onClick={() => { if (formal && record) { setRecord({ ...formal, sections: record.sections }); persist(record.sections, formal.revision); setConflict(false) } }}>以最新版本继续编辑周期草稿</button></div>}
      <form className="form" onSubmit={event => { event.preventDefault(); void save() }}><fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
        <div className="field-grid">{fields.map(([field, label]) => <Field key={field} label={label}><textarea rows={field === 'tags' ? 2 : 4} value={record.sections[field] ?? ''} onChange={event => edit(field, event.target.value)} /></Field>)}</div>
        <div className="sticky-actions"><span className="muted">{dirty ? '有未保存的修改' : '已与正式记录同步'}</span><button className="button primary" disabled={conflict || buffer.conflict || !verified}><Icon name="save" />保存周期复盘</button>{record.revision > 0 && <a className="button" href={`/api/v1/accounts/${accountId}/exports/review/${kind}/${key}.md`} download>导出已保存的 Markdown</a>}{record.revision > 0 && <a className="button" href={`/api/v1/accounts/${accountId}/exports/review/${kind}/${key}.pdf`} download>导出复盘 PDF</a>}{record.revision > 0 && <a className="button" href={printPreviewUrl(accountId, kind, key)} target="_blank" rel="noopener noreferrer">浏览器打印</a>}{record.revision > 0 && <button className="button" type="button" disabled={dirty || conflict || buffer.conflict || !verified} onClick={remove}>删除本周期正文</button>}</div>
      </fieldset></form></>}
  </section>
}

