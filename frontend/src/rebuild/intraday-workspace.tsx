import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { IntradayChart, type IntradayData } from './intraday-chart'

type Snapshot = { id: string; symbol: string; date: string; as_of_at: string; point_count: number }
type Capabilities = { index_symbols: string[] }
const root = '/market/intraday'

export function IntradayWorkspace({ symbol, initialDay }: { symbol: string; initialDay: string }) {
  const [day, setDay] = useState(initialDay)
  const [rows, setRows] = useState<Snapshot[]>([])
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [data, setData] = useState<IntradayData | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [deleteId, setDeleteId] = useState('')
  const stamp = useRef(0)
  useEffect(() => {
    let active = true
    api<Capabilities>(root + '/capabilities').then(value => { if (active) setCapabilities(value) }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [])
  useEffect(() => { setDay(initialDay) }, [initialDay, symbol])
  useEffect(() => {
    const current = ++stamp.current
    setData(null); setRows([]); setError(''); setNotice(''); setDeleteId(''); setBusy(false)
    if (day) api<Snapshot[]>(root + '/snapshots?' + new URLSearchParams({ symbol, date: day })).then(value => {
      if (current === stamp.current) setRows(value)
    }).catch(err => { if (current === stamp.current) setError(err.message) })
    return () => { stamp.current += 1 }
  }, [day, symbol])
  async function fetchOnline() {
    const current = ++stamp.current
    setBusy(true); setError(''); setNotice(''); setDeleteId('')
    try {
      const result = await api<IntradayData>(root + '/fetch', 'POST', { symbol, date: day })
      if (current !== stamp.current) return
      setData(result)
      setNotice('已保存独立分时缓存；重新进入页面只读取缓存，不自动联网。')
      const saved = await api<Snapshot[]>(root + '/snapshots?' + new URLSearchParams({ symbol, date: day }))
      if (current === stamp.current) setRows(saved)
    } catch (err) { if (current === stamp.current) setError(err instanceof Error ? err.message : '在线分时获取失败') }
    finally { if (current === stamp.current) setBusy(false) }
  }
  async function open(id: string) {
    const current = ++stamp.current
    setBusy(true); setError(''); setDeleteId(''); setNotice('')
    try {
      const saved = await api<IntradayData>(root + '/snapshots/' + id)
      if (current === stamp.current) setData(saved)
    } catch (err) { if (current === stamp.current) setError(err instanceof Error ? err.message : '缓存读取失败') }
    finally { if (current === stamp.current) setBusy(false) }
  }
  async function remove(id: string) {
    const current = ++stamp.current
    setBusy(true); setError('')
    try {
      await api(root + '/snapshots/' + id, 'DELETE')
      if (current !== stamp.current) return
      setRows(previous => previous.filter(row => row.id !== id)); setDeleteId('')
      if (data?.id === id) setData(null)
      setNotice('已删除选定的分时缓存。')
    } catch (err) { if (current === stamp.current) setError(err instanceof Error ? err.message : '缓存删除失败') }
    finally { if (current === stamp.current) setBusy(false) }
  }
  return <section aria-label="在线分时与缓存"><h3>在线分时与缓存</h3><p className="muted">当前证券 {symbol}。主动获取东方财富近期原始 1 分钟数据；请求最近 5 个交易日中的指定日期，实际范围由来源决定。沪深 A 股受支持；指数须带交易所。上证 sh000001 与平安银行 sz000001 分开保存。北京证券、基金及其他指数请使用本地来源。</p><details><summary>在线分时能力与边界</summary><p className="muted">已支持指数：{capabilities?.index_symbols.join('、') || '正在读取'}。不复权；手 / 元，指数价格为点。只显示观察时点以前且该分钟已结束的记录；当前分钟暂不展示。缺少日期或来源异常会明确报错，不生成近似分时。分时不进入严格研究或自动交易。</p></details><div className="form-grid"><label className="field"><span>在线分时日期</span><input type="date" value={day} disabled={busy} onChange={event => setDay(event.target.value)} /></label><button className="button secondary" disabled={busy || !day || !capabilities} onClick={fetchOnline}>{busy ? '正在处理分时…' : '主动联网获取分时'}</button></div>{error && <div className="alert error" role="alert">{error}{data && '；下方仍是此前保存的缓存。'}</div>}{notice && <div className="alert success" role="status">{notice}</div>}<p className="muted">本日缓存（最多显示最近 100 条）：{rows.length} 条。查看和重新加载均不联网。</p>{rows.length > 0 && <div className="table-wrap"><table><thead><tr><th>证券 / 日期</th><th>观察截止 UTC</th><th>记录数</th><th>操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td>{row.symbol} · {row.date}</td><td>{row.as_of_at}</td><td>{row.point_count}</td><td><button className="link-button" disabled={busy} onClick={() => open(row.id)}>查看缓存 {row.id.slice(0, 8)}</button>{deleteId === row.id ? <><button className="link-button" disabled={busy} onClick={() => remove(row.id)}>确认删除此缓存</button><button className="link-button" onClick={() => setDeleteId('')}>保留</button></> : <button className="link-button" disabled={busy} onClick={() => setDeleteId(row.id)}>删除缓存</button>}</td></tr>)}</tbody></table></div>}{data && <IntradayChart data={data} />}</section>
}
