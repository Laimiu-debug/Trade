import { useEffect, useRef, useState } from 'react'
import { api, type Trade } from './api'
import { useReviewBuffer } from './review-drafts'
import { Icon } from './workspace-icons'

type Entry = { ai_comment?: string; ai_generation_id?: string; ai_run_id?: string; ai: number | null; final: number | null; comment: string; final_source: string | null }
type Sheet = { id: string; scope: 'daily' | 'trade' | 't_group'; subject_id: string; trade_ids: string[]; association_changed: boolean; scores: Record<string, Entry>; comment: string; revision: number }
type Editable = { final: number | null; comment: string }
type ScoreDraft = { base_revision: number; scores: Record<string, Editable>; comment: string }
function parseDraft(raw: string): ScoreDraft {
  const value = JSON.parse(raw) as ScoreDraft
  if (!value || !Number.isInteger(value.base_revision) || value.base_revision < 0 || typeof value.comment !== 'string' ||
      !value.scores || Array.isArray(value.scores) || Object.values(value.scores).some(row => !row || typeof row.comment !== 'string' || row.final !== null && (!Number.isInteger(row.final) || row.final < 0 || row.final > 10))) throw new Error('评分草稿格式无效')
  return value
}
const dimensions = {
  daily: [['position', '仓位控制'], ['drawdown', '回撤控制'], ['discipline', '计划执行'], ['entry', '买点质量'], ['exit', '卖点质量'], ['emotion', '情绪管理']],
  trade: [['timing', '时机质量'], ['discipline', '计划执行'], ['emotion', '情绪管理']],
  t_group: [['timing', '做 T 时机'], ['discipline', '计划执行'], ['emotion', '情绪管理']],
} as const

export function ReviewScores({ accountId, day }: { accountId: string; day: string }) {
  const root = `/accounts/${accountId}/daily-reviews/${day}`
  const [trades, setTrades] = useState<Trade[]>([])
  const [sheets, setSheets] = useState<Sheet[]>([])
  const [scope, setScope] = useState<'daily' | 'trade' | 't_group'>('daily')
  const [tradeId, setTradeId] = useState('')
  const [groupIds, setGroupIds] = useState<string[]>([])
  const [scores, setScores] = useState<Record<string, Editable>>({})
  const [comment, setComment] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const scopeStamp = useRef(0)
  useEffect(() => {
    const stamp = ++scopeStamp.current
    setLoaded(false); setSheets([]); setTrades([]); setError(''); setMessage(''); setBusy(false)
    Promise.all([api<Trade[]>(`/accounts/${accountId}/trades`), api<Sheet[]>(root + '/scores')])
      .then(([allTrades, allSheets]) => { if (scopeStamp.current === stamp) { setTrades(allTrades.filter(row => row.trade_date === day)); setSheets(allSheets); setError(''); setLoaded(true) } })
      .catch(err => { if (scopeStamp.current === stamp) setError(err.message) })
    return () => { ++scopeStamp.current }
  }, [accountId, day, root, reload])
  useEffect(() => { setScope('daily'); setTradeId(''); setGroupIds([]) }, [root])
  const ids = scope === 'daily' ? [] : scope === 'trade' ? tradeId ? [tradeId] : [] : [...groupIds].sort()
  const sheet = sheets.find(row => row.scope === scope &&
    JSON.stringify([...row.trade_ids].sort()) === JSON.stringify(ids))
  const draftKey = `trade-rebuild:score-draft:${root}:${scope}:${ids.join('|')}`
  const buffer = useReviewBuffer<ScoreDraft>(draftKey, parseDraft)
  const current = useRef(buffer); current.current = buffer
  const selection = useRef(draftKey); selection.current = draftKey
  const selectionKey = `${draftKey}:${sheet?.id || ''}:${sheet?.revision ?? 0}`
  const versionConflict = Boolean(loaded && buffer.value && buffer.value.base_revision !== (sheet?.revision ?? 0))
  function valuesFromSheet() {
    return Object.fromEntries(dimensions[scope].map(([key]) => [key, { final: sheet?.scores[key]?.final ?? null, comment: sheet?.scores[key]?.comment ?? '' }]))
  }
  useEffect(() => {
    const local = current.current.get().value
    setScores(local?.scores ?? valuesFromSheet()); setComment(local?.comment ?? sheet?.comment ?? '')
  }, [selectionKey, loaded])
  function edit(nextScores: Record<string, Editable>, nextComment = comment) {
    setScores(nextScores); setComment(nextComment)
    buffer.write({ base_revision: buffer.value?.base_revision ?? sheet?.revision ?? 0, scores: nextScores, comment: nextComment })
    setMessage('评分草稿已保留，尚未提交')
  }
  const groupTrades = trades.filter(row => groupIds.includes(row.id))
  const groupValid = groupTrades.length >= 2 && new Set(groupTrades.map(row => row.symbol)).size === 1 &&
    new Set(groupTrades.map(row => row.side)).size === 2
  const canSave = scope === 'daily' || scope === 'trade' && Boolean(tradeId) || scope === 't_group' && groupValid

  const dirty = comment !== (sheet?.comment || '') || dimensions[scope].some(([key]) => (scores[key]?.final ?? null) !== (sheet?.scores[key]?.final ?? null) || (scores[key]?.comment || '') !== (sheet?.scores[key]?.comment || ''))

  useEffect(() => {
    if (!dirty) return
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  async function copySuggestion(dimension: string) {
    if (!sheet || dirty || busy || buffer.conflict || versionConflict || !loaded) return
    const stamp = scopeStamp.current, selected = draftKey
    setBusy(true); setError(''); setMessage('')
    try {
      const saved = await api<Sheet>(`/accounts/${accountId}/review-scores/${sheet.id}/copy-ai-to-final`, 'POST', { expected_revision: sheet.revision, dimensions: [dimension] })
      if (scopeStamp.current !== stamp || selection.current !== selected) return
      buffer.clear(buffer.get().raw)
      setSheets(current => [...current.filter(row => row.id !== saved.id), saved]); setMessage('已将所选维度的建议保存为最终分，来源已记录')
    } catch (err) { if (scopeStamp.current === stamp && selection.current === selected) setError(err instanceof Error ? err.message : '采用建议失败') }
    finally { if (scopeStamp.current === stamp && selection.current === selected) setBusy(false) }
  }

  async function save() {
    if (busy || !loaded || !canSave || buffer.conflict || versionConflict) return
    const stamp = scopeStamp.current, selected = draftKey, raw = buffer.get().raw
    setBusy(true); setError(''); setMessage('')
    try {
      const saved = await api<Sheet>(root + '/scores', 'PUT', {
        scope, trade_ids: ids, scores, comment, expected_revision: sheet?.revision ?? 0,
      })
      if (scopeStamp.current !== stamp || selection.current !== selected) return
      current.current.clear(raw)
      setSheets(current => [...current.filter(row => row.id !== saved.id), saved])
      setMessage('人工最终评分已保存')
    } catch (err) {
      if (scopeStamp.current !== stamp || selection.current !== selected) return
      setError(err instanceof Error ? err.message : '评分保存失败，草稿已保留')
      if ((err as Error & { code?: string }).code?.includes('CONFLICT')) setReload(value => value + 1)
    } finally { if (scopeStamp.current === stamp && selection.current === selected) setBusy(false) }
  }

  return <section className="card"><h2 className="title-with-icon"><Icon name="flag" />人工复盘评分</h2><p className="muted">整日六维、逐笔三维、同股做 T 分组分别保存。最终分为 0–10 分。AI 建议与人工最终分分别保存，可在 AI 工作台生成整日 / 逐笔 / 批量 / 做 T 评分。</p>
    {error && <div className="alert error" role="alert">{error}</div>}{message && <div className="alert success" role="status">{message}</div>}
    {buffer.warning && <p className="danger" role="alert">{buffer.warning}</p>}
    {buffer.conflict && <div className="alert error"><p>另一页面的评分草稿已变化，请明确选择。</p><button className="button" onClick={() => { const other = buffer.resolve(false); if (other !== undefined) { setScores(other?.scores ?? valuesFromSheet()); setComment(other?.comment ?? sheet?.comment ?? '') } }}>采用另一页面评分草稿</button><button className="button" onClick={() => buffer.write({ base_revision: buffer.value?.base_revision ?? sheet?.revision ?? 0, scores, comment }, true)}>保留本页评分草稿</button></div>}
    {versionConflict && <div className="alert error"><p>正式人工评分已更新，当前输入保留。</p><details><summary>查看正式评分（第 {sheet?.revision ?? 0} 版）</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ scores: sheet?.scores ?? {}, comment: sheet?.comment ?? '' }, null, 2)}</pre></details><button className="button" disabled={buffer.conflict} onClick={() => { buffer.clear(buffer.get().raw); setScores(valuesFromSheet()); setComment(sheet?.comment ?? '') }}>采用正式人工评分</button><button className="button" disabled={buffer.conflict} onClick={() => buffer.write({ base_revision: sheet?.revision ?? 0, scores, comment })}>以最新版本继续编辑评分草稿</button></div>}
    {!loaded && <button className="button" onClick={() => setReload(value => value + 1)}>重新读取正式评分</button>}
    <fieldset disabled={busy || !loaded && !buffer.value} style={{ border: 0, padding: 0, minWidth: 0 }}><label className="field">评分范围<select value={scope} onChange={event => setScope(event.target.value as 'daily' | 'trade' | 't_group')}><option value="daily">整日六维</option><option value="trade">单笔成交</option><option value="t_group">同股做 T 分组</option></select></label>
    {scope === 'trade' && <label className="field">选择成交<select value={tradeId} onChange={event => setTradeId(event.target.value)}><option value="">请选择</option>{trades.map(row => <option key={row.id} value={row.id}>{row.side === 'buy' ? '买' : '卖'} · {row.symbol} · {row.quantity} 股 · {row.id.slice(0, 8)}</option>)}</select></label>}
    {scope === 't_group' && <><p className="muted">选择同一天、同一代码的一买一卖或更多成交。</p><div className="form-grid">{trades.map(row => <label className="check-field" key={row.id}><input type="checkbox" checked={groupIds.includes(row.id)} onChange={event => setGroupIds(current => event.target.checked ? [...current, row.id] : current.filter(id => id !== row.id))} />{row.side === 'buy' ? '买' : '卖'} · {row.symbol} · {row.quantity} 股</label>)}</div>{groupIds.length > 0 && !groupValid && <p className="danger">分组需包含同股买入和卖出。</p>}</>}
    {sheet?.association_changed && <p className="danger">评分关联的成交已变化，请核对历史评分。</p>}
    <div className="form-grid">{dimensions[scope].map(([key, label]) => <div className="market-detail" key={key}><label className="field">{scope === 'trade' && key === 'timing' ? trades.find(row => row.id === tradeId)?.side === 'sell' ? '卖点质量' : '买点质量' : label}<select value={scores[key]?.final ?? ''} onChange={event => edit({ ...scores, [key]: { ...(scores[key] || { final: null, comment: '' }), final: event.target.value ? Number(event.target.value) : null } })}><option value="">未评分</option>{Array.from({ length: 11 }, (_, i) => <option key={i} value={i}>{i}</option>)}</select></label>{sheet?.scores[key]?.ai !== null && sheet?.scores[key]?.ai !== undefined && <div><p className="muted">AI 建议：{sheet.scores[key].ai} · {sheet.scores[key].ai_comment || '未提供评语'}<br />{sheet.scores[key].final_source === 'ai_accepted' ? '此维度最终分已由人工采用建议' : '尚未采用本维度建议'}</p><button type="button" className="button secondary" disabled={dirty || buffer.conflict || versionConflict || !loaded} onClick={() => copySuggestion(key)}>采用此维度建议</button>{dirty && <p className="muted">请先保存当前人工编辑，再采用 AI 建议。</p>}</div>}<label className="field">评语<input value={scores[key]?.comment ?? ''} onChange={event => edit({ ...scores, [key]: { ...(scores[key] || { final: null, comment: '' }), comment: event.target.value } })} /></label></div>)}</div>
    <label className="field">整体点评<textarea value={comment} onChange={event => edit(scores, event.target.value)} /></label>
    <button className="button primary" disabled={!loaded || !canSave || buffer.conflict || versionConflict} onClick={save}><Icon name="save" />保存人工评分</button></fieldset>
  </section>
}
