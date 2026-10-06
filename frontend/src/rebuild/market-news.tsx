import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type NewsItem = { id: string; title: string; snippet: string; url: string | null; published_at: string; source_name: string; provider: string }
type NewsView = {
  request: { query: string; provider: string; age_hours: number; as_of_at: string; date_from: string | null; date_to: string | null; window_start: string; window_end: string }
  snapshot_id: string | null; fetched_at: string | null; actual_provider: string | null; source_url: string | null
  items: NewsItem[]; count: number; source_item_count: number; excluded: Record<string, number>
  cache_hit: boolean; cache_age_seconds: number | null; cache_stale: boolean; cache_ttl_seconds: number
  attempted_providers: string[]; fallback_used: boolean; degraded: boolean; errors: Array<{ provider: string; code: string }>
  status: 'ready' | 'empty' | 'not_loaded' | 'unavailable'; availability_quality: string; notes: string[]
}
const providers: Record<string, string> = { auto: '自动：东方财富 → Google RSS', eastmoney: '东方财富快讯', google_rss: 'Google News RSS' }
const reasons: Record<string, string> = { NEWS_SOURCE_TIMEOUT: '来源请求超时', NEWS_SOURCE_UNAVAILABLE: '来源暂不可用', NEWS_SOURCE_FORMAT: '来源返回格式变化', NEWS_SOURCE_LIMIT: '响应超过读取限制' }
const localNow = () => {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
const dateTime = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '尚未抓取'
const headlineKey = (value: string) => value.replace(/[\s\p{P}\p{S}]/gu, '')

export function MarketNewsEditor() {
  const [query, setQuery] = useState('A股 热点')
  const [provider, setProvider] = useState('auto')
  const [hours, setHours] = useState(72)
  const [asOf, setAsOf] = useState(localNow)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [result, setResult] = useState<NewsView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState(0)
  const requestSequence = useRef(0)
  useEffect(() => {
    const sequence = ++requestSequence.current
    api<NewsView>('/market/news').then(row => { if (sequence === requestSequence.current) setResult(row) })
      .catch(err => { if (sequence === requestSequence.current) setError(err.message) })
    return () => { requestSequence.current += 1 }
  }, [])

  async function load(refresh: boolean) {
    setError('')
    const parsed = new Date(asOf)
    if (!query.trim() || !Number.isFinite(parsed.getTime()) || Boolean(from) !== Boolean(to) || (from && from > to)) {
      setError('请填写查询词和有效截至时间；复盘起止日期需同时填写且顺序正确。')
      return
    }
    const sequence = ++requestSequence.current
    setBusy(true)
    try {
      const body = { query: query.trim(), provider, age_hours: hours, as_of_at: parsed.toISOString(), date_from: from || null, date_to: to || null }
      const params = new URLSearchParams({ query: body.query, provider, age_hours: String(hours), as_of_at: body.as_of_at })
      if (from) { params.set('date_from', from); params.set('date_to', to) }
      const row = await api<NewsView>(refresh ? '/market/news/refresh' : '/market/news?' + params, refresh ? 'POST' : 'GET', refresh ? body : undefined)
      if (sequence === requestSequence.current) { setResult(row); setPage(0) }
    } catch (err) { if (sequence === requestSequence.current) setError(err instanceof Error ? err.message : '资讯读取失败') }
    finally { if (sequence === requestSequence.current) setBusy(false) }
  }

  const totalPages = Math.max(1, Math.ceil((result?.items.length || 0) / 20))
  return <section className="card span-all"><h2 className="title-with-icon"><Icon name="news" />市场资讯</h2>
    <p className="muted">按固定截至时间查看近期资讯。进入页面只读取本地缓存；点击“联网刷新”才请求新闻来源。历史窗口只过滤已抓取条目，不补造历史新闻。</p>
    <form className="form" onSubmit={event => { event.preventDefault(); void load(false) }}>
      <div className="form-grid">
        <label className="field">查询词<input value={query} onChange={event => setQuery(event.target.value)} maxLength={120} required /></label>
        <label className="field">资讯来源<select value={provider} onChange={event => setProvider(event.target.value)}>{Object.entries(providers).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="field">时效窗口<select value={hours} onChange={event => setHours(Number(event.target.value))}>{[24, 48, 72].map(value => <option key={value} value={value}>近 {value} 小时</option>)}</select></label>
        <label className="field">截至时间（设备时区）<input type="datetime-local" value={asOf} onChange={event => setAsOf(event.target.value)} required /></label>
        <label className="field">复盘开始日期（可选，上海时区）<input type="date" value={from} onChange={event => setFrom(event.target.value)} /></label>
        <label className="field">复盘结束日期（可选，上海时区）<input type="date" value={to} onChange={event => setTo(event.target.value)} /></label>
      </div>
      <div className="form-actions"><button className="button secondary" disabled={busy}>读取缓存</button><button className="button primary" type="button" disabled={busy} onClick={() => void load(true)}>{busy ? '读取中…' : '联网刷新'}</button><button className="button ghost" type="button" disabled={busy} onClick={() => setAsOf(localNow())}>截至现在</button><button className="button ghost" type="button" onClick={() => { setFrom(''); setTo('') }} disabled={busy}>清除复盘日期</button></div>
    </form>
    {error && <p className="alert error" role="alert">{error}</p>}
    {result && <>
      <div className="period-summary"><span>结果窗口：{result.request.age_hours} 小时</span><span>截至：{dateTime(result.request.as_of_at)}（上海）</span><span>实际来源：{result.actual_provider ? providers[result.actual_provider] : '尚无'}</span><span>{result.cache_hit ? '读取缓存' : result.snapshot_id ? '本次联网获取' : '尚无抓取快照'}</span><span>抓取时间：{dateTime(result.fetched_at)}</span><span>缓存年龄：{result.cache_age_seconds === null ? '—' : `${result.cache_age_seconds} 秒`}</span><span>窗口内：{result.count} 条 / 来源样本 {result.source_item_count} 条</span></div>
      {result.cache_stale && <p className="alert">缓存已超过 {result.cache_ttl_seconds / 60} 分钟，页面保留原抓取时间。需要最新资讯时请联网刷新。</p>}
      {result.fallback_used && <p className="muted">本次使用了来源回退或先前缓存，以下显示实际来源与错误信息。</p>}
      {result.errors.length > 0 && <p className="alert" role="status">{result.errors.map(item => `${providers[item.provider] || item.provider}：${reasons[item.code] || item.code}`).join('；')}</p>}
      {result.status === 'not_loaded' && <p className="muted">当前查询还没有本地资讯。点击“联网刷新”获取。</p>}
      {result.status === 'unavailable' && <p className="alert error">资讯源暂时不可用，也没有可回退的本地缓存。没有生成替代新闻。</p>}
      {result.status === 'empty' && <p className="muted">已获取来源样本，但所选时间/复盘日期/查询词内没有可确认发布时间的资讯。可调整窗口后读取缓存；来源不保证历史覆盖。</p>}
      <p className="muted">已排除：发布时间未知 {result.excluded.unknown_publication_time} 条、晚于截至时间 {result.excluded.after_as_of} 条、窗口外 {result.excluded.outside_window} 条。</p>
      {result.items.length > 0 && <><ul className="news-feed" aria-label="资讯列表">{result.items.slice(page * 20, (page + 1) * 20).map(row => <li key={row.id}><article className="news-article"><h3>{row.url ? <a href={row.url} target="_blank" rel="noopener noreferrer">{row.title}</a> : row.title}</h3><div className="news-meta"><time dateTime={row.published_at}>{dateTime(row.published_at)}（上海）</time><span>{row.source_name} · {providers[row.provider]}</span></div>{row.snippet && headlineKey(row.snippet) !== headlineKey(row.title) && <p>{row.snippet}</p>}</article></li>)}</ul><div className="form-actions"><button className="button ghost" disabled={page === 0} onClick={() => setPage(value => value - 1)}>上一页</button><span>{page + 1} / {totalPages}</span><button className="button ghost" disabled={page + 1 >= totalPages} onClick={() => setPage(value => value + 1)}>下一页</button></div></>}
      <details><summary>时间口径与来源记录</summary><p>窗口：{dateTime(result.request.window_start)} 至 {dateTime(result.request.window_end)}（上海）；查询词：{result.request.query}。</p><p>抓取快照：{result.snapshot_id || '无'}；尝试来源：{result.attempted_providers.map(item => providers[item] || item).join(' → ') || '无'}。</p>{result.notes.map(note => <p key={note} className="muted">{note}</p>)}</details>
    </>}
  </section>
}
