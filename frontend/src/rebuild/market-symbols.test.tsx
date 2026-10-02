import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { sameMarketSymbol, marketSymbolKey } from './market-symbols'
import { ShareCardEditor } from './share-card'
import { MarketChart } from './market-chart'
import { AssetEstimate } from './asset-estimate'
import { SimulationEditor } from './simulation'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
vi.mock('./sim-review-tags', () => ({ SimReviewTags: () => null }))
vi.mock('./sim-performance', () => ({ SimPerformanceSummary: () => null }))
vi.mock('./sim-equity', () => ({ SimEquityReports: () => null }))
vi.mock('./stock-search', () => ({ StockSearch: ({ onSelect }: { onSelect: (row: unknown) => void }) =>
  <button onClick={() => onSelect({ symbol: '000001', prefixed_symbol: 'sz000001', name: '股票' })}>选择测试证券</button> }))

const datasets = [
  { id: 'stock-suffix', symbol: '000001.SZ', first_date: '2025-01-01', last_date: '2025-01-02', provider: 'manual', availability_quality: 'provided_availability' },
  { id: 'same-code-index', symbol: 'sh000001', first_date: '2025-01-01', last_date: '2025-01-02', provider: 'manual', availability_quality: 'provided_availability' },
]
const bars = ['2025-01-01', '2025-01-02'].map(event_date => ({ event_date, open: '10', high: '11', low: '9', close: '10.5', volume: 1000, available_at: `${event_date}T08:00:00Z` }))

beforeEach(() => { mockApi.mockReset() })

describe('market identity', () => {
  it.each(['600000', '600000.SH', 'SH600000', ' sh600000.SH '])('merges stock alias %s', value => {
    expect(marketSymbolKey(value)).toBe('sh600000')
  })
  it.each(['920001', 'BJ920001', '920001.BJ'])('keeps Beijing920 alias %s', value => {
    expect(marketSymbolKey(value)).toBe('bj920001')
  })
  it('preserves index, fund, manual symbols and exchange conflicts', () => {
    expect(sameMarketSymbol('sh000001', '000001.SH')).toBe(true)
    expect(sameMarketSymbol('sh000001', 'sz000001')).toBe(false)
    expect(sameMarketSymbol('sh000001', '000001')).toBe(false)
    expect(sameMarketSymbol('510300', '510300.SH')).toBe(true)
    expect(sameMarketSymbol('aapl', 'AAPL')).toBe(true)
    expect(sameMarketSymbol('SH920001', '920001')).toBe(false)
    expect(sameMarketSymbol('SH600000.SZ', '600000')).toBe(false)
    expect(sameMarketSymbol(null, '')).toBe(false)
  })
})

it('share card offers suffix stock sample and hides same-code index sample', async () => {
  mockApi.mockImplementation(async (path: string) => path === '/market/datasets' ? datasets : [])
  render(<ShareCardEditor />)
  fireEvent.click(screen.getByRole('button', { name: '选择测试证券' }))
  const selector = screen.getByLabelText('冻结行情样本')
  await waitFor(() => expect(selector.querySelector('option[value="stock-suffix"]')).not.toBeNull())
  expect(selector.querySelector('option[value="same-code-index"]')).toBeNull()
})

it('asset estimate accepts alias sample and excludes index without applying facts', async () => {
  mockApi.mockResolvedValue(datasets)
  const apply = vi.fn()
  render(<AssetEstimate accountId="real" day="2025-01-02" symbols={['000001']} onApply={apply} />)
  const selector = screen.getByLabelText('000001 行情样本')
  await waitFor(() => expect(selector.querySelector('option[value="stock-suffix"]')).not.toBeNull())
  expect(selector.querySelector('option[value="same-code-index"]')).toBeNull()
  expect(apply).not.toHaveBeenCalled()
})

it('chart shows matching alias execution and preserves AI marker but hides index trade', () => {
  render(<MarketChart bars={bars} symbol="000001.SZ" quality="provided_availability" aiStartDate="2025-01-02" executions={[
    { id: 'stock', date: '2025-01-02', symbol: 'SZ000001', side: 'buy', quantity: 100, price: '10', source: 'sim' },
    { id: 'index', date: '2025-01-02', symbol: 'sh000001', side: 'buy', quantity: 999, price: '10', source: 'sim' },
  ]} />)
  expect(screen.queryByRole('img', { name: '模拟 2025-01-02 买入 100 股，成交价 10' })).not.toBeNull()
  expect(screen.queryByRole('img', { name: '模拟 2025-01-02 买入 999 股，成交价 10' })).toBeNull()
  expect(screen.queryByRole('img', { name: 'AI 候选起爆日 2025-01-02' })).not.toBeNull()
})

it('simulation defaults to alias dataset and excludes same-code index in both selectors', async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path === '/market/datasets') return datasets
    if (path.endsWith('/portfolio')) return { account_id: 'sim', as_of_date: '2025-01-01', initial_capital: '10000', cash: '9000', reserved_cash: '0', available_cash: '9000',
      config: {}, config_version: 1, wallet_revision: 1, frozen: false, reset_group_id: null,
      positions: [{ symbol: '000001', quantity: 100, sellable_quantity: 100, cost_basis: '1000' }], valuation_quality: 'unknown' }
    if (path.endsWith('/orders')) return [{ id: 'order', symbol: '000001', side: 'buy', quantity: 100, limit_price: '10', status: 'pending', revision: 1 }]
    return []
  })
  render(<SimulationEditor accountId="sim" accountName="模拟" frozen={false} onAccountSwitch={async () => undefined} />)
  for (const [page, label] of [['持仓估值', '000001 的估值样本'], ['委托与结算', '000001 的撮合样本']]) {
    fireEvent.click(screen.getByRole('button', { name: page }))
    const selector = await screen.findByLabelText(label)
    expect((selector as HTMLSelectElement).value).toBe('stock-suffix')
    expect(selector.querySelector('option[value="same-code-index"]')).toBeNull()
    expect(within(selector).getAllByRole('option')).toHaveLength(2)
  }
  expect(mockApi.mock.calls.some(([, method]) => method === 'POST')).toBe(false)
})
