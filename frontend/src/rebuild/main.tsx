import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import type { ApiRequestBody } from './api-contracts.generated'
import { api, connect, type Account, type Analytics, type Flow, type Snapshot, type SnapshotPosition, type Trade } from './api'
const SimulationEditor = React.lazy(() => import('./simulation').then(module => ({ default: module.SimulationEditor })))
const MarketEditor = React.lazy(() => import('./market').then(module => ({ default: module.MarketEditor })))
const ValuationEditor = React.lazy(() => import('./valuation').then(module => ({ default: module.ValuationEditor })))
const ShareCardEditor = React.lazy(() => import('./share-card').then(module => ({ default: module.ShareCardEditor })))
const ResearchWorkspace = React.lazy(() => import('./research-workspace').then(module => ({ default: module.ResearchWorkspace })))
import { readResearchNavigation, researchNavigationUrl, type ResearchNavigation } from './research-navigation'
const SignalWorkspace = React.lazy(() => import('./signal-workspace').then(module => ({ default: module.SignalWorkspace })))
const EventStoreWorkspace = React.lazy(() => import('./event-store').then(module => ({ default: module.EventStoreWorkspace })))
const MarketNewsEditor = React.lazy(() => import('./market-news').then(module => ({ default: module.MarketNewsEditor })))
const SettingsWorkspace = React.lazy(() => import('./settings-workspace').then(module => ({ default: module.SettingsWorkspace })))
const TaskCenter = React.lazy(() => import('./task-center').then(module => ({ default: module.TaskCenter })))
const AIWorkspace = React.lazy(() => import('./ai-workspace').then(module => ({ default: module.AIWorkspace })))
import { PendingTradesEditor } from './pending-trades'
import { RealFeeEditor } from './real-fees'
import { TargetNodes } from './target-nodes'
import { TradeRounds } from './trade-rounds'
const DailyReviewEditor = React.lazy(() => import('./daily-review').then(module => ({ default: module.DailyReviewEditor })))
import { AssetEstimate } from './asset-estimate'
import { DailyInspiration, InspirationEditor } from './insights'
import { PerformanceSummary } from './performance'
import { PrintPreview } from './print-preview'
import { ReviewReminders } from './review-reminders'
import { GlobalAILauncher, type AIActivity } from './ai-launcher'
const PeriodEditor = React.lazy(() => import('./period-editor').then(module => ({ default: module.PeriodEditor })))
import './tokens.generated.css'
import './style.css'
import './workspace.css'
import { Icon, IconTile, WorkspaceIcon, type IconName, type IconTone } from './workspace-icons'
import { designTokens } from './design-tokens.generated'

const NavChart = React.lazy(() => import('./nav-chart'))

type Page = 'overview' | 'statistics' | 'trades' | 'flows' | 'snapshots' | 'review' | 'period' | 'simulation' | 'market' | 'valuation' | 'share' | 'research' | 'backtest' | 'insights' | 'ai' | 'tasks' | 'settings' | 'news' | 'events' | 'signals'
const pages: Page[] = ['overview', 'statistics', 'trades', 'flows', 'snapshots', 'review', 'period', 'simulation', 'market', 'valuation', 'share', 'research', 'backtest', 'insights', 'ai', 'tasks', 'settings', 'news', 'events', 'signals']
const pageDescriptions: Partial<Record<Page, string>> = {
  overview: '资产与持仓概览，日常记录从这里开始。', statistics: '按周期查看收益、交易表现和已完成的交易回合。',
  research: '选择一个研究任务，配置、运行与结果在对应页面完成。', backtest: '先选策略和样本，再核对执行规则与回测结果。',
  news: '阅读按时间归档的市场资讯，来源与抓取时间可核对。', review: '整理当日交易事实、思考和次日计划。',
  period: '回看一周或一个月，记录可复用的经验。', market: '管理行情样本，查看走势图和数据质量。',
  signals: '从研究证据跟踪信号，核对确认状态与后续表现。', settings: '管理显示偏好、数据目录、备份和服务连接。',
  trades: '录入实盘成交，核对费用与待确认交易。', flows: '记录初始资金、入金和出金，净值按份额调整。',
  snapshots: '确认每日总资产与持仓明细，作为净值计算的依据。', simulation: '在模拟账户中下单、结算并复盘策略执行。',
  valuation: '用五因子模型估算情绪估值，比较不同情景。', events: '管理事件判定结果的版本、统计和回填任务。',
  share: '把交易记录生成可分享的长图卡片。', insights: '随手记录灵感卡片，按标签整理并每日温故。',
  ai: '与 AI 对话，带入冻结的上下文生成建议。', tasks: '查看后台任务的进度、结果与失败原因。',
}
/** Default tone per navigation page so each module keeps one recognisable colour. */
const pageTone: Record<string, IconTone> = {
  overview: 'blue', trades: 'violet', flows: 'green', snapshots: 'teal', review: 'amber', period: 'amber',
  statistics: 'blue', market: 'teal', events: 'violet', news: 'pink', valuation: 'amber', research: 'violet',
  signals: 'pink', backtest: 'blue', share: 'teal', insights: 'amber', ai: 'violet', tasks: 'green',
  settings: 'blue', simulation: 'green',
}
function routeState() {
  const query = new URLSearchParams(window.location.search)
  const value = query.get('page') as Page
  return { page: pages.includes(value) ? value : 'overview' as Page,
    account: /^[a-f0-9]{32}$/.test(query.get('account') || '') ? query.get('account')! : '',
    researchRun: /^(?:[a-f0-9]{32}|[a-f0-9]{64})$/.test(query.get('run') || '') ? query.get('run') : null,
    dataset: /^[a-f0-9]{64}$/.test(query.get('dataset') || '') ? query.get('dataset') : null }
}
const today = () => new Date().toLocaleDateString('sv-SE')
const initialTrade = { trade_date: today(), symbol: '', name: '', side: 'buy' as 'buy' | 'sell', quantity: 100, price: '', fee: '0', fee_mode: 'auto' as 'auto' | 'manual', note: '' } satisfies ApiRequestBody<'/accounts/{account_id}/trades', 'post'>
const initialFlow = { flow_date: today(), kind: 'deposit' as 'initial' | 'deposit' | 'withdraw', amount: '', note: '' } satisfies ApiRequestBody<'/accounts/{account_id}/cash-flows', 'post'>

export function App() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountIdState] = useState(() => routeState().account)
  const activeAccountRef = useRef(accountId)
  const accountScope = useRef(0)
  const mutationLocks = useRef(new Set<string>())
  const [mutatingAccounts, setMutatingAccounts] = useState<string[]>([])
  const saving = mutatingAccounts.includes(accountId)
  const refreshGeneration = useRef(0)
  const [page, setPage] = useState<Page>(() => routeState().page)
  const [marketTarget, setMarketTarget] = useState<string | null>(() => routeState().dataset)
  const [researchTarget, setResearchTarget] = useState<string | null>(() => routeState().researchRun)
  const [researchRoute, setResearchRoute] = useState<ResearchNavigation>(() => readResearchNavigation(window.location.search, routeState().page))
  const replaceRoute = useRef(true)
  const [trades, setTrades] = useState<Trade[]>([])
  const [flows, setFlows] = useState<Flow[]>([])
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [tradeForm, setTradeForm] = useState(initialTrade)
  const [editingTrade, setEditingTrade] = useState<Trade | null>(null)
  const [stageTrade, setStageTrade] = useState(false)
  const [pendingRevision, setPendingRevision] = useState(0)
  const [flowForm, setFlowForm] = useState(initialFlow)
  const [snapshotDate, setSnapshotDate] = useState(today())
  const [reviewDate, setReviewDate] = useState('')
  const [snapshotTotal, setSnapshotTotal] = useState('')
  const [snapshotCash, setSnapshotCash] = useState('')
  const [snapshotPosition, setSnapshotPosition] = useState('')
  const [snapshotPositions, setSnapshotPositions] = useState<SnapshotPosition[]>([])
  const [newAccount, setNewAccount] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [ready, setReady] = useState(false)
  const [aiActivity, setAIActivity] = useState<AIActivity>({ running: false, accountId: null, runId: null })
  const aiActivityRef = useRef(aiActivity)
  const updateAIActivity = useCallback((activity: AIActivity) => {
    aiActivityRef.current = activity
    setAIActivity(activity)
  }, [])
  const [themeMode, setThemeMode] = useState<'light' | 'dark' | 'system'>(() => {
    const saved = localStorage.getItem('trade-theme-mode') || localStorage.getItem('trade-theme')
    return saved === 'dark' || saved === 'system' ? saved : 'light'
  })
  const [theme, setTheme] = useState<'light' | 'dark'>('light')
  const [density, setDensity] = useState<'comfortable' | 'compact'>(() => localStorage.getItem('trade-list-density') === 'comfortable' ? 'comfortable' : 'compact')
  const selectedAccount = accounts.find(row => row.id === accountId)

  const setAccountId = useCallback((value: React.SetStateAction<string>) => {
    const previous = activeAccountRef.current
    const next = typeof value === 'function' ? value(previous) : value
    if (next === previous) return
    activeAccountRef.current = next
    accountScope.current += 1
    refreshGeneration.current += 1
    // Draft fields and fetched facts always belong to one account. Clear them
    // in the same update as the switch, before another account can save them.
    setTradeForm({ ...initialTrade, trade_date: today() }); setEditingTrade(null); setStageTrade(false)
    setFlowForm({ ...initialFlow, flow_date: today() })
    setSnapshotDate(today()); setReviewDate(''); setSnapshotTotal(''); setSnapshotCash(''); setSnapshotPosition(''); setSnapshotPositions([])
    setTrades([]); setFlows([]); setSnapshots([]); setAnalytics(null); setError('')
    if (previous && next) setNotice('已切换账户，上一账户未提交的交易、资金和快照表单已清空。')
    setAccountIdState(next)
  }, [])

  useEffect(() => {
    const open = () => setPage('ai')
    window.addEventListener('trade-ai-open', open)
    return () => window.removeEventListener('trade-ai-open', open)
  }, [])

  useEffect(() => {
    const onPop = () => {
      const route = routeState()
      if (aiActivityRef.current.running && route.account !== accountId) {
        const url = new URL(window.location.href)
        url.searchParams.set('account', accountId); url.searchParams.set('page', 'ai')
        window.history.replaceState(null, '', url)
        setPage('ai'); setError('AI 正在生成，请先停止或等待完成后切换账户。')
        return
      }
      replaceRoute.current = true
      setPage(route.page); setMarketTarget(route.dataset); setResearchTarget(route.researchRun)
      setResearchRoute(readResearchNavigation(window.location.search, route.page))
      setAccountId(accounts.some(row => row.id === route.account) ? route.account : accounts[0]?.id || '')
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [accounts, accountId, setAccountId])
  useEffect(() => {
    if (!ready || (accountId && !selectedAccount)) return
    const url = new URL(window.location.href)
    url.searchParams.set('page', page)
    if (accountId) url.searchParams.set('account', accountId)
    else url.searchParams.delete('account')
    if (page === 'market' && marketTarget) url.searchParams.set('dataset', marketTarget)
    else url.searchParams.delete('dataset')
    if (page === 'research' || page === 'backtest') {
      url.href = new URL(researchNavigationUrl(url.href, researchRoute), url).href
      url.searchParams.set('page', page)
    } else {
      for (const key of ['research', 'research-mode', 'research-source', 'strategy', 'run', 'task']) url.searchParams.delete(key)
    }
    if (url.href !== window.location.href) {
      if (replaceRoute.current) window.history.replaceState(null, '', url)
      else window.history.pushState(null, '', url)
      window.dispatchEvent(new Event('research-navigation'))
    }
    replaceRoute.current = false
  }, [ready, accountId, selectedAccount, page, marketTarget, researchRoute])

  function navigateResearch(next: ResearchNavigation) {
    setResearchRoute(next); setResearchTarget(next.runId || null); setPage('research')
  }

  function openMarket(id: string) {
    const url = new URL(window.location.href)
    url.searchParams.set('page', 'market'); url.searchParams.set('dataset', id); url.searchParams.set('market-view', 'library')
    for (const key of ['research', 'research-mode', 'research-source', 'strategy', 'run', 'task']) url.searchParams.delete(key)
    window.history.pushState(null, '', url)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }

  const refreshAccounts = useCallback(async () => {
    const rows = await api<Account[]>('/accounts')
    setAccounts(rows)
    setAccountId(current => current && rows.some(row => row.id === current) ? current : rows[0]?.id || '')
  }, [setAccountId])
  const refresh = useCallback(async (id: string) => {
    if (!id || activeAccountRef.current !== id) return
    const generation = ++refreshGeneration.current
    const base = `/accounts/${id}`
    try {
      const [nextTrades, nextFlows, nextSnapshots, nextAnalytics] = await Promise.all([
        api<Trade[]>(base + '/trades'), api<Flow[]>(base + '/cash-flows'),
        api<Snapshot[]>(base + '/snapshots'), api<Analytics>(base + '/analytics'),
      ])
      if (activeAccountRef.current !== id || generation !== refreshGeneration.current) return
      setTrades(nextTrades); setFlows(nextFlows); setSnapshots(nextSnapshots); setAnalytics(nextAnalytics)
    } catch (err) {
      if (activeAccountRef.current === id && generation === refreshGeneration.current) throw err
    }
  }, [])
  useEffect(() => { connect().then(() => { setReady(true); return refreshAccounts() }).catch(err => setError(err.message)) }, [refreshAccounts])
  useEffect(() => {
    setTrades([]); setFlows([]); setSnapshots([]); setAnalytics(null)
    if (ready && accountId && selectedAccount?.kind === 'real') refresh(accountId).catch(err => setError(err.message))
  }, [ready, accountId, selectedAccount?.kind, refresh])
  useEffect(() => {
    if (selectedAccount?.kind === 'sim') setPage(current => current === 'market' || current === 'valuation' || current === 'share' || current === 'research' || current === 'backtest' || current === 'insights' || current === 'ai' || current === 'tasks' || current === 'settings' || current === 'news' || current === 'events' || current === 'signals' ? current : 'simulation')
    if (selectedAccount?.kind === 'real') setPage(current => current === 'simulation' ? 'overview' : current)
  }, [selectedAccount?.kind, accountId])
  useEffect(() => {
    if (!accountId || analytics?.status !== 'recalculating') return
    const timer = window.setInterval(() => refresh(accountId).catch(err => setError(err.message)), 900)
    return () => window.clearInterval(timer)
  }, [accountId, analytics?.status, refresh])
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      const resolved = themeMode === 'system' ? media.matches ? 'dark' : 'light' : themeMode
      setTheme(resolved)
      document.documentElement.dataset.theme = resolved
    }
    apply()
    media.addEventListener('change', apply)
    localStorage.setItem('trade-theme-mode', themeMode)
    return () => media.removeEventListener('change', apply)
  }, [themeMode])
  useEffect(() => { document.documentElement.dataset.density = density; localStorage.setItem('trade-list-density', density) }, [density])

  async function mutate(message: string, task: (isCurrent: () => boolean) => Promise<unknown>) {
    const owner = accountId
    if (activeAccountRef.current !== owner || mutationLocks.current.has(owner)) return
    // The ref closes the same-event-loop double-submit window before React
    // renders disabled controls. Each account owns its in-flight submission.
    mutationLocks.current.add(owner)
    setMutatingAccounts([...mutationLocks.current])
    const scope = accountScope.current
    const isCurrent = () => activeAccountRef.current === owner && accountScope.current === scope
    setError(''); setNotice('')
    try { await task(isCurrent); if (!isCurrent()) return; setNotice(message); if (selectedAccount?.kind === 'real') await refresh(owner) }
    catch (err) { if (isCurrent()) setError(err instanceof Error ? err.message : '操作失败') }
    finally { mutationLocks.current.delete(owner); setMutatingAccounts([...mutationLocks.current]) }
  }
  const base = `/accounts/${accountId}`
  function openSnapshot(day: string) {
    const row = snapshots.find(item => item.snap_date === day)
    setSnapshotDate(day); setSnapshotTotal(row?.total_assets ?? '')
    setSnapshotCash(row?.available_cash ?? ''); setSnapshotPosition(row?.position_value ?? '')
    setSnapshotPositions(row?.positions ?? []); setPage('snapshots')
  }
  const current = analytics?.result?.nav.current
  const points = analytics?.result?.nav.points || []
  const chartColors = designTokens.themes[theme].color
  const chart = {
    tooltip: { trigger: 'axis', backgroundColor: chartColors['bg.surface'], borderColor: chartColors['border.subtle'], textStyle: { color: chartColors['text.primary'] } },
    grid: { top: 24, left: 58, right: 24, bottom: 35 },
    xAxis: { type: 'category', data: points.map(row => row.date), axisLabel: { color: chartColors['chart.axis'] }, axisLine: { lineStyle: { color: chartColors['chart.grid'] } } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: chartColors['chart.axis'] }, splitLine: { lineStyle: { color: chartColors['chart.grid'] } } },
    series: [{ type: 'line', name: '单位净值', smooth: true, showSymbol: false,
      lineStyle: { width: 2, color: designTokens.themes[theme].color['chart.series1'] },
      data: points.map(row => row.nav === null ? null : Number(row.nav)) }],
  }
  const navigation: Array<[Page, string]> = selectedAccount?.kind === 'sim'
    ? [['simulation', '模拟交易'], ['market', '行情样本'], ['events', '事件仓'], ['news', '市场资讯'], ['valuation', '情绪估值'], ['share', '分享卡'], ['research', '策略研究'], ['signals', '待买信号'], ['backtest', '历史回测'], ['insights', '灵感闪记'], ['ai', 'AI 工作台'], ['tasks', '任务中心'], ['settings', '系统设置']]
    : [['overview', '总览'], ['trades', '交易流水'], ['flows', '资金流水'], ['snapshots', '资产快照'], ['review', '每日复盘'], ['period', '周期复盘'], ['statistics', '统计分析'], ['market', '行情样本'], ['events', '事件仓'], ['news', '市场资讯'], ['valuation', '情绪估值'], ['share', '分享卡'], ['research', '策略研究'], ['signals', '待买信号'], ['backtest', '历史回测'], ['insights', '灵感闪记'], ['ai', 'AI 工作台'], ['tasks', '任务中心'], ['settings', '系统设置']]
  const navigationGroups: Array<[string, Page[]]> = [
    ['账户与复盘', ['overview', 'trades', 'flows', 'snapshots', 'simulation', 'review', 'period', 'statistics']],
    ['行情与研究', ['market', 'news', 'valuation', 'research', 'signals', 'backtest', 'events']],
    ['记录与工具', ['insights', 'share', 'ai', 'tasks', 'settings']],
  ]

  async function addSimAccount() {
    if (aiActivityRef.current.running) { setPage('ai'); setError('AI 正在生成，请先停止或等待完成后创建并切换账户。'); return }
    const name = window.prompt('模拟账户名称')?.trim()
    if (!name) return
    const initialCapital = window.prompt('初始模拟资金（元）', '100000')?.trim()
    if (!initialCapital) return
    await mutate('模拟账户已创建', async isCurrent => {
      const created = await api<Account>('/sim-accounts', 'POST', { name, initial_capital: initialCapital, start_date: today() })
      if (!isCurrent()) return
      await refreshAccounts()
      if (isCurrent()) { setAccountId(created.id); setPage('simulation'); setNotice('模拟账户已创建') }
    })
  }

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><img className="brand-mark" src="/trade-mark.svg" alt="" width="36" height="36" /><span>Trade<span className="brand-sub">交易与复盘工作台</span></span></div>
            <nav aria-label="主导航">{navigationGroups.map(([title, keys]) => <div className="nav-group" key={title}><div className="nav-group-label">{title}</div>{navigation.filter(([key]) => keys.includes(key)).map(([key, label]) =>
        <button key={key} className={page === key ? 'nav-item active' : 'nav-item'} aria-current={page === key ? 'page' : undefined} onClick={() => { if (key === 'market') setMarketTarget(null); if (key === 'research' || key === 'backtest') { setResearchTarget(null); setResearchRoute({ view: key === 'backtest' ? 'backtest' : 'catalog' }) }; setPage(key) }}><WorkspaceIcon name={key} /><span>{label}</span></button>)}</div>)}</nav>
      <div className="sidebar-foot"><span className="workspace-dot" /><span>本地服务已连接</span></div>
    </aside>
    <main className="main">
      <header className="topbar">
        <div className="breadcrumb">工作空间 <span>/</span><strong>{navigation.find(([key]) => key === page)?.[1]}</strong></div>
        <div className="toolbar">
          <label className="account-select"><span>账户</span><select value={accountId} disabled={aiActivity.running} title={aiActivity.running ? 'AI 生成结束或停止后可切换账户' : undefined} onChange={event => setAccountId(event.target.value)}>{accounts.map(row => <option key={row.id} value={row.id}>{row.name}{row.kind === 'sim' ? row.frozen ? '（模拟·恢复点）' : '（模拟）' : '（实盘）'}</option>)}</select></label>
          {ready && accountId && <button className="button ghost" disabled={aiActivity.running || saving} onClick={() => { const name = window.prompt('新实盘账户名称'); if (name?.trim()) mutate('账户已创建', async isCurrent => { const created = await api<Account>('/accounts', 'POST', { name: name.trim() }); if (!isCurrent()) return; await refreshAccounts(); if (isCurrent()) { setNotice('账户已创建'); setAccountId(created.id) } }) }}><Icon name="add" />新增实盘</button>}
          {ready && <button className="button ghost" disabled={aiActivity.running || saving} onClick={addSimAccount}><Icon name="add" />新增模拟</button>}
          {ready && <GlobalAILauncher accountId={accountId || undefined} accountKind={selectedAccount?.kind} page={page} datasetId={marketTarget} researchRunId={researchTarget} onOpen={() => setPage('ai')} />}
          {ready && accountId && <details className="export-menu"><summary className="button ghost"><Icon name="download" />导出数据</summary><div><a href={`/api/v1/accounts/${accountId}/exports/account.xlsx`} download>账户 Excel 工作簿</a>{(selectedAccount?.kind === 'sim' ? [['sim-fills', '模拟成交']] : [['trades', '实盘交易'], ['flows', '资金流水'], ['snapshots', '资产快照'], ['rounds', '交易回合']]).map(([kind, label]) => <a key={kind} href={`/api/v1/accounts/${accountId}/exports/${kind}.csv`} download>{label} CSV</a>)}</div></details>}
          <button className="button ghost" onClick={() => setThemeMode(themeMode === 'light' ? 'dark' : themeMode === 'dark' ? 'system' : 'light')} title={`当前${themeMode === 'system' ? '跟随系统' : themeMode === 'light' ? '浅色' : '深色'}主题`}><Icon name={themeMode === 'light' ? 'theme' : 'light'} />{themeMode === 'light' ? '深色' : themeMode === 'dark' ? '跟随系统' : '浅色'}</button>
          <button className="button ghost" onClick={() => setDensity(density === 'comfortable' ? 'compact' : 'comfortable')}><Icon name={density === 'comfortable' ? 'density' : 'comfortable'} />{density === 'comfortable' ? '紧凑列表' : '舒适列表'}</button>
        </div>
      </header>
      <div className="content"><React.Suspense fallback={<section className="card" role="status">正在加载页面…</section>}>
        {(!(accountId && (page === 'research' || page === 'backtest'))) && <div className="page-heading"><div className="page-heading-main"><IconTile name={page} tone={pageTone[page]} size="lg" /><div><h1>{navigation.find(([key]) => key === page)?.[1]}</h1><p>{pageDescriptions[page] || '记录每一次操作，确认数据后查看复盘结果。'}</p></div></div>
          <div className="status" data-status={selectedAccount?.kind === 'sim' ? selectedAccount.frozen ? 'recalculating' : 'fresh' : analytics?.status || 'missing'}>{selectedAccount?.kind === 'sim' ? selectedAccount.frozen ? '只读恢复点' : '模拟账户' : analytics?.status === 'fresh' ? '数据已更新' : analytics?.status === 'recalculating' ? '正在重算 · 显示上次完整结果' : analytics?.status === 'failed' ? '重算失败 · 显示上次完整结果' : analytics?.status === 'stale' ? '数据待更新' : '等待数据'}</div></div>}
        {error && <div className="alert error" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}
        {notice && <div className="alert success" role="status">{notice}</div>}
        {saving && <div className="alert" role="status">正在保存，请稍候…</div>}
        {!ready && !error && <div className="card">正在连接本地服务…</div>}
        {ready && !accountId && <section className="card empty onboarding"><h2>创建第一个账户</h2><p>开始记录交易、资金、资产和每日复盘。</p><form className="onboarding-form" aria-busy={saving} onSubmit={event => { event.preventDefault(); mutate('账户已创建', async isCurrent => { await api('/accounts', 'POST', { name: newAccount.trim() }); if (!isCurrent()) return; setNewAccount(''); setNotice('账户已创建'); await refreshAccounts() }) }}><fieldset disabled={saving} style={{ display: 'contents' }}><label className="field">账户名称<input placeholder="例如：主账户" value={newAccount} onChange={event => setNewAccount(event.target.value)} maxLength={80} required /></label><div className="form-actions"><button className="button primary" disabled={saving || !newAccount.trim()}><Icon name="add" />创建实盘账户</button><button type="button" className="button secondary" onClick={addSimAccount}><Icon name="add" />创建模拟账户</button></div></fieldset></form></section>}
        {ready && accountId && selectedAccount?.kind === 'sim' && page === 'simulation' && <SimulationEditor key={`${accountId}-${selectedAccount.frozen}`} accountId={accountId} accountName={selectedAccount.name} frozen={Boolean(selectedAccount.frozen)} onAccountSwitch={async id => { await refreshAccounts(); setAccountId(id); setPage('simulation') }} />}
        {ready && accountId && page === 'market' && <MarketEditor key={accountId} accountId={accountId} accountKind={selectedAccount?.kind || ''} trades={selectedAccount?.kind === 'real' ? trades : []} initialDatasetId={marketTarget} onDatasetChange={setMarketTarget} />}
        {ready && accountId && page === 'valuation' && <ValuationEditor />}
        {ready && accountId && page === 'share' && <ShareCardEditor />}
        {ready && accountId && (page === 'research' || page === 'backtest') && <ResearchWorkspace key={accountId} route={researchRoute} onNavigate={navigateResearch} accountId={accountId} initialRunId={researchTarget} simAccounts={accounts.filter(row => row.kind === 'sim' && !row.frozen)} onOpenSim={id => { setAccountId(id); setPage('simulation') }} onOpenMarket={openMarket} />}
        {ready && page === 'news' && <MarketNewsEditor />}
        {ready && page === 'signals' && <SignalWorkspace onOpenMarket={openMarket} onOpenResearch={id => navigateResearch({ view: 'single', runId: id })} />}
        {ready && page === 'events' && <EventStoreWorkspace onOpenMarket={openMarket} />}
        {ready && page === 'settings' && <SettingsWorkspace accountId={accountId || undefined} accountKind={selectedAccount?.kind} themeMode={themeMode} density={density} onThemeChange={setThemeMode} onDensityChange={setDensity} onImported={id => { refreshAccounts().then(() => { if (aiActivityRef.current.running) { setPage('ai'); setNotice('新账户已保存；AI 生成结束后可从账户列表选择。') } else { setAccountId(id); setPage('overview') } }).catch(err => setError(err.message)) }} />}
        {ready && page === 'tasks' && <TaskCenter accountId={accountId || undefined} onOpen={(nextPage, id, kind) => { const url = new URL(window.location.href); const targetPage = nextPage === 'overview' && selectedAccount?.kind === 'sim' ? 'simulation' : nextPage; url.searchParams.set('page', targetPage); if (targetPage === 'backtest' || targetPage === 'research') { url.searchParams.delete('run'); url.searchParams.delete('research'); url.searchParams.delete('research-mode'); url.searchParams.set('task', `${kind}:${id}`); navigateResearch(readResearchNavigation(url.search, targetPage)) } else setPage(targetPage) }} />}
        {ready && <div hidden={page !== 'ai'}><AIWorkspace accountId={accountId || undefined} accountKind={selectedAccount?.kind} onActivityChange={updateAIActivity} /></div>}
        {ready && accountId && page === 'insights' && <InspirationEditor />}
        {ready && accountId && selectedAccount?.kind === 'real' && page !== 'market' && page !== 'research' && page !== 'backtest' && page !== 'insights' && page !== 'ai' && page !== 'tasks' && <>
          {page === 'overview' && <>
            <div className="metrics"><Metric icon="asset" tone="blue" label="最新资产" value={current?.assets ? `¥ ${current.assets}` : '—'} note={current?.date || '待确认'} /><Metric icon="nav" tone="green" label="单位净值" value={current?.nav ? Number(current.nav).toFixed(4) : '—'} note={current?.quality === 'confirmed' ? '资产快照已确认' : '按历史数据结转'} /><Metric icon="target" tone="amber" label="已点亮节点" value={String(current?.lit_count ?? 0)} note={`每级 × ${analytics?.result?.nav.target_config?.multiplier ?? '1.30'}`} /><Metric icon="count" tone="violet" label="交易笔数" value={String(analytics?.result?.trade_stats.trade_count ?? 0)} note={`已完成 ${analytics?.result?.trade_stats.closed_rounds ?? 0} 轮`} /></div>
            <div className="quick-actions"><button className="button primary" onClick={() => setPage('review')}><Icon name="review" />填写每日复盘</button><button className="button secondary" onClick={() => setPage('trades')}><Icon name="trades" />录入交易</button><button className="button secondary" onClick={() => setPage('snapshots')}><Icon name="snapshots" />确认资产</button><button className="button secondary" onClick={() => setPage('statistics')}><Icon name="statistics" />查看统计分析与交易回合</button></div>
            <div className="overview-grid"><div className="overview-main">
            <section className="card"><div className="section-heading"><div><h2 className="title-with-icon"><Icon name="chart" />净值走势</h2><p>资金进出按份额调整，资产快照确认当日净值。</p></div></div>{points.length ? <React.Suspense fallback={<div className="empty-chart">正在加载图表…</div>}><NavChart option={chart} /></React.Suspense> : <div className="empty-chart">先录入初始资金和资产快照，即可看到净值走势</div>}</section>
              <section className="card"><h2 className="title-with-icon"><Icon name="holdings" />当前持仓</h2>{analytics?.result?.positions.length ? <table><thead><tr><th>代码</th><th>数量</th><th>成本</th></tr></thead><tbody>{analytics.result.positions.map(row => <tr key={row.symbol}><td>{row.symbol}</td><td>{row.quantity}</td><td>{row.cost_basis}</td></tr>)}</tbody></table> : <p className="muted">暂无持仓</p>}</section>
            <TargetNodes accountId={accountId} nav={analytics?.result?.nav ?? null} onChanged={() => refresh(accountId)} />
            </div><div className="overview-side">
            <DailyInspiration />
            <ReviewReminders accountId={accountId} revision={selectedAccount.input_revision} onReview={day => { setReviewDate(day); setPage('review') }} onSnapshot={openSnapshot} />
              <section className="card"><h2 className="title-with-icon"><Icon name="data" />数据状态</h2><p>输入版本：{analytics?.account_input_revision ?? '—'} · 结果版本：{analytics?.projection_input_revision ?? '—'}</p><p>状态：{analytics?.status ?? '—'}</p>{analytics?.error && <p className="danger">{analytics.error}</p>}<div className="form-actions"><button className="button secondary" disabled={saving} onClick={() => mutate('已提交重算', () => api(base + '/analytics/retry', 'POST', {}))}><Icon name="refresh" />重新计算</button><a className="button secondary" href="/api/v1/backups/export" download="trade-backup.zip"><Icon name="download" />下载备份</a></div>{analytics?.result?.anomalies.length ? <p className="danger">发现 {analytics.result.anomalies.length} 条超卖交易，请核对流水。</p> : null}</section>
            </div></div>
          </>}
          {page === 'statistics' && <><PerformanceSummary key={`${accountId}:${analytics?.projection_input_revision ?? 0}`} accountId={accountId} onOpenRounds={() => document.getElementById('trade-rounds')?.scrollIntoView({ behavior: 'smooth' })} /><TradeRounds accountId={accountId} result={analytics?.result ?? null} trades={trades} onOpenTrade={() => setPage('trades')} /></>}
          {page === 'trades' && <div className="two-col wide-left"><PendingTradesEditor key={`${accountId}:${pendingRevision}`} accountId={accountId} onConfirmed={() => refresh(accountId)} /><section className="card"><div className="section-heading"><h2 className="title-with-icon"><Icon name="trades" />交易记录</h2><span className="muted">共 {trades.length} 笔</span></div><div className="table-wrap"><table><thead><tr><th>日期</th><th>方向</th><th>代码 / 名称</th><th>数量</th><th>价格</th><th>费用</th><th>操作</th></tr></thead><tbody>{trades.map(row => <tr key={row.id}><td>{row.trade_date}</td><td><span className={row.side === 'buy' ? 'tag buy' : 'tag sell'}>{row.side === 'buy' ? '买入' : '卖出'}</span></td><td><strong>{row.symbol}</strong><br /><span className="muted">{row.name}</span></td><td>{row.quantity}</td><td>{row.price}</td><td>{row.fee}<br /><span className="muted">{row.fee_source === 'auto' ? '自动' : '实付'} · 计算 {row.calculated_fee}</span></td><td><button className="link-button" disabled={saving} onClick={() => { setEditingTrade(row); setStageTrade(false); setTradeForm({ trade_date: row.trade_date, symbol: row.symbol, name: row.name, side: row.side, quantity: row.quantity, price: row.price, fee: row.fee, fee_mode: row.fee_source, note: row.note }) }}>编辑</button><button className="link-button danger" disabled={saving} onClick={() => { if (confirm('确认作废这笔交易？')) mutate('交易已作废', () => api(base + `/trades/${row.id}?expected_revision=${row.revision}`, 'DELETE')) }}>作废</button></td></tr>)}</tbody></table></div>{!trades.length && <p className="muted">暂无交易记录</p>}</section>
            <section className="card"><h2 className="title-with-icon"><Icon name={editingTrade ? 'review' : 'add'} />{editingTrade ? '编辑交易' : '新增交易'}</h2><form className="form" aria-busy={saving} onSubmit={event => { event.preventDefault(); mutate(editingTrade ? '交易已更新' : stageTrade ? '已加入待确认交易' : '交易已保存', async isCurrent => { await api(base + (editingTrade ? `/trades/${editingTrade.id}` : stageTrade ? '/pending-trades' : '/trades'), editingTrade ? 'PUT' : 'POST', { ...tradeForm, expected_revision: editingTrade?.revision }); if (!isCurrent()) return; if (stageTrade && !editingTrade) setPendingRevision(value => value + 1); setEditingTrade(null); setTradeForm({ ...initialTrade, trade_date: today() }) }) }}><fieldset disabled={saving} style={{ display: 'contents' }}>
              <Field label="交易日期"><input type="date" value={tradeForm.trade_date} onChange={event => setTradeForm({ ...tradeForm, trade_date: event.target.value })} required /></Field><Field label="方向"><select value={tradeForm.side} onChange={event => setTradeForm({ ...tradeForm, side: event.target.value as 'buy' | 'sell' })}><option value="buy">买入</option><option value="sell">卖出</option></select></Field><Field label="证券代码"><input value={tradeForm.symbol} onChange={event => setTradeForm({ ...tradeForm, symbol: event.target.value })} required /></Field><Field label="名称"><input value={tradeForm.name} onChange={event => setTradeForm({ ...tradeForm, name: event.target.value })} /></Field><div className="form-grid"><Field label="数量"><input type="number" min="1" value={tradeForm.quantity} onChange={event => setTradeForm({ ...tradeForm, quantity: Number(event.target.value) })} required /></Field><Field label="价格"><input type="number" step="0.0001" min="0.0001" value={tradeForm.price} onChange={event => setTradeForm({ ...tradeForm, price: event.target.value })} required /></Field></div><Field label="费用方式"><select value={tradeForm.fee_mode} onChange={event => setTradeForm({ ...tradeForm, fee_mode: event.target.value as 'auto' | 'manual' })}><option value="auto">按费率自动计算</option><option value="manual">手动实付覆盖</option></select></Field>{tradeForm.fee_mode === 'manual' && <Field label="实付费用"><input type="number" step="0.01" min="0" value={tradeForm.fee} onChange={event => setTradeForm({ ...tradeForm, fee: event.target.value })} /></Field>}<Field label="备注"><textarea value={tradeForm.note} onChange={event => setTradeForm({ ...tradeForm, note: event.target.value })} /></Field>{!editingTrade && <label className="check-field"><input type="checkbox" checked={stageTrade} onChange={event => setStageTrade(event.target.checked)} />先加入待确认交易，核对后再计入账本</label>}<div className="form-actions"><button className="button primary">{editingTrade ? '保存修改' : stageTrade ? '加入待确认' : '新增交易'}</button>{editingTrade && <button type="button" className="button ghost" onClick={() => { setEditingTrade(null); setTradeForm({ ...initialTrade, trade_date: today() }) }}><Icon name="close" />取消</button>}</div></fieldset></form></section><RealFeeEditor accountId={accountId} trade={tradeForm} /></div>}
          {page === 'flows' && <div className="two-col wide-left"><section className="card"><h2 className="title-with-icon"><Icon name="flows" />资金流水</h2><div className="table-wrap"><table><thead><tr><th>日期</th><th>类型</th><th>金额</th><th>备注</th><th>操作</th></tr></thead><tbody>{flows.map(row => <tr key={row.id}><td>{row.flow_date}</td><td>{({ initial: '初始资金', deposit: '入金', withdraw: '出金' } as Record<string, string>)[row.kind]}</td><td>¥ {row.amount}</td><td>{row.note || '—'}</td><td><button className="link-button danger" disabled={saving} onClick={() => { if (confirm('确认作废这条资金流水？')) mutate('流水已作废', () => api(base + `/cash-flows/${row.id}?expected_revision=${row.revision}`, 'DELETE')) }}>作废</button></td></tr>)}</tbody></table></div>{!flows.length && <p className="muted">请先录入初始资金</p>}</section><section className="card"><h2 className="title-with-icon"><Icon name="add" />新增资金流水</h2><form className="form" aria-busy={saving} onSubmit={event => { event.preventDefault(); mutate('资金流水已保存', async isCurrent => { await api(base + '/cash-flows', 'POST', flowForm); if (isCurrent()) setFlowForm({ ...initialFlow, flow_date: today() }) }) }}><fieldset disabled={saving} style={{ display: 'contents' }}><Field label="日期"><input type="date" value={flowForm.flow_date} onChange={event => setFlowForm({ ...flowForm, flow_date: event.target.value })} required /></Field><Field label="类型"><select value={flowForm.kind} onChange={event => setFlowForm({ ...flowForm, kind: event.target.value as typeof flowForm.kind })}><option value="initial">初始资金</option><option value="deposit">入金</option><option value="withdraw">出金</option></select></Field><Field label="金额"><input type="number" step="0.01" min="0.01" value={flowForm.amount} onChange={event => setFlowForm({ ...flowForm, amount: event.target.value })} required /></Field><Field label="备注"><textarea value={flowForm.note} onChange={event => setFlowForm({ ...flowForm, note: event.target.value })} /></Field><button className="button primary"><Icon name="save" />保存流水</button></fieldset></form></section></div>}
          {page === 'snapshots' && <div className="two-col wide-left">
            <section className="card"><h2 className="title-with-icon"><Icon name="snapshots" />资产快照</h2><div className="table-wrap"><table><thead><tr><th>日期</th><th>总资产</th><th>可用现金</th><th>持仓市值</th><th>明细</th><th>操作</th></tr></thead><tbody>{snapshots.map(row => <tr key={row.id}><td>{row.snap_date}</td><td>¥ {row.total_assets}</td><td>{row.available_cash ?? '—'}</td><td>{row.position_value ?? '—'}</td><td>{row.positions?.length ?? 0} 项</td><td><button className="link-button" disabled={saving} onClick={() => { setSnapshotDate(row.snap_date); setSnapshotTotal(row.total_assets); setSnapshotCash(row.available_cash ?? ''); setSnapshotPosition(row.position_value ?? ''); setSnapshotPositions(row.positions ?? []) }}>编辑</button></td></tr>)}</tbody></table></div>{!snapshots.length && <p className="muted">暂无资产快照</p>}</section>
            <section className="card"><h2 className="title-with-icon"><Icon name="check" />确认资产</h2><p className="muted">同一日期再次保存会更新该快照，历史修订保留在审计记录中。</p><form className="form" aria-busy={saving} onSubmit={event => { event.preventDefault(); mutate('资产快照已保存', () => api(base + `/snapshots/${snapshotDate}`, 'PUT', { snap_date: snapshotDate, total_assets: snapshotTotal, available_cash: snapshotCash || null, position_value: snapshotPosition || null, positions: snapshotPositions, expected_revision: snapshots.find(row => row.snap_date === snapshotDate)?.revision ?? 0 })) }}><fieldset disabled={saving} style={{ display: 'contents' }}>
              <Field label="日期"><input type="date" value={snapshotDate} onChange={event => { setSnapshotDate(event.target.value); const row = snapshots.find(item => item.snap_date === event.target.value); setSnapshotTotal(row?.total_assets ?? ''); setSnapshotCash(row?.available_cash ?? ''); setSnapshotPosition(row?.position_value ?? ''); setSnapshotPositions(row?.positions ?? []) }} required /></Field>
              <Field label="总资产"><input type="number" step="0.01" min="0" value={snapshotTotal} onChange={event => setSnapshotTotal(event.target.value)} required /></Field><Field label="可用现金（可选）"><input type="number" step="0.01" min="0" value={snapshotCash} onChange={event => setSnapshotCash(event.target.value)} /></Field><Field label="持仓市值（可选）"><input type="number" step="0.01" min="0" value={snapshotPosition} onChange={event => setSnapshotPosition(event.target.value)} /></Field>
              <div className="section-heading"><h3>持仓明细</h3><button type="button" className="button secondary" onClick={() => setSnapshotPositions(rows => [...rows, { symbol: '', name: '', quantity: 100, market_value: '' }])}><Icon name="add" />添加持仓</button></div>
              {snapshotPositions.map((row, index) => <div className="snapshot-position-row" key={index}><label className="field">代码<input aria-label={`持仓${index + 1}代码`} value={row.symbol} onChange={event => setSnapshotPositions(rows => rows.map((item, i) => i === index ? { ...item, symbol: event.target.value } : item))} required /></label><label className="field">名称<input aria-label={`持仓${index + 1}名称`} value={row.name} onChange={event => setSnapshotPositions(rows => rows.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} /></label><label className="field">数量<input aria-label={`持仓${index + 1}数量`} type="number" min="1" value={row.quantity} onChange={event => setSnapshotPositions(rows => rows.map((item, i) => i === index ? { ...item, quantity: Number(event.target.value) } : item))} required /></label><label className="field">市值<input aria-label={`持仓${index + 1}市值`} type="number" min="0" step="0.01" value={row.market_value} onChange={event => setSnapshotPositions(rows => rows.map((item, i) => i === index ? { ...item, market_value: event.target.value } : item))} required /></label><button type="button" className="link-button danger" onClick={() => setSnapshotPositions(rows => rows.filter((_, i) => i !== index))}>移除</button></div>)}
              {snapshotPositions.length > 0 && <p className="muted">明细市值合计 ¥ {snapshotPositions.reduce((sum, row) => sum + (Number(row.market_value) || 0), 0).toFixed(2)}；{snapshotPosition ? `与填写的持仓市值差 ¥ ${(snapshotPositions.reduce((sum, row) => sum + (Number(row.market_value) || 0), 0) - Number(snapshotPosition)).toFixed(2)}` : '未填持仓市值时按明细合计计算'}</p>}
              {snapshotCash && (snapshotPosition || snapshotPositions.length > 0) && snapshotTotal && <p className={Math.abs(Number(snapshotCash) + Number(snapshotPosition || snapshotPositions.reduce((sum, row) => sum + (Number(row.market_value) || 0), 0)) - Number(snapshotTotal)) > 0.01 ? 'danger' : 'muted'}>现金与持仓合计距总资产差 ¥ {(Number(snapshotCash) + Number(snapshotPosition || snapshotPositions.reduce((sum, row) => sum + (Number(row.market_value) || 0), 0)) - Number(snapshotTotal)).toFixed(2)}</p>}
              <button className="button primary"><Icon name="save" />保存快照</button></fieldset></form></section>
            <AssetEstimate accountId={accountId} day={snapshotDate} symbols={trades.map(row => row.symbol)} onApply={estimate => { setSnapshotTotal(estimate.total_assets); setSnapshotCash(estimate.cash); setSnapshotPosition(estimate.position_value); setSnapshotPositions(estimate.positions) }} />
          </div>}
          {page === 'review' && <><DailyReviewEditor key={`${accountId}:${reviewDate}`} accountId={accountId} initialDate={reviewDate} /><ReviewReminders accountId={accountId} showReviews={false} onSnapshot={openSnapshot} /></>}
          {page === 'period' && <PeriodEditor accountId={accountId} />}
        </>}
      </React.Suspense></div>
    </main>
  </div>
}

function Metric({ label, value, note, icon, tone }: { label: string; value: string; note: string; icon?: IconName; tone?: IconTone }) { return <div className="metric card">{icon && <IconTile name={icon} tone={tone} size="sm" />}<span>{label}</span><strong>{value}</strong><small>{note}</small></div> }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="field"><span>{label}</span>{children}</label> }

const root = document.getElementById('root')
if (root) createRoot(root).render(<React.StrictMode>{new URLSearchParams(location.search).get('print') === 'review' ? <PrintPreview /> : <App />}</React.StrictMode>)
