import { lazy, Suspense, useEffect, useState, type ComponentProps } from 'react'
import { api } from './api'
import { ResearchEditor, type Strategy } from './research'
import { navigateResearch, readResearchNavigation, researchNavigationUrl, researchPages, type ResearchNavigation } from './research-navigation'
import './research-workspace.css'
import { Icon } from './workspace-icons'

const ScreenerEditor = lazy(() => import('./screener').then(m => ({ default: m.ScreenerEditor })))
const StrategyScanEditor = lazy(() => import('./strategy-scans').then(m => ({ default: m.StrategyScanEditor })))
const SignalBasketEditor = lazy(() => import('./signal-baskets').then(m => ({ default: m.SignalBasketEditor })))
const EventProfileEditor = lazy(() => import('./event-profiles').then(m => ({ default: m.EventProfileEditor })))
const BacktestEditor = lazy(() => import('./backtest').then(m => ({ default: m.BacktestEditor })))
const PortfolioWorkspace = lazy(() => import('./portfolio-workspace').then(m => ({ default: m.PortfolioWorkspace })))
const PlateauWorkspace = lazy(() => import('./plateau-workspace').then(m => ({ default: m.PlateauWorkspace })))
const WalkForwardWorkspace = lazy(() => import('./walk-forward-workspace').then(m => ({ default: m.WalkForwardWorkspace })))
const PortfolioExperimentWorkspace = lazy(() => import('./portfolio-experiment-workspace').then(m => ({ default: m.PortfolioExperimentWorkspace })))
const PortfolioWalkForwardWorkspace = lazy(() => import('./portfolio-walk-forward-workspace').then(m => ({ default: m.PortfolioWalkForwardWorkspace })))
const PortfolioAnalysisWorkspace = lazy(() => import('./portfolio-analysis-workspace').then(m => ({ default: m.PortfolioAnalysisWorkspace })))
const ReportLibrary = lazy(() => import('./report-library').then(m => ({ default: m.ReportLibrary })))
const PortfolioReportLibrary = lazy(() => import('./portfolio-report-library').then(m => ({ default: m.PortfolioReportLibrary })))
const LegacyReportLibrary = lazy(() => import('./legacy-report-library').then(m => ({ default: m.LegacyReportLibrary })))
type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
type Props = ComponentProps<typeof ResearchEditor> & { route?: ResearchNavigation; onNavigate?: (next: ResearchNavigation) => void }
const modes = {
  experiments: [{ mode: 'single', title: '单股收益平原' }, { mode: 'portfolio', title: '组合收益平原' }, { mode: 'single-wf', title: '单股 Walk-forward' }, { mode: 'portfolio-wf', title: '组合 Walk-forward' }],
  reports: [{ mode: 'single', title: '单股报告' }, { mode: 'portfolio', title: '组合报告' }, { mode: 'legacy', title: '旧版报告' }],
} as const
const pageKey = (route: ResearchNavigation) => `${route.view}:${route.view === 'experiments' || route.view === 'reports' ? route.mode || 'single' : ''}`
const pathLabels: Record<string, string> = { single_observation: '单股信号', single_backtest: '单股回测', traditional_portfolio: '传统组合', full_signal_context: '完整上下文', b1_scanner: 'B1 扫描', matrix_pool: '矩阵冻结池', aligned_event_portfolio: '事件组合' }

function StrategyDirectory({ strategies, navigate }: { strategies: Strategy[]; navigate: (next: ResearchNavigation) => void }) {
  const [query, setQuery] = useState('')
  const [family, setFamily] = useState('')
  const families = [...new Set(strategies.map(strategy => strategy.family_name || '其他策略'))]
  const visible = strategies.filter(strategy => (!family || (strategy.family_name || '其他策略') === family) && `${strategy.name} ${strategy.id} ${strategy.description}`.toLowerCase().includes(query.toLowerCase()))
  return <>
    <div className="research-start-grid">
      {(['screener', 'single', 'portfolio', 'reports'] as const).map(view => { const item = researchPages.find(page => page.view === view)!; return <button className="research-start-card" key={view} onClick={() => navigate({ view })}><strong>{item.title}</strong><span>{item.description}</span><span className="research-card-arrow" aria-hidden="true">→</span></button> })}
    </div>
    <section className="card"><div className="section-header"><div><h2 className="title-with-icon"><Icon name="research" />可用策略</h2><p className="muted">先选择研究任务，再查看策略适用路径和边界。启用与默认策略在系统设置管理。</p></div><span className="research-count">{visible.length} / {strategies.length} 项</span></div>
      <div className="research-directory-filter"><label className="field"><span>搜索策略</span><input type="search" placeholder="名称、策略编号或说明" value={query} onChange={event => setQuery(event.target.value)} /></label><label className="field"><span>策略系列</span><select value={family} onChange={event => setFamily(event.target.value)}><option value="">全部系列</option>{families.map(value => <option key={value}>{value}</option>)}</select></label></div>
      <div className="research-strategy-list">{visible.map(strategy => <article key={strategy.id} className="research-strategy-row"><div><div className="research-strategy-title"><h3>{strategy.name}</h3><span className="research-count">{strategy.enabled_in_rebuild === false ? '已停用' : strategy.availability === 'unavailable' ? '尚不可运行' : '可用'}{strategy.default_in_rebuild ? ' · 默认' : ''}</span></div><p>{strategy.description}</p><p className="muted">{strategy.family_name || '策略'} · {strategy.version} · {strategy.id}</p><div className="research-tags">{(strategy.execution_paths || []).map(path => <span key={path}>{pathLabels[path] || path}</span>)}</div><details><summary>适用范围与执行规则</summary>{strategy.execution_semantics && <dl>{Object.entries(strategy.execution_semantics).map(([key, value]) => <div key={key}><dt>{{ entry: '入场', exit: '退出', ranking: '排序' }[key] || key}</dt><dd>{value}</dd></div>)}</dl>}{strategy.limitations.length ? <ul>{strategy.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="muted">以实际运行记录的参数与数据范围为准。</p>}</details></div><button className="button secondary" disabled={strategy.enabled_in_rebuild === false || strategy.availability === 'unavailable'} onClick={() => navigate({ view: strategy.signal_params !== null ? 'single' : 'screener', strategyId: strategy.id })}>打开{strategy.signal_params !== null ? '单股研究' : '筛选入口'}</button></article>)}</div>{!visible.length && <p className="muted">没有符合条件的策略。</p>}
    </section>
  </>
}

export function ResearchWorkspace({ route, onNavigate, initialRunId, ...props }: Props) {
  const [localRoute, setLocalRoute] = useState<ResearchNavigation>(() => initialRunId ? { view: 'single', runId: initialRunId } : readResearchNavigation(window.location.search))
  const current = route || localRoute
  const currentKey = pageKey(current)
  const [visited, setVisited] = useState<Record<string, ResearchNavigation>>(() => ({ [currentKey]: current }))
  // Retain each visited workspace's form while changing only the visible page.
  // A destination's source changes only when navigation explicitly provides a new source.
  const saved = visited[currentKey]
  if (!saved || JSON.stringify(saved) !== JSON.stringify(current)) setVisited({ ...visited, [currentKey]: current })
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [error, setError] = useState('')
  useEffect(() => { const changed = () => setLocalRoute(readResearchNavigation(window.location.search)); window.addEventListener('popstate', changed); return () => window.removeEventListener('popstate', changed) }, [])
  useEffect(() => { let alive = true; Promise.all([api<Dataset[]>('/market/datasets'), api<Strategy[]>('/research/strategies')]).then(([ds, ss]) => { if (alive) { setDatasets(ds); setStrategies(ss) } }).catch(err => { if (alive) setError(err.message) }); return () => { alive = false } }, [])
  const navigate = (next: ResearchNavigation) => { if (onNavigate) onNavigate(next); else navigateResearch(next) }
  const promoted = (runId: string) => navigate({ view: 'single', runId })
  const currentPage = researchPages.find(page => page.view === current.view) || researchPages[0]
  function renderPage(item: ResearchNavigation) {
    switch (item.view) {
      case 'catalog': return <StrategyDirectory strategies={strategies} navigate={navigate} />
      case 'screener': return <ScreenerEditor preferredStrategy={item.strategyId} datasets={datasets} onOpenMarket={props.onOpenMarket} onPromoted={promoted} />
      case 'scan': return <StrategyScanEditor datasets={datasets} strategies={strategies} onOpenMarket={props.onOpenMarket} onPromoted={promoted} />
      case 'compare': return <SignalBasketEditor datasets={datasets} onOpenMarket={props.onOpenMarket} onOpenResearch={promoted} />
      case 'profiles': return <EventProfileEditor />
      case 'single': return <ResearchEditor {...props} initialRunId={item.runId} initialStrategyId={item.strategyId} onRunSelected={id => navigate({ view: 'single', runId: id })} onNavigate={navigate} />
      case 'backtest': return <BacktestEditor accountId={props.accountId} onNavigate={navigate} />
      case 'portfolio': return <PortfolioWorkspace accountId={props.accountId} onNavigate={navigate} />
      case 'analysis': return <PortfolioAnalysisWorkspace sourceRunId={item.sourceId} />
      case 'experiments':
        if (item.mode === 'portfolio') return <PortfolioExperimentWorkspace sourceRunId={item.sourceId} />
        if (item.mode === 'portfolio-wf') return <PortfolioWalkForwardWorkspace sourceRunId={item.sourceId} />
        if (item.mode === 'single-wf') return <WalkForwardWorkspace baseRunId={item.sourceId} />
        return <PlateauWorkspace baseRunId={item.sourceId} />
      case 'reports':
        if (item.mode === 'portfolio') return <PortfolioReportLibrary sourceRunId={item.sourceId} />
        if (item.mode === 'legacy') return <LegacyReportLibrary />
        return <ReportLibrary sourceRunId={item.sourceId} />
    }
  }
  return <div className="research-workspace">
    <aside className="research-sidebar"><div className="research-sidebar-title">研究工作台</div><nav aria-label="研究子页面">{['准备与发现', '研究与验证', '归档'].map(group => <div className="research-nav-group" key={group}><p>{group}</p>{researchPages.filter(page => page.group === group).map(page => <a key={page.view} href={researchNavigationUrl(window.location.href, { view: page.view })} aria-current={current.view === page.view ? 'page' : undefined} onClick={event => { if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate({ view: page.view }) }}>{page.title}<span aria-hidden="true">›</span></a>)}</div>)}</nav><p className="research-session-note">子页切换保留本次表单。刷新或离开研究工作台后，未保存配置可能清空。</p></aside>
    <div className="research-content"><header className="research-page-header"><div><p className="research-eyebrow">策略研究 / {currentPage.group}</p><h1>{currentPage.title}</h1><p className="muted">{currentPage.description}</p></div></header>
      {error && <p className="alert error" role="alert">{error}</p>}
      {(current.view === 'experiments' || current.view === 'reports') && <nav className="research-mode-nav" aria-label={`${currentPage.title}类型`}>{modes[current.view].map(item => <a key={item.mode} href={researchNavigationUrl(window.location.href, { view: current.view, mode: item.mode })} aria-current={(current.mode || 'single') === item.mode ? 'page' : undefined} onClick={event => { if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate({ view: current.view, mode: item.mode }) }}>{item.title}</a>)}</nav>}
      {Object.entries(visited).map(([key, item]) => <div key={key} className="research-page" hidden={key !== currentKey} aria-label={`${researchPages.find(page => page.view === item.view)?.title}工作区`}><Suspense fallback={<p className="muted" role="status">正在打开工作区…</p>}>{renderPage(item)}</Suspense></div>)}
    </div>
  </div>
}
