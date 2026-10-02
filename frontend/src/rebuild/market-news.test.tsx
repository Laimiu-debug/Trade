import { expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MarketNewsEditor } from './market-news'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))

const result = (title: string) => ({
  request: { query: 'A股 热点', provider: 'auto', age_hours: 72, as_of_at: '2025-01-02T10:00:00Z', date_from: null, date_to: null, window_start: '2024-12-30T10:00:00Z', window_end: '2025-01-02T10:00:00Z' },
  snapshot_id: title, fetched_at: '2025-01-02T10:00:00Z', actual_provider: 'eastmoney', source_url: null,
  items: [{ id: title, title, snippet: title, url: null, published_at: '2025-01-02T08:00:00Z', source_name: '测试来源', provider: 'eastmoney' }],
  count: 1, source_item_count: 1, excluded: {}, cache_hit: true, cache_age_seconds: 0, cache_stale: false,
  cache_ttl_seconds: 300, attempted_providers: [], fallback_used: false, degraded: false, errors: [], status: 'ready', availability_quality: 'cached', notes: [],
})

it('an initial slow cache read cannot replace a later explicit refresh or trigger networking itself', async () => {
  let resolveInitial!: (value: ReturnType<typeof result>) => void
  mockApi.mockReset()
  mockApi.mockImplementation((path: string) => path === '/market/news'
    ? new Promise(resolve => { resolveInitial = resolve }) : Promise.resolve(result('显式刷新结果')))
  render(<MarketNewsEditor />)
  expect(mockApi.mock.calls).toEqual([['/market/news']])
  fireEvent.click(screen.getByRole('button', { name: '联网刷新' }))
  await waitFor(() => expect(screen.queryByRole('heading', { name: '显式刷新结果' })).not.toBeNull())
  await act(async () => { resolveInitial(result('迟到缓存结果')) })
  expect(screen.queryByRole('heading', { name: '显式刷新结果' })).not.toBeNull()
  expect(screen.queryByRole('heading', { name: '迟到缓存结果' })).toBeNull()
  expect(mockApi.mock.calls.filter(([, method]) => method === 'POST')).toHaveLength(1)
})
