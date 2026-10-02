import '@testing-library/jest-dom/vitest'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { ResearchEditor } from './research'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
vi.mock('./strategy-presets', () => ({ StrategyPresets: () => null }))
let result: Record<string, unknown>
let strategyId: string
beforeEach(() => {
  result = { status: 'computed', signal: true, exit_signal: false, source_date: '2025-01-02', quality_flags: ['classic_reference_variant'], indicator: { close: '11.5' }, evaluation: { exit_signal: false, entry_reason: 'SMA_TREND_ABOVE', exit_reason: null, observed_bars: 201, required_bars: 200, local_score: 12 }, draft_eligible: false }
  mockApi.mockImplementation((path: string) => {
    if (path === '/research/event-profiles') return Promise.resolve({ profiles: [], active_profile_id: '' })
    if (path === '/research/runs/classic') return Promise.resolve({ id: 'classic', strategy_id: strategyId, strategy_version: '1', params: {}, result })
    return Promise.resolve([])
  })
})
it.each([
  ['classic_donchian_breakout_v1', 'prior_entry_high', '此前入场通道高点'],
  ['classic_sma_trend_v1', 'entry_threshold', '入场阈值'],
  ['classic_bollinger_reentry_v1', 'lower', '布林下轨'],
])('shows %s thresholds without the old ABC fallback', async (id, metric, label) => {
  strategyId = id
  result.indicator = { close: '11.5', [metric]: '10.123456' }
  render(<ResearchEditor simAccounts={[]} initialRunId="classic" onOpenSim={vi.fn()} onOpenMarket={vi.fn()} />)
  const region = await screen.findByRole('region', { name: '经典策略信号依据' })
  expect(within(region).getByText('入场信号：是')).toBeVisible()
  expect(within(region).getByText('退出信号：否')).toBeVisible()
  expect(within(region).getByText(label)).toBeVisible()
  expect(within(region).getByText('10.123456')).toBeVisible()
  expect(screen.queryByText(/模式 A \/ B \/ C/)).toBeNull()
})
it('shows exit-only evidence independently of a buy signal', async () => {
  strategyId = 'classic_sma_trend_v1'
  result.signal = false
  result.evaluation = { exit_signal: true, entry_reason: null, exit_reason: 'SMA_TREND_BELOW', reasons: ['SMA_TREND_BELOW'] }
  render(<ResearchEditor simAccounts={[]} initialRunId="classic" onOpenSim={vi.fn()} onOpenMarket={vi.fn()} />)
  const region = await screen.findByRole('region', { name: '经典策略信号依据' })
  expect(within(region).getByText('入场信号：否')).toBeVisible()
  expect(within(region).getByText('退出信号：是')).toBeVisible()
  expect(within(region).getAllByText(/收盘低于均线及退出缓冲/).length).toBeGreaterThan(0)
})
it('does not display a missing-history result as a negative trading decision', async () => {
  strategyId = 'classic_sma_trend_v1'
  result.status = 'insufficient_data'; result.signal = null; result.indicator = {}
  result.evaluation = { exit_signal: false, reasons: ['INSUFFICIENT_CLASSIC_HISTORY'], observed_bars: 20, required_bars: 200 }
  render(<ResearchEditor simAccounts={[]} initialRunId="classic" onOpenSim={vi.fn()} onOpenMarket={vi.fn()} />)
  const region = await screen.findByRole('region', { name: '经典策略信号依据' })
  expect(within(region).getByText('入场信号：数据不足')).toBeVisible()
  expect(within(region).getByText('退出信号：数据不足')).toBeVisible()
})
