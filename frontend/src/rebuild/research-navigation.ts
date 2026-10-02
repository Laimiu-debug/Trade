export type ResearchView = 'catalog' | 'screener' | 'scan' | 'compare' | 'single' | 'profiles' | 'backtest' | 'portfolio' | 'experiments' | 'analysis' | 'reports'
export type ResearchMode = 'single' | 'portfolio' | 'single-wf' | 'portfolio-wf' | 'legacy'
export type ResearchNavigation = { view: ResearchView; mode?: ResearchMode; sourceId?: string; runId?: string; task?: string; strategyId?: string }

export const researchPages: Array<{ view: ResearchView; title: string; description: string; group: string }> = [
  { view: 'catalog', title: '策略目录', description: '选择研究入口，查看已启用策略及适用范围。', group: '准备与发现' },
  { view: 'screener', title: '选股筛选', description: '四步漏斗、B1 多周期与矩阵冻结池筛选。', group: '准备与发现' },
  { view: 'scan', title: '区间扫描', description: '按策略和日期扫描冻结样本，复核历史信号。', group: '准备与发现' },
  { view: 'compare', title: '策略交叉验证', description: '比较信号篮子，核对交集、差异与来源。', group: '准备与发现' },
  { view: 'profiles', title: '事件模板', description: '管理维科夫事件参数与冻结修订。', group: '准备与发现' },
  { view: 'single', title: '单股研究', description: '在指定决策时点判断信号，查看证据与模拟草稿。', group: '研究与验证' },
  { view: 'backtest', title: '单股回测', description: '创建单股回测，跟踪任务并复核成交。', group: '研究与验证' },
  { view: 'portfolio', title: '组合回测', description: '按步骤配置固定研究样本与组合执行规则。', group: '研究与验证' },
  { view: 'experiments', title: '参数实验', description: '收益平原、参数敏感性与 Walk-forward 验证。', group: '研究与验证' },
  { view: 'analysis', title: '组合分析与计划', description: '组合风险分析、每日持仓和条件计划。', group: '研究与验证' },
  { view: 'reports', title: '报告库', description: '归档、导入和导出单股、组合及旧版冻结报告。', group: '归档' },
]

const taskPages: Record<string, Pick<ResearchNavigation, 'view' | 'mode'>> = {
  backtest: { view: 'backtest' }, portfolio: { view: 'portfolio' },
  plateau: { view: 'experiments', mode: 'single' }, walk_forward: { view: 'experiments', mode: 'single-wf' },
  portfolio_experiment: { view: 'experiments', mode: 'portfolio' }, portfolio_walk_forward: { view: 'experiments', mode: 'portfolio-wf' },
  portfolio_analysis: { view: 'analysis' }, scan: { view: 'scan' }, strategy_scan: { view: 'scan' }, universe: { view: 'screener' },
}
const idPattern = /^(?:[a-f0-9]{32}|[a-f0-9]{64})$/

export function readResearchNavigation(search: string, legacyPage?: string): ResearchNavigation {
  const params = new URLSearchParams(search)
  const task = params.get('task') || ''
  const match = /^([a-z_]+):((?:[a-f0-9]{32}|[a-f0-9]{64}))$/.exec(task)
  if (match && taskPages[match[1]]) return { ...taskPages[match[1]], task }
  const runId = params.get('run') || ''
  if (idPattern.test(runId)) return { view: 'single', runId }
  const requested = params.get('research')
  const view = researchPages.find(page => page.view === requested)?.view || ((legacyPage || params.get('page')) === 'backtest' ? 'backtest' : 'catalog')
  const mode = params.get('research-mode') as ResearchMode | null
  const allowed = view === 'experiments' ? ['single', 'portfolio', 'single-wf', 'portfolio-wf'] : view === 'reports' ? ['single', 'portfolio', 'legacy'] : []
  const sourceId = params.get('research-source') || ''
  const strategyId = params.get('strategy') || ''
  return { view, ...(mode && allowed.includes(mode) ? { mode } : {}), ...(idPattern.test(sourceId) ? { sourceId } : {}), ...(/^[a-z0-9_]{1,80}$/.test(strategyId) && ['single', 'screener'].includes(view) ? { strategyId } : {}) }
}

export function researchNavigationUrl(currentHref: string, next: ResearchNavigation): string {
  const url = new URL(currentHref, window.location.origin)
  for (const name of ['research-mode', 'research-source', 'run', 'task', 'strategy']) url.searchParams.delete(name)
  url.searchParams.set('page', 'research')
  url.searchParams.set('research', next.view)
  if (next.mode) url.searchParams.set('research-mode', next.mode)
  if (next.sourceId) url.searchParams.set('research-source', next.sourceId)
  if (next.runId) url.searchParams.set('run', next.runId)
  if (next.task) url.searchParams.set('task', next.task)
  if (next.strategyId) url.searchParams.set('strategy', next.strategyId)
  return url.pathname + url.search + url.hash
}

export function navigateResearch(next: ResearchNavigation) {
  window.history.pushState(null, '', researchNavigationUrl(window.location.href, next))
  window.dispatchEvent(new PopStateEvent('popstate'))
}
