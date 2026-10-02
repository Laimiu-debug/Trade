import { describe, expect, it } from 'vitest'
import { readResearchNavigation, researchNavigationUrl } from './research-navigation'

const id = 'a'.repeat(32)
describe('research route compatibility', () => {
  it('opens the directory by default and preserves the historical backtest alias', () => {
    expect(readResearchNavigation('?page=research')).toEqual({ view: 'catalog' })
    expect(readResearchNavigation('?page=backtest')).toEqual({ view: 'backtest' })
    expect(readResearchNavigation(`?page=research&run=${id}`)).toEqual({ view: 'single', runId: id })
  })
  it.each([
    ['portfolio', 'portfolio', undefined], ['backtest', 'backtest', undefined],
    ['plateau', 'experiments', 'single'], ['walk_forward', 'experiments', 'single-wf'],
    ['portfolio_experiment', 'experiments', 'portfolio'], ['portfolio_walk_forward', 'experiments', 'portfolio-wf'],
    ['portfolio_analysis', 'analysis', undefined], ['scan', 'scan', undefined],
  ])('routes %s tasks to their own workspace, even with an old research query', (kind, view, mode) => {
    expect(readResearchNavigation(`?research=catalog&task=${kind}:${id}`)).toEqual({ view, ...(mode ? { mode } : {}), task: `${kind}:${id}` })
  })
  it('keeps the account and unrelated parameters and clears previous artifacts', () => {
    const next = researchNavigationUrl(`http://localhost/app?page=backtest&account=acc&task=portfolio:${id}&run=${id}&research-source=${id}&strategy=old&extra=keep#top`, { view: 'reports', mode: 'portfolio', sourceId: id })
    const url = new URL(next, 'http://localhost')
    expect(url.searchParams.get('account')).toBe('acc')
    expect(url.searchParams.get('extra')).toBe('keep')
    expect(url.hash).toBe('#top')
    for (const key of ['run', 'task', 'strategy']) expect(url.searchParams.has(key)).toBe(false)
    expect(readResearchNavigation(url.search)).toEqual({ view: 'reports', mode: 'portfolio', sourceId: id })
  })
  it('validates task ids and mode/strategy scope', () => {
    expect(readResearchNavigation('?research=reports&research-mode=single-wf&task=portfolio:../../&research-source=bad')).toEqual({ view: 'reports' })
    expect(readResearchNavigation('?research=single&strategy=wyckoff_trend_v1')).toEqual({ view: 'single', strategyId: 'wyckoff_trend_v1' })
    expect(readResearchNavigation('?research=reports&strategy=wyckoff_trend_v1')).toEqual({ view: 'reports' })
  })
})
