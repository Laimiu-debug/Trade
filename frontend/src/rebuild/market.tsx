import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { api, type Trade, type SimFill, type SimOrder } from './api'
import { MarketChart, type ChartBar, type ChartExecution } from './market-chart'
import { IntradayChart, type IntradayData } from './intraday-chart'
import { IntradayWorkspace } from './intraday-workspace'
import { MarketCloseLookup } from './market-close'
import { MarketDiagnostics } from './market-diagnostics'
import { StockAnnotationEditor } from './stock-annotation'
import { StockAIOverlay } from './stock-ai-overlay'
import { sameMarketSymbol } from './market-symbols'
import { StockSearch } from './stock-search'
import { useWorkspacePage } from './use-workspace-page'
import { WorkspaceNavigation } from './workspace-navigation'
import { Icon } from './workspace-icons'

const marketPages = ['library', 'import', 'sync', 'quality'] as const

type Dataset = { id: string; symbol: string; provider: string; adjustment: string; first_date: string; last_date: string; bar_count: number; availability_quality: string; source?: { filename?: string; sha256?: string; file_mtime_utc?: string; upstream?: string; canonical_rows_sha256?: string; volume_unit?: string } | null; bars?: ChartBar[] }
type SyncResult = { dataset: Dataset; fetched_count: number; changed: boolean; fetch_start: string; actual_provider: 'akshare' | 'baostock'; attempted_providers: string[]; fallback_errors: Array<{ provider: string; code: string }> }
type SyncJob = { id: string; state: string; provider: string; symbols: string[]; total: number; completed: number; results: Array<{ symbol: string; state: string; provider?: string; dataset_id?: string; errors?: Array<{ provider: string; code: string }> }> }

export function MarketEditor({ accountId, accountKind, trades = [], initialDatasetId, onDatasetChange }: { accountId: string; accountKind: string; trades?: Trade[]; initialDatasetId?: string | null; onDatasetChange?: (id: string | null) => void }) {
  const [marketPage, setMarketPage] = useWorkspacePage('market-view', marketPages, 'library')
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [selected, setSelected] = useState<Dataset | null>(null)
  const datasetRequest = useRef(0)
  const loadDataset = useCallback(async (id: string, updateRoute = true) => {
    const stamp = ++datasetRequest.current
    const value = await api<Dataset>('/market/datasets/' + id)
    if (stamp !== datasetRequest.current) return
    setSelected(value)
    if (updateRoute) { setMarketPage('library'); onDatasetChange?.(id) }
  }, [onDatasetChange, setMarketPage])
  const [intraday, setIntraday] = useState<IntradayData | null>(null)
  const intradayRequest = useRef(0)
  const [aiStartDate, setAIStartDate] = useState<string | null>(null)
  useEffect(() => { setAIStartDate(null) }, [selected?.id])
  const [manualStartDate, setManualStartDate] = useState<string | null>(null)
  const [simExecutions, setSimExecutions] = useState<ChartExecution[]>([])
  const [symbol, setSymbol] = useState('')
  const [adjustment, setAdjustment] = useState('none')
  const [csv, setCsv] = useState('')
  const [tdxSymbol, setTdxSymbol] = useState('')
  const [cacheSymbol, setCacheSymbol] = useState('')
  const [cacheProvider, setCacheProvider] = useState<'akshare' | 'baostock'>('akshare')
  const [syncSymbol, setSyncSymbol] = useState('')
  const [syncStart, setSyncStart] = useState('2020-01-01')
  const [syncEnd, setSyncEnd] = useState(() => new Date().toLocaleDateString('sv-SE'))
  const [syncMode, setSyncMode] = useState<'incremental' | 'full'>('incremental')
  const [syncProvider, setSyncProvider] = useState<'auto' | 'akshare' | 'baostock'>('auto')
  const [preferredProvider, setPreferredProvider] = useState<'akshare' | 'baostock'>('baostock')
  const [sourceRevision, setSourceRevision] = useState<number | null>(null)
  const [syncBusy, setSyncBusy] = useState(false)
  const [batchSymbols, setBatchSymbols] = useState('')
  const [syncJobs, setSyncJobs] = useState<SyncJob[]>([])
  const completedJobs = useRef(new Set<string>())
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const refresh = useCallback(() => api<Dataset[]>('/market/datasets').then(setDatasets), [])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])
  useEffect(() => {
    if (initialDatasetId && initialDatasetId !== selected?.id) void loadDataset(initialDatasetId, false)
      .catch(err => setError(err instanceof Error ? err.message : '指定行情样本读取失败'))
    else if (!initialDatasetId) setSelected(null)
    return () => { datasetRequest.current += 1 }
  }, [initialDatasetId, loadDataset])
  useEffect(() => {
    let active = true
    api<{ revision: number; value: { provider: 'auto' | 'akshare' | 'baostock'; provider_order: Array<'akshare' | 'baostock'> } }>('/settings/groups/market_sources')
      .then(value => { if (active) { setSyncProvider(value.value.provider); setPreferredProvider(value.value.provider_order[0]); setSourceRevision(value.revision) } })
      .catch(err => { if (active) setError(`读取全局行情来源失败：${err.message}；重新打开行情页后重试。`) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    let active = true
    const load = () => api<SyncJob[]>('/market/sync-jobs').then(rows => {
      if (active) {
        setSyncJobs(rows)
        const newlyCompleted = rows.filter(row => ['succeeded', 'partial_failed'].includes(row.state) && !completedJobs.current.has(row.id))
        newlyCompleted.forEach(row => completedJobs.current.add(row.id))
        if (newlyCompleted.length) refresh().catch(() => {})
      }
    }).catch(() => {})
    load()
    const timer = window.setInterval(load, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [refresh])
  useEffect(() => { intradayRequest.current += 1; setIntraday(null); setManualStartDate(null)
    return () => { intradayRequest.current += 1 }
  }, [selected?.id])
  useEffect(() => {
    if (accountKind !== 'sim') return
    let active = true
    Promise.all([api<SimOrder[]>(`/sim-accounts/${accountId}/orders`), api<SimFill[]>(`/sim-accounts/${accountId}/fills`)]).then(([orders, fills]) => {
      const byId = new Map(orders.map(order => [order.id, order]))
      if (active) setSimExecutions(fills.flatMap(fill => {
        const order = byId.get(fill.order_id)
        return order ? [{ id: fill.id, date: fill.fill_date, symbol: order.symbol,
          side: order.side, quantity: order.quantity, price: fill.fill_price, source: 'sim' as const }] : []
      }))
    }).catch(err => { if (active) setError(err instanceof Error ? err.message : '模拟成交读取失败') })
    return () => { active = false }
  }, [accountId, accountKind])
  const executions: ChartExecution[] = accountKind === 'sim' ? simExecutions : trades.map(row => ({
    id: row.id, date: row.trade_date, symbol: row.symbol, side: row.side,
    quantity: row.quantity, price: row.price, source: 'real',
  }))

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    try {
      const lines = csv.trim().split(/\r?\n/).map(line => line.split(',').map(cell => cell.trim()))
      if (lines[0]?.[0]?.toLowerCase() === 'date') lines.shift()
      const bars = lines.map(columns => {
        if (columns.length < 6 || columns.length > 8) throw new Error('每行需要 date,open,high,low,close,volume，可选 available_at,amount')
        return { event_date: columns[0], open: columns[1], high: columns[2], low: columns[3], close: columns[4], volume: Number(columns[5]), available_at: columns[6] || null, amount: columns[7] || null }
      })
      const saved = await api<Dataset>('/market/datasets', 'POST', { symbol, adjustment, bars })
      await refresh()
      await loadDataset(saved.id)
      setNotice('不可变行情样本已保存；历史可得性以每行时间标记为准。')
    } catch (err) { setError(err instanceof Error ? err.message : '行情导入失败') }
  }

  async function importTdx(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    try {
      const saved = await api<Dataset>('/market/tdx-import', 'POST', { symbol: tdxSymbol })
      await refresh()
      await loadDataset(saved.id)
      setNotice('通达信日线已复制为不可变样本；原文件未修改。历史可得时间未知，严格研究会排除这些 K 线。')
    } catch (err) { setError(err instanceof Error ? err.message : '通达信导入失败') }
  }

  async function importCache(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    try {
      const saved = await api<Dataset>('/market/cache-import', 'POST', {
        provider: cacheProvider, symbol: cacheSymbol,
      })
      await refresh()
      await loadDataset(saved.id)
      setNotice('本地缓存已复制为不可变样本；原 CSV 未修改。历史可得时间未知。')
    } catch (err) { setError(err instanceof Error ? err.message : '本地行情缓存导入失败') }
  }

  async function syncAkshare(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice(''); setSyncBusy(true)
    try {
      if (sourceRevision === null) throw new Error('全局行情来源尚未读取，暂不能提交联网请求')
      const result = await api<SyncResult>('/market/online-sync', 'POST', {
        provider: syncProvider, provider_order: preferredProvider === 'baostock' ? ['baostock', 'akshare'] : ['akshare', 'baostock'],
        symbol: syncSymbol, start_date: syncStart, end_date: syncEnd, mode: syncMode,
      })
      await refresh()
      await loadDataset(result.dataset.id)
      const sourceName = result.actual_provider === 'akshare' ? 'AKShare' : 'BaoStock'
      const fallbackNote = result.fallback_errors.length ? `；首选来源失败，已回退到 ${sourceName}` : ''
      setNotice(result.changed
        ? `已从 ${sourceName} 获取 ${result.fetched_count} 根日线，保存为新的冻结样本${fallbackNote}。`
        : `已检查 ${sourceName} ${result.fetched_count} 根日线；冻结样本内容未变${fallbackNote}。`)
    } catch (err) { setError(err instanceof Error ? err.message : '在线行情同步失败') }
    finally { setSyncBusy(false) }
  }

  async function submitBatch(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    const symbols = batchSymbols.split(/[\s,，;；]+/).map(item => item.trim()).filter(Boolean)
    try {
      if (sourceRevision === null) throw new Error('全局行情来源尚未读取，暂不能提交联网请求')
      const job = await api<SyncJob>('/market/sync-jobs', 'POST', {
        symbols, provider: syncProvider,
        provider_order: preferredProvider === 'baostock' ? ['baostock', 'akshare'] : ['akshare', 'baostock'],
        start_date: syncStart, end_date: syncEnd, mode: syncMode,
      })
      setSyncJobs(previous => [job, ...previous])
      setNotice(`已提交 ${job.total} 只证券的同步任务；可以离开页面，进度会保留。`)
    } catch (err) { setError(err instanceof Error ? err.message : '批量同步提交失败') }
  }

  async function cancelBatch(jobId: string) {
    try {
      const job = await api<SyncJob>(`/market/sync-jobs/${jobId}/cancel`, 'POST', {})
      setSyncJobs(previous => previous.map(item => item.id === jobId ? job : item))
    } catch (err) { setError(err instanceof Error ? err.message : '取消任务失败') }
  }

  async function retryBatchFailures(jobId: string) {
    try {
      const job = await api<SyncJob>(`/market/sync-jobs/${jobId}/retry-failed`, 'POST', {})
      setSyncJobs(previous => [job, ...previous])
      setNotice(`已为 ${job.total} 只失败证券创建新任务；原任务记录已保留。`)
    } catch (err) { setError(err instanceof Error ? err.message : '重试失败证券失败') }
  }

  async function openIntraday(day: string) {
    if (!selected) return
    const stamp = ++intradayRequest.current
    setError(''); setIntraday(null)
    try {
      const query = new URLSearchParams({ symbol: selected.symbol, day })
      const result = await api<IntradayData>('/market/tdx-intraday?' + query.toString())
      if (stamp === intradayRequest.current) setIntraday(result)
    } catch (err) { if (stamp === intradayRequest.current) setError(err instanceof Error ? err.message : '本地分时读取失败') }
  }

  return <><WorkspaceNavigation label="行情页面" current={marketPage} items={[["library", "样本与图表"], ["import", "导入样本"], ["sync", "在线同步"], ["quality", "数据质量"]]} onChange={setMarketPage} />
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    <section className="card" hidden={marketPage !== 'library'}><h2 className="title-with-icon"><Icon name="market" />本地行情数据集</h2><p className="muted">同一内容复用同一哈希 ID。更新行情会产生新数据集，不覆盖旧版本。</p>
    <StockSearch onSelect={row => { setTdxSymbol(row.prefixed_symbol); setCacheSymbol(row.prefixed_symbol); setSyncSymbol(row.prefixed_symbol); setSymbol(row.symbol); const found = datasets.find(dataset => sameMarketSymbol(dataset.symbol, row.prefixed_symbol)); if (found) loadDataset(found.id).catch(err => setError(err.message)) }} />
    <div className="table-wrap"><table><thead><tr><th>代码</th><th>来源</th><th>日期范围</th><th>日数</th><th>复权</th><th>可得性</th><th>查看</th></tr></thead><tbody>{datasets.map(row => <tr key={row.id}><td><strong>{row.symbol}</strong><br /><small className="muted">{row.id.slice(0, 12)}</small></td><td>{({ tdx_local: '通达信本地', akshare_cache: 'AkShare 缓存', baostock_cache: 'Baostock 缓存', akshare_online: 'AKShare 在线', baostock_online: 'BaoStock 在线', manual_import: '手工导入' } as Record<string, string>)[row.provider] || row.provider}</td><td>{row.first_date} 至 {row.last_date}</td><td>{row.bar_count}</td><td>{row.adjustment}</td><td>{row.availability_quality === 'provided_availability' ? '提供了可得时间' : '历史可得时间未知'}</td><td><button className="link-button" onClick={() => loadDataset(row.id).catch(err => setError(err.message))}>查看</button></td></tr>)}</tbody></table></div>{!datasets.length && <p className="muted">暂无行情样本</p>}

    {selected && <div className="market-detail"><h3>{selected.symbol} · {selected.id.slice(0, 16)}</h3>{selected.source && <p className="muted">来源：{selected.source.filename || selected.source.upstream || '未知'}{selected.source.sha256 && ` · 原文件 SHA-256 ${selected.source.sha256.slice(0, 16)}`}{selected.source.canonical_rows_sha256 && ` · 行情内容 SHA-256 ${selected.source.canonical_rows_sha256.slice(0, 16)}`}{selected.source.file_mtime_utc && ` · 文件修改时间 ${selected.source.file_mtime_utc}`}{selected.source.volume_unit && ` · 成交量 ${selected.source.volume_unit}`}</p>}{selected.bars && <MarketChart key={selected.id} datasetId={selected.id} bars={selected.bars} symbol={selected.symbol} quality={selected.availability_quality} onOpenIntraday={openIntraday} manualStartDate={manualStartDate} aiStartDate={aiStartDate} executions={executions} />}{intraday && <IntradayChart data={intraday} />}<IntradayWorkspace key={selected.id} symbol={selected.symbol} initialDay={intraday?.date || selected.last_date} /><MarketCloseLookup datasetId={selected.id} lastDate={selected.last_date} /><StockAIOverlay key={`${accountId}:${selected.id}`} accountId={accountId} datasetId={selected.id} manualDate={manualStartDate} onDate={setAIStartDate} /><StockAnnotationEditor symbol={selected.symbol} lastDate={selected.last_date} onStartDate={setManualStartDate} /><div className="table-wrap"><table><thead><tr><th>日期</th><th>开</th><th>高</th><th>低</th><th>收</th><th>量</th><th>额</th><th>可得时间</th></tr></thead><tbody>{selected.bars?.map(row => <tr key={row.event_date}><td>{row.event_date}</td><td>{row.open}</td><td>{row.high}</td><td>{row.low}</td><td>{row.close}</td><td>{row.volume}</td><td>{row.amount ?? '缺失'}</td><td>{row.available_at ?? '未知'}</td></tr>)}</tbody></table></div></div>}
  </section><section className="card reading" hidden={marketPage !== 'import'}><h2 className="title-with-icon"><Icon name="upload" />导入固定样本</h2><p className="muted">CSV 列：date,open,high,low,close,volume,available_at,amount。最后两列可选；amount 是成交额（元）。日期升序；available_at 需带时区。缺失时只能用于带质量提示的非严格研究。</p>

    <form className="form" onSubmit={submit}><label className="field"><span>证券代码</span><input value={symbol} onChange={event => setSymbol(event.target.value)} required /></label><label className="field"><span>复权</span><select value={adjustment} onChange={event => setAdjustment(event.target.value)}><option value="none">不复权</option></select></label><label className="field"><span>CSV 内容</span><textarea rows={14} value={csv} onChange={event => setCsv(event.target.value)} placeholder={'date,open,high,low,close,volume,available_at\n2025-01-01,10,11,9,10.5,1000,2025-01-02T00:00:00+00:00\n2025-01-02,10.5,12,10,11.5,1200,2025-01-03T00:00:00+00:00'} required /></label><button className="button primary"><Icon name="save" />保存不可变样本</button></form>
    <h3>从通达信本地目录导入</h3><p className="muted">启动时自动识别本机通达信；也可在“系统设置 → 行情来源”选择目录或扫描本机。按代码读取 .day 文件，最多保留最近 2000 根日线，不修改原文件。</p><form className="form" onSubmit={importTdx}><label className="field"><span>通达信证券代码</span><input value={tdxSymbol} onChange={event => setTdxSymbol(event.target.value)} placeholder="例如 sh600000" required /></label><button className="button secondary"><Icon name="upload" />导入通达信日线</button></form>
    <h3>从旧版行情缓存导入</h3><p className="muted">分别设置 TRADE_AKSHARE_CACHE_DIR 或 TRADE_BAOSTOCK_CACHE_DIR 为旧同步脚本的 CSV 输出目录。选择实际来源；最多保留最近 2000 根日线，导入不会联网或修改旧文件。</p><form className="form" onSubmit={importCache}><label className="field"><span>缓存来源</span><select value={cacheProvider} onChange={event => setCacheProvider(event.target.value as 'akshare' | 'baostock')}><option value="akshare">AkShare</option><option value="baostock">Baostock</option></select></label><label className="field"><span>缓存证券代码</span><input value={cacheSymbol} onChange={event => setCacheSymbol(event.target.value)} placeholder="例如 sh600000" required /></label><button className="button secondary"><Icon name="upload" />导入本地缓存</button></form>
  </section><section className="card reading" hidden={marketPage !== 'sync'}><h2 className="title-with-icon"><Icon name="refresh" />在线日线同步</h2><p className="muted">来源默认来自系统设置的全局配置（修订 {sourceRevision ?? '读取中'}）。本页临时选源只作用于本次单股或批量请求，不自动覆盖全局；旧本机来源可在系统设置中预览迁入。</p><p className="muted">联网获取单股不复权日线。自动模式按选定优先级尝试来源。AKShare 成交量从“手”换算为“股”，BaoStock 原值为“股”；成交额均为元。历史可得时间未知，严格时点研究会排除。增量模式重取最近 14 个自然日并与上次同来源冻结样本合并；同内容复用样本 ID。BaoStock 目前仅支持沪深代码。</p><form className="form" onSubmit={syncAkshare}><label className="field"><span>在线来源</span><select value={syncProvider} onChange={event => setSyncProvider(event.target.value as 'auto' | 'akshare' | 'baostock')}><option value="auto">自动回退</option><option value="akshare">AKShare / 东方财富</option><option value="baostock">BaoStock</option></select></label>{syncProvider === 'auto' && <label className="field"><span>自动优先来源</span><select value={preferredProvider} onChange={event => setPreferredProvider(event.target.value as 'akshare' | 'baostock')}><option value="baostock">BaoStock → AKShare</option><option value="akshare">AKShare → BaoStock</option></select></label>}<label className="field"><span>在线同步证券代码</span><input value={syncSymbol} onChange={event => setSyncSymbol(event.target.value)} placeholder="例如 sh600000" required /></label><div className="form-grid"><label className="field"><span>起始日期</span><input type="date" value={syncStart} onChange={event => setSyncStart(event.target.value)} required /></label><label className="field"><span>截止日期</span><input type="date" value={syncEnd} onChange={event => setSyncEnd(event.target.value)} required /></label></div><label className="field"><span>同步方式</span><select value={syncMode} onChange={event => setSyncMode(event.target.value as 'incremental' | 'full')}><option value="incremental">增量合并</option><option value="full">按日期范围全量重取</option></select></label><button className="button secondary" disabled={syncBusy}>{syncBusy ? '正在联网获取…' : '同步在线日线'}</button></form>
    <h3>批量在线同步</h3><p className="muted">最多 50 只证券，逐只抓取并记录结果。任务可跨页面和服务重启恢复，取消会在当前证券处理完成后生效。</p><form className="form" onSubmit={submitBatch}><label className="field"><span>证券代码（逗号、空格或换行分隔）</span><textarea rows={4} value={batchSymbols} onChange={event => setBatchSymbols(event.target.value)} placeholder="sh600000, sz000001" required /></label><button className="button secondary"><Icon name="check" />提交批量任务</button></form>
    {syncJobs.length > 0 && <div className="table-wrap"><table><thead><tr><th>任务</th><th>进度</th><th>状态</th><th>结果</th><th>操作</th></tr></thead><tbody>{syncJobs.map(job => <tr key={job.id}><td>{job.id.slice(0, 8)}</td><td>{job.completed}/{job.total}</td><td>{({ queued: '排队中', running: '运行中', cancelling: '取消中', cancelled: '已取消', succeeded: '已完成', partial_failed: '部分失败' } as Record<string, string>)[job.state] || job.state}</td><td>{job.results.map(item => <div key={item.symbol}>{item.symbol}：{item.state === 'succeeded' ? `成功 · ${item.provider}` : `失败 · ${item.errors?.map(error => error.code).join(', ')}`}{item.dataset_id && <button className="link-button" onClick={() => loadDataset(item.dataset_id!).catch(err => setError(err.message))}>查看</button>}</div>)}</td><td>{['queued', 'running', 'cancelling'].includes(job.state) && <button className="link-button" onClick={() => cancelBatch(job.id)}>取消</button>}{job.state === 'partial_failed' && <button className="link-button" onClick={() => retryBatchFailures(job.id)}>重试失败代码</button>}</td></tr>)}</tbody></table></div>}
  </section><section className="card" hidden={marketPage !== 'quality'}><h2 className="title-with-icon"><Icon name="check" />数据质量</h2><MarketDiagnostics key={datasets.map(row => row.id).join(',')} /></section></>
}

