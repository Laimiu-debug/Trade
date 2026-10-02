import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { IntradayWorkspace } from './intraday-workspace'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
const root = '/market/intraday'
const data = { id: 'cache-1', symbol: 'sh000001', date: '2026-09-25', provider: 'eastmoney_online', quality: 'source_one_minute', availability_quality: 'historical_availability_unknown', price_unit: 'index_points', volume_unit: 'lots', as_of_at: '2026-09-25T02:00:00Z', quality_flags: ['source_fields_missing'], source: { sha256: 'source-hash', retrieved_at: '2026-09-25T02:00:01Z' }, points: [{ time: '09:30', close: '3000', open: null, high: null, low: null, volume: '200', amount: null }] }
const rows = [{ id: data.id, symbol: data.symbol, date: data.date, point_count: 1, as_of_at: data.as_of_at }]
beforeEach(() => {
  mockApi.mockReset()
  mockApi.mockImplementation(async (path: string, method?: string) => {
    if (path === root + '/capabilities') return { index_symbols: ['sh000001'] }
    if (path.startsWith(root + '/snapshots?')) return rows
    if (path === root + '/fetch') return data
    if (path === root + '/snapshots/cache-1' && method === 'DELETE') return { deleted: true }
    if (path === root + '/snapshots/cache-1') return data
    throw new Error(path)
  })
})

it('only reads cache initially, preserves index identity and shows missing values with explicit units', async () => {
  render(<IntradayWorkspace symbol="sh000001" initialDay="2026-09-25" />)
  fireEvent.click(await screen.findByRole('button', { name: '查看缓存 cache-1' }))
  await screen.findByRole('img', { name: 'sh000001 2026-09-25 一分钟收盘价曲线' })
  expect(mockApi.mock.calls.every(call => !call[1])).toBe(true)
  expect(screen.getByText('最低收盘：3000.0000 点')).not.toBeNull()
  expect(screen.getByText('成交量合计：200 手')).not.toBeNull()
  expect(screen.getByText('成交额合计：未知 元')).not.toBeNull()
  expect(screen.getAllByText('缺失').length).toBe(4)
  fireEvent.click(screen.getByRole('button', { name: '主动联网获取分时' }))
  await screen.findByText('已保存独立分时缓存；重新进入页面只读取缓存，不自动联网。')
  expect(mockApi).toHaveBeenCalledWith(root + '/fetch', 'POST', { symbol: 'sh000001', date: '2026-09-25' })
})

it('failed explicit refresh keeps previous cache visible and date changes never post', async () => {
  render(<IntradayWorkspace symbol="sh000001" initialDay="2026-09-25" />)
  fireEvent.click(await screen.findByRole('button', { name: '查看缓存 cache-1' }))
  await screen.findByRole('img')
  mockApi.mockRejectedValueOnce(new Error('来源超时'))
  fireEvent.click(screen.getByRole('button', { name: '主动联网获取分时' }))
  await screen.findByText('来源超时；下方仍是此前保存的缓存。')
  expect(screen.queryByRole('img')).not.toBeNull()
  fireEvent.change(screen.getByLabelText('在线分时日期'), { target: { value: '2026-09-24' } })
  await waitFor(() => expect(screen.queryByRole('img')).toBeNull())
  expect(mockApi.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(1)
})

it('deletion needs explicit confirmation and removes displayed snapshot without network refresh', async () => {
  render(<IntradayWorkspace symbol="sh000001" initialDay="2026-09-25" />)
  fireEvent.click(await screen.findByRole('button', { name: '查看缓存 cache-1' }))
  await screen.findByRole('img')
  fireEvent.click(screen.getByRole('button', { name: '删除缓存' }))
  expect(mockApi.mock.calls.some(call => call[1] === 'DELETE')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认删除此缓存' }))
  await screen.findByText('已删除选定的分时缓存。')
  expect(screen.queryByRole('img')).toBeNull()
  expect(mockApi.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(0)
})
