import { useEffect, useState } from 'react'
import { api } from './api'
import { DirectoryPicker } from './directory-picker'
import { Icon } from './workspace-icons'

type Source = { id: string; name: string; network: boolean; configured: boolean; instruments: string[]; daily: boolean; intraday: string | null; location: string | null; notes: string }
type Probe = { status: string; symbol: string; checked_at: string; sample_count?: number; first_date?: string; last_date?: string; elapsed_ms: number; error_code?: string; message?: string; scope?: string }
type Locations = { current_path: string | null; selection: 'unset' | 'auto' | 'manual' | 'environment'; persisted: false; candidates: Array<{ path: string; vipdoc: string; markets: string[] }>; limited: boolean; scope: string }
const states: Record<string, string> = { ok: '本次日线样本有效', readable: '本地样本可读', failed: '测试失败', empty: '范围内无数据', insufficient_sample: '样本不足' }
const localDay = (offset = 0) => { const value = new Date(); value.setDate(value.getDate() + offset); return value.toLocaleDateString('sv-SE') }

export function ProviderHealth({ onBusy }: { onBusy?: (value: boolean) => void }) {
  const [sources, setSources] = useState<Source[]>([])
  const [error, setError] = useState('')
  const [symbol, setSymbol] = useState('600000')
  const [start, setStart] = useState(() => localDay(-14))
  const [end, setEnd] = useState(() => localDay())
  const [busy, setBusy] = useState('')
  const [results, setResults] = useState<Record<string, Probe>>({})
  const [locations, setLocations] = useState<Locations | null>(null)
  const [tdxPath, setTdxPath] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => {
    let current = true
    api<{ sources: Source[] }>('/market/providers').then(value => { if (current) setSources(value.sources) })
      .catch(err => { if (current) setError(err.message) })
    api<Locations>('/market/providers/tdx/locations').then(value => { if (current) { setLocations(value); setTdxPath(value.current_path || '') } })
      .catch(err => { if (current) setError(err.message) })
    return () => { current = false }
  }, [])
  async function locate(mode: 'scan' | 'select') {
    setBusy('tdx-location'); onBusy?.(true); setError(''); setNotice('')
    try {
      const value = await api<Locations>(`/market/providers/tdx/${mode}`, 'POST', mode === 'select' ? { path: tdxPath } : {})
      setLocations(value)
      if (mode === 'select') {
        setTdxPath(value.current_path || '')
        setResults(previous => { const next = { ...previous }; delete next.tdx; return next })
        setNotice('本次运行已使用所选目录，可以检查或导入通达信日线。')
      } else {
        if (value.candidates.length === 1) setTdxPath(value.candidates[0].path)
        setNotice(value.candidates.length ? `发现 ${value.candidates.length} 个目录，请选择后点击“使用此目录”。` : '未发现通达信目录，请手动选择安装目录或 vipdoc 目录。')
      }
      setSources((await api<{ sources: Source[] }>('/market/providers')).sources)
    } catch (err) { setError(err instanceof Error ? err.message : '目录识别失败') }
    finally { setBusy(''); onBusy?.(false) }
  }
  async function test(source: Source) {
    setBusy(source.id); onBusy?.(true); setError('')
    try {
      const value = await api<Probe>(`/market/providers/${source.id}/probe`, 'POST', { symbol, start_date: start, end_date: end })
      setResults(previous => ({ ...previous, [source.id]: value }))
    } catch (err) { setError(err instanceof Error ? err.message : '测试失败') }
    finally { setBusy(''); onBusy?.(false) }
  }
  return <section className="card span-all" aria-label="行情能力与连通性"><h2 className="title-with-icon"><Icon name="market" />行情能力与连通性</h2>
    <p className="muted">打开此页只读取适配器能力。点击在线来源的测试按钮才会联网；测试不会导入行情，也不会切换默认来源。已配置不代表连通。</p>
    <section className="market-detail" aria-label="通达信目录选择"><h3>本地通达信目录</h3>
      <p style={{ overflowWrap: 'anywhere' }}>当前目录：{locations?.current_path || '尚未选择'}{locations?.selection === 'auto' ? '（启动时自动识别）' : ''}</p>
      <p className="muted">启动时仅发现一个安装目录会自动使用；多个目录由你选择。手动选择仅本次运行生效，不保存目录配置，下次打开重新识别。</p>
      <label className="field"><span>通达信安装目录或 vipdoc 目录</span><input value={tdxPath} onChange={event => setTdxPath(event.target.value)} disabled={Boolean(busy)} placeholder="选择目录，也可输入完整路径" /></label>
      <div className="form-actions"><DirectoryPicker label="选择通达信目录" selectedMessage="已选中目录，点击“使用此目录”后本次运行生效。" disabled={Boolean(busy)} onSelect={setTdxPath} />
        <button type="button" className="button secondary" disabled={Boolean(busy)} onClick={() => void locate('scan')}>扫描本机</button>
        <button type="button" className="button primary" disabled={Boolean(busy) || !tdxPath.trim()} onClick={() => void locate('select')}>使用此目录</button></div>
      {!!locations?.candidates?.length && <label className="field"><span>发现的通达信目录</span><select value={locations.candidates.some(item => item.path === tdxPath) ? tdxPath : ''} disabled={Boolean(busy)} onChange={event => setTdxPath(event.target.value)}><option value="">请选择目录</option>{locations.candidates.map(item => <option key={item.path} value={item.path}>{item.path} · {item.markets.join(' / ')}</option>)}</select></label>}
      <p className="muted">{locations?.scope}{locations?.limited ? '本次扫描达到范围或时间上限，可手动选择其他位置。' : ''}</p>
      {notice && <p role="status">{notice}</p>}
    </section>
    <div className="form-grid"><label className="field"><span>测试证券代码</span><input value={symbol} onChange={event => setSymbol(event.target.value)} disabled={Boolean(busy)} /></label>
      <label className="field"><span>测试开始日期</span><input type="date" value={start} onChange={event => setStart(event.target.value)} disabled={Boolean(busy)} /></label>
      <label className="field"><span>测试结束日期</span><input type="date" value={end} onChange={event => setEnd(event.target.value)} disabled={Boolean(busy)} /></label></div>
    {error && <p className="alert error" role="alert">{error}</p>}
    <div className="table-wrap"><table><thead><tr><th>来源</th><th>范围 / 周期</th><th>配置</th><th>测试</th></tr></thead><tbody>{sources.map(source => <tr key={source.id}><td>{source.name}<p className="muted">{source.notes}</p></td>
      <td>{source.instruments.join('、')}<br />日线{source.intraday ? ` / ${source.intraday}` : '；在线分时未接入'}<br />未复权</td>
      <td>{source.configured ? source.network ? '依赖已安装' : '目录存在' : source.network ? '缺少依赖' : '目录未配置或不存在'}{source.location && <p className="muted" style={{ overflowWrap: 'anywhere', maxWidth: '20rem' }}>{source.location}</p>}</td>
      <td><button type="button" className="button secondary" disabled={Boolean(busy) || !symbol || !start || !end} onClick={() => void test(source)}>{busy === source.id ? '正在测试…' : source.network ? `联网测试 ${source.name}` : `检查 ${source.name}`}</button>
        {results[source.id] && <div role="status"><strong>{states[results[source.id].status] || results[source.id].status}</strong><p>{results[source.id].symbol} · {results[source.id].sample_count ?? '—'} 条 · {results[source.id].elapsed_ms}ms</p>
          <p className="muted">{results[source.id].first_date} {results[source.id].last_date ? `至 ${results[source.id].last_date}` : ''}{results[source.id].message}<br />{results[source.id].error_code}<br />{results[source.id].checked_at}</p></div>}</td></tr>)}</tbody></table></div>
  </section>
}
