import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ResearchEditor } from './research'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
vi.mock('./strategy-presets', () => ({ StrategyPresets: () => null }))
vi.mock('./screener', () => ({ ScreenerEditor: () => null }))
vi.mock('./strategy-scans', () => ({ StrategyScanEditor: () => null }))
vi.mock('./signal-baskets', () => ({ SignalBasketEditor: () => null }))
vi.mock('./event-profiles', () => ({ EventProfileEditor: () => null }))
const run = { id: 'run-a', dataset_id: 'dataset-a', strategy_id: 'relative_strength_breakout_v1', strategy_version: '1', decision_at: '2025-01-04T08:00:00Z', strict: true, params: {}, created_at: '', result: { status: 'computed', signal: true, draft_eligible: true, source_date: '2025-01-04', quality_flags: [], candidate: null, score: null, executable_date: null } }
const quote = { quantity: 200, estimated_fees: '5.02', required_cash: '2005.02', spendable_cash: '8994.99', cash_gap: '0.00', max_affordable_quantity: 800, can_create: true, note: '含费用买入预算', quote_sha256: 'frozen-quote', denominator_assets: '10294.99', valuation_date: '2025-01-04', requested_budget: '2058.99' }

beforeEach(() => {
  mockApi.mockReset()
  mockApi.mockImplementation((path: string, method?: string) => {
    if (path === '/market/datasets' || path === '/research/strategies') return Promise.resolve([])
    if (path === '/research/event-profiles') return Promise.resolve({ profiles: [], active_profile_id: '' })
    if (path === '/research/runs') return Promise.resolve([run])
    if (path === '/research/runs/run-a') return Promise.resolve(run)
    if (path === '/sim-accounts/sim-a/equity-reports') return Promise.resolve([{ id: 'report-a', date_to: '2025-01-04', wallet_revision: 4, summary: { ending_assets: '10294.99' } }])
    if (path.endsWith('/equity-reports/size')) return Promise.resolve(quote)
    if (path.endsWith('/equity-reports/drafts') && method === 'POST') return Promise.resolve({ draft: { id: 'draft-a' } })
    throw new Error(`Unexpected ${method || 'GET'} ${path}`)
  })
})

it('saves asset-percent drafts only with the selected frozen report and a fresh sizing hash', async () => {
  render(<ResearchEditor simAccounts={[{ id: 'sim-a', name: '模拟账户 A' }]} initialRunId="run-a" onOpenSim={vi.fn()} onOpenMarket={vi.fn()} />)
  await screen.findByRole('heading', { name: '加入模拟委托草稿' })
  fireEvent.change(screen.getByLabelText('委托限价'), { target: { value: '10' } })
  fireEvent.change(screen.getByLabelText('换算方式'), { target: { value: 'asset_percent' } })
  fireEvent.change(screen.getByLabelText('比例（%）'), { target: { value: '20' } })
  await screen.findByRole('option', { name: /钱包版本 4/ })
  expect((screen.getByRole('button', { name: '保存草稿' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('总资产分母报告'), { target: { value: 'report-a' } })
  fireEvent.click(screen.getByRole('button', { name: '估算数量与费用' }))
  await screen.findByText(/总资产分母：¥ 10294.99/)
  expect((screen.getByLabelText('草稿数量（股）') as HTMLInputElement).readOnly).toBe(true)
  expect((screen.getByLabelText('草稿数量（股）') as HTMLInputElement).value).toBe('200')
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith('/sim-accounts/sim-a/equity-reports/drafts', 'POST', {
    source_run_id: 'run-a', report_id: 'report-a', percent: '20', limit_price: '10', expected_quote_sha256: 'frozen-quote',
  }))
  expect(mockApi.mock.calls.some(call => call[0] === '/sim-accounts/sim-a/drafts')).toBe(false)
  fireEvent.change(screen.getByLabelText('比例（%）'), { target: { value: '30' } })
  expect((screen.getByRole('button', { name: '保存草稿' }) as HTMLButtonElement).disabled).toBe(true)
})
