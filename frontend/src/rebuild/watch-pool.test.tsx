import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WatchPoolPanel } from './watch-pool'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const config = { enabled: true, source_mode: 'strategy', ret40_min: .2, ret40_max: 2,
  turnover20_min: .05, amount20_min: 5e8, amplitude20_min: .03, trend_classes: ['A', 'A_B'],
  retrace20_min: 0, retrace20_max: .3, pullback_days_max: 5, vol_slope20_min: 0, up_down_volume_ratio_min: 0, top_n: 50 }
const row = (symbol: string, dataset = symbol) => ({ symbol, dataset_id: dataset, as_of_date: '2025-05-01',
  ret40: .5, turnover20: .1, amount20: 1e9, amplitude20: .1, trend_class: 'A', retrace20: .1,
  pullback_days: 1, vol_slope20: .1, up_down_volume_ratio: 2 })
const saved = { revision: 1, state: { config, manual: [], automatic: [], order: [] }, sha256: 'a', updated_at: null }
beforeEach(() => { call.mockReset(); localStorage.clear() })

it('never imports or rewrites legacy data on mount or local edits', async () => {
  const legacy = JSON.stringify({ config, manual: [row('600000')], order: ['600000'] })
  localStorage.setItem('trade-rebuild.watch-pool.v1', legacy)
  call.mockResolvedValue(saved)
  render(<WatchPoolPanel input={[]} current={[]} b1Symbols={[]} onOpenMarket={vi.fn()} />)
  await screen.findByText(/修订 1/)
  expect(screen.queryByRole('row', { name: /600000/ })).toBeNull()
  fireEvent.click(screen.getByLabelText('启用自动观察'))
  expect(call.mock.calls.every(([, method]) => method === undefined)).toBe(true)
  expect(localStorage.getItem('trade-rebuild.watch-pool.v1')).toBe(legacy)
})

it('B1 aliases resolve exactly while same-code Shanghai index stays out', async () => {
  call.mockResolvedValue(saved)
  render(<WatchPoolPanel input={[row('sh000001'), row('sz000001'), row('bj920001')]}
    current={[]} b1Symbols={['000001.SZ', '920001.BJ']} onOpenMarket={vi.fn()} />)
  await screen.findByText(/修订 1/)
  fireEvent.click(screen.getByRole('button', { name: '使用当前筛选更新自动成员' }))
  expect(screen.queryByRole('row', { name: /sh000001/ })).toBeNull()
  expect(screen.getByRole('row', { name: /sz000001/ })).toBeTruthy()
  expect(screen.getByRole('row', { name: /bj920001/ })).toBeTruthy()
})

it('stale revision retains draft and requires explicit reconciliation', async () => {
  let reads = 0
  call.mockImplementation((_path: string, method?: string) => {
    if (method === 'PUT') return Promise.reject(Object.assign(new Error('版本冲突'), { code: 'WATCH_POOL_VERSION_CONFLICT' }))
    return Promise.resolve({ ...saved, revision: ++reads === 1 ? 1 : 2 })
  })
  render(<WatchPoolPanel input={[]} current={[]} b1Symbols={[]} onOpenMarket={vi.fn()} />)
  await screen.findByText(/修订 1/)
  fireEvent.click(screen.getByLabelText('启用自动观察'))
  fireEvent.click(screen.getByRole('button', { name: '保存观察池' }))
  await screen.findByText('比较服务端修订 2')
  expect((screen.getByLabelText('启用自动观察') as HTMLInputElement).checked).toBe(false)
  expect((screen.getByRole('button', { name: '保存观察池' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '保留草稿，以最新修订继续编辑' }))
  await waitFor(() => expect((screen.getByRole('button', { name: '保存观察池' }) as HTMLButtonElement).disabled).toBe(false))
})
