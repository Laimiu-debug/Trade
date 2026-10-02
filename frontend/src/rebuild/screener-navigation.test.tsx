import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { ScreenerEditor } from './screener'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
vi.mock('./strategy-presets', () => ({ StrategyPresets: () => null }))
vi.mock('./watch-pool', () => ({ WatchPoolPanel: () => null }))
vi.mock('./matrix-pool', () => ({ MatrixPoolPanel: () => <h3>矩阵专属工作区</h3> }))
const props = { datasets: [{ id: 'ds1', symbol: '600000', first_date: '2020-01-01', last_date: '2025-10-01', availability_quality: 'provided_availability' }], onPromoted: vi.fn(), onOpenMarket: vi.fn() }
beforeEach(() => {
  localStorage.clear()
  window.history.replaceState(null, '', '/?page=research&research=screener')
  mockApi.mockReset()
  mockApi.mockImplementation((path: string) => Promise.resolve(path === '/research/strategies' ? [{ id: 'b1_mtf_v1', enabled_in_rebuild: true }] : []))
})
it('opens B1 separately and retains shared sample selection when switching to the funnel', async () => {
  render(<ScreenerEditor {...props} preferredStrategy="b1_mtf_v1" />)
  await screen.findByRole('heading', { name: 'B1 多周期扫描' })
  expect(screen.queryByRole('heading', { name: '四步选股漏斗' })).toBeNull()
  fireEvent.click(screen.getByText('选择 B1 冻结样本 · 已选 0 只'))
  fireEvent.click(screen.getByRole('checkbox', { name: 'B1 样本 600000 ds1' }))
  expect(screen.getByRole('button', { name: '运行 B1（已选 1 只）' })).toBeEnabled()
  fireEvent.click(screen.getByRole('tab', { name: '四步漏斗' }))
  expect(screen.getByRole('heading', { name: '四步选股漏斗' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'B1 多周期扫描' })).toBeNull()
  expect(screen.getByRole('checkbox', { name: '选择 600000 ds1' })).toBeChecked()
})
it('opens the matrix entry from a directory strategy without mounting a B1 form', async () => {
  render(<ScreenerEditor {...props} preferredStrategy="matrix_signal_v1" />)
  await screen.findByRole('heading', { name: '矩阵专属工作区' })
  expect(screen.getByRole('tab', { name: '矩阵信号' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.queryByRole('heading', { name: 'B1 多周期扫描' })).toBeNull()
})
