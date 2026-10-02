import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SimEquityReports } from './sim-equity'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
const root = '/sim-accounts/sim-a/equity-reports'
const summary = { ending_assets: null, ending_cash: '8994.99', range_return_pct: null, range_denominator_assets: '10000.00', range_denominator_date: '2025-01-01', max_drawdown_pct: null, observed_max_drawdown_pct: '0.0000', missing_observation_count: 1, stale_observation_count: 0 }
const report = { id: 'report-a', input_sha256: 'frozen-input', date_from: '2025-01-01', date_to: '2025-01-04', wallet_revision: 4, summary,
  result: { date_from: '2025-01-01', date_to: '2025-01-04', initial_date: '2025-01-01', initial_capital: '10000.00', summary, monthly: [], method: '实际费用、缺价未知', points: [
    { date: '2025-01-04', cash: '8994.99', total_assets: null, known_position_value: '0.00', asset_index: null, drawdown_pct: null, drawdown_quality: 'missing', quality: 'missing_quotes', cumulative_fees: '5.01', axis_reasons: [], positions: [{ symbol: 'sh600000', quantity: 100, dataset_id: null, close: null, quote_date: null, market_value: null, quality: 'missing' }] },
  ] } }

beforeEach(() => {
  mockApi.mockReset()
  mockApi.mockImplementation((path: string, method?: string) => {
    if (path === root + '/inputs') return Promise.resolve({ initial_date: '2025-01-01', wallet_date: '2025-01-04', initial_capital: '10000.00', wallet_revision: 4, symbols: ['sh600000'], current_quantities: { sh600000: 100 }, fill_count: 1 })
    if (path === '/market/datasets') return Promise.resolve([
      { id: 'raw-a', symbol: '600000.SH', first_date: '2025-01-01', last_date: '2025-01-04', adjustment: 'none' },
      { id: 'wrong-exchange', symbol: 'sz600000', first_date: '2025-01-01', last_date: '2025-01-04', adjustment: 'none' },
      { id: 'adjusted', symbol: '600000.SH', first_date: '2025-01-01', last_date: '2025-01-04', adjustment: 'qfq' },
    ])
    if (path === root && !method) return Promise.resolve([])
    if (path === root + '/preview' || path === root && method === 'POST') return Promise.resolve(report)
    throw new Error(`Unexpected ${method || 'GET'} ${path}`)
  })
})

it('loads only history metadata, leaves quote selection explicit and isolates exchange and adjustment', async () => {
  render(<SimEquityReports accountId="sim-a" />)
  const selector = await screen.findByLabelText('sh600000 估值样本（当前 100 股）')
  expect((selector as HTMLSelectElement).value).toBe('')
  expect(screen.queryByRole('option', { name: /sz600000/ })).toBeNull()
  expect((selector as HTMLSelectElement).options.length).toBe(2)
  expect(mockApi.mock.calls.every(call => !call[1])).toBe(true)
})

it('keeps missing assets unknown and requires a current explicit preview before saving', async () => {
  render(<SimEquityReports accountId="sim-a" />)
  await screen.findByLabelText('资产曲线开始日')
  fireEvent.click(screen.getByRole('button', { name: '预览资产曲线' }))
  await screen.findByText('期末总资产：缺失')
  expect(screen.getByText('样本最大回撤：缺失')).toBeTruthy()
  expect(screen.queryByRole('img', { name: '模拟总资产（元）' })).toBeNull()
  fireEvent.change(screen.getByLabelText('资产曲线开始日'), { target: { value: '2025-01-02' } })
  await waitFor(() => expect(screen.queryByRole('button', { name: '保存资产报告' })).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: '预览资产曲线' }))
  fireEvent.click(await screen.findByRole('button', { name: '保存资产报告' }))
  await screen.findByText('已保存冻结资产报告，可用于当前日完整总资产比例换算。')
  expect(mockApi).toHaveBeenCalledWith(root, 'POST', { date_from: '2025-01-02', date_to: '2025-01-04', dataset_ids: [], strict: true, expected_input_sha256: 'frozen-input' })
  expect(mockApi.mock.calls.some(call => call[0].includes('/orders') || call[0].includes('/drafts'))).toBe(false)
})

it('invalidates preview when parent signals a changed wallet version', async () => {
  const view = render(<SimEquityReports accountId="sim-a" walletRevision={4} />)
  await screen.findByLabelText('资产曲线开始日')
  fireEvent.click(screen.getByRole('button', { name: '预览资产曲线' }))
  await screen.findByRole('button', { name: '保存资产报告' })
  view.rerender(<SimEquityReports accountId="sim-a" walletRevision={5} />)
  await waitFor(() => expect(screen.queryByRole('button', { name: '保存资产报告' })).toBeNull())
  expect(mockApi.mock.calls.filter(call => call[0] === root + '/inputs').length).toBe(2)
})
