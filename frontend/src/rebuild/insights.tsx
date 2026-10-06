import { useCallback, useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Card = { id: string; content: string; tags: string[]; revision: number; created_at: string }

export function DailyInspiration() {
  const [card, setCard] = useState<Card | null>(null)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    api<Card | null>('/insights/daily', 'POST', { day: new Date().toLocaleDateString('sv-SE') })
      .then(setCard).catch(() => {}).finally(() => setLoaded(true))
  }, [])
  return <section className="card"><h2 className="title-with-icon"><Icon name="insights" />今日旧卡温故</h2>{card ? <><p className="inspiration-text">{card.content}</p><div className="period-summary">{card.tags.map(tag => <span key={tag}>{tag}</span>)}<span>记录于 {card.created_at.slice(0, 10)}</span></div></> : <p className="muted">{loaded ? '暂无可温故的旧卡，记下一条灵感，之后再回来看看。' : '正在读取…'}</p>}</section>
}

export function InspirationEditor() {
  const [cards, setCards] = useState<Card[]>([])
  const [content, setContent] = useState('')
  const [tags, setTags] = useState('')
  const [filter, setFilter] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const refresh = useCallback(() => api<Card[]>('/insights/cards').then(setCards), [])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])
  const allTags = useMemo(() => [...new Set(cards.flatMap(card => card.tags))].sort(), [cards])
  const shown = filter ? cards.filter(card => card.tags.includes(filter)) : cards

  async function add() {
    if (!content.trim()) { setError('请先写下灵感内容'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      await api('/insights/cards', 'POST', { content, tags: tags.split(',').map(tag => tag.trim()).filter(Boolean) })
      setContent(''); setTags(''); await refresh(); setNotice('灵感已保存')
    } catch (err) { setError(err instanceof Error ? err.message : '保存失败') }
    finally { setBusy(false) }
  }
  async function remove(card: Card) {
    if (!window.confirm('删除这张灵感卡片？')) return
    setBusy(true); setError(''); setNotice('')
    try { await api(`/insights/cards/${card.id}?expected_revision=${card.revision}`, 'DELETE'); await refresh(); setNotice('灵感已删除') }
    catch (err) { setError(err instanceof Error ? err.message : '删除失败') }
    finally { setBusy(false) }
  }
  function shortcut(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); add().catch(() => {}) }
  }
  return <div><section className="card reading"><h2 className="title-with-icon"><Icon name="insights" />灵感闪记</h2><p className="muted">记录市场规律、交易心得或给自己的提醒。</p><div className="form"><label className="field">内容<textarea rows={5} value={content} onChange={event => setContent(event.target.value)} onKeyDown={shortcut} placeholder="此刻想到的交易规律……" /></label><label className="field">标签（逗号分隔）<input value={tags} onChange={event => setTags(event.target.value)} placeholder="情绪,仓位" /></label><button className="button primary" disabled={busy} onClick={add}>记下（Ctrl+Enter）</button></div>{error && <p className="danger" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}</section>
    {allTags.length > 0 && <div className="period-controls"><button className={filter ? 'button secondary' : 'button primary'} onClick={() => setFilter('')}>全部</button>{allTags.map(tag => <button key={tag} className={filter === tag ? 'button primary' : 'button secondary'} onClick={() => setFilter(tag)}>{tag}</button>)}</div>}
    <div className="inspiration-grid">{shown.map(card => <article className="card" key={card.id}><p className="inspiration-text">{card.content}</p><div className="section-heading"><span className="muted">{card.created_at.slice(0, 10)}</span><button className="link-button danger" disabled={busy} onClick={() => remove(card)}>删除</button></div><div className="period-summary">{card.tags.map(tag => <span key={tag}>{tag}</span>)}</div></article>)}</div>{!shown.length && <section className="card empty"><p>{filter ? '这个标签下还没有卡片' : '还没有闪记，先记下一条灵感。'}</p></section>}
  </div>
}
