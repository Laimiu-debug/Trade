import { beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SimulationEditor } from './simulation'
import { useWorkspacePage } from './use-workspace-page'
import { WorkspaceNavigation } from './workspace-navigation'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
vi.mock('./sim-review-tags', () => ({ SimReviewTags: () => null }))
vi.mock('./sim-performance', () => ({ SimPerformanceSummary: () => null }))
vi.mock('./sim-equity', () => ({ SimEquityReports: () => null }))

beforeEach(() => {
  window.history.replaceState(null, '', '/?page=simulation')
  mockApi.mockReset()
  mockApi.mockImplementation(async (path: string) => path.endsWith('/portfolio') ? {
    account_id: 'sim', as_of_date: '2025-01-01', initial_capital: '10000', cash: '10000',
    reserved_cash: '0', available_cash: '10000', config: {}, config_version: 1,
    wallet_revision: 1, positions: [], valuation_quality: 'unknown',
  } : [])
})

it.each(['valuation', 'recovery'])('frozen account rejects unavailable %s route and shows a usable history tab', async view => {
  window.history.replaceState(null, '', '/?page=simulation&simulation-view=' + view)
  render(<SimulationEditor accountId="sim" accountName="恢复点" frozen onAccountSwitch={async () => undefined} />)
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith('/sim-accounts/sim/portfolio'))
  expect(screen.getByRole('button', { name: '历史持仓' }).getAttribute('aria-current')).toBe('page')
  expect(screen.queryByRole('heading', { name: '历史持仓' })).not.toBeNull()
  expect(screen.queryByRole('button', { name: '提交模拟委托' })).toBeNull()
  expect(mockApi.mock.calls.some(([, method]) => method === 'POST')).toBe(false)
})

it('simulation subpages preserve a pending order draft and hide inactive submit controls', async () => {
  render(<SimulationEditor accountId="sim" accountName="模拟" frozen={false} onAccountSwitch={async () => undefined} />)
  await waitFor(() => expect((screen.getByLabelText('信号日期') as HTMLInputElement).value).toBe('2025-01-01'))
  fireEvent.change(screen.getByLabelText('代码'), { target: { value: '600000' } })
  fireEvent.change(screen.getByLabelText('限价'), { target: { value: '12.34' } })
  fireEvent.click(screen.getByRole('button', { name: '委托与结算' }))
  expect(screen.queryByRole('heading', { name: '提交模拟委托' })).toBeNull()
  expect(screen.getByRole('button', { name: '委托与结算' }).getAttribute('aria-current')).toBe('page')
  fireEvent.click(screen.getByRole('button', { name: '持仓与下单' }))
  expect((screen.getByLabelText('代码') as HTMLInputElement).value).toBe('600000')
  expect((screen.getByLabelText('限价') as HTMLInputElement).value).toBe('12.34')
  expect(mockApi.mock.calls.some(([, method]) => method === 'POST')).toBe(false)
})

const views = ['one', 'two'] as const
function NavigationFixture() {
  const [view, change] = useWorkspacePage('view', views, 'one')
  return <WorkspaceNavigation label="test" current={view} items={views.map(item => [item, item] as const)} onChange={change} />
}
it('workspace navigation retains unrelated route keys and reacts to browser history', () => {
  window.history.replaceState(null, '', '/?page=market&dataset=frozen&view=invalid')
  render(<NavigationFixture />)
  expect(screen.getByRole('button', { name: 'one' }).getAttribute('aria-current')).toBe('page')
  fireEvent.click(screen.getByRole('button', { name: 'two' }))
  expect(new URLSearchParams(window.location.search).get('dataset')).toBe('frozen')
  expect(new URLSearchParams(window.location.search).get('view')).toBe('two')
  act(() => {
    window.history.replaceState(null, '', '/?page=market&dataset=frozen&view=one')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  expect(screen.getByRole('button', { name: 'one' }).getAttribute('aria-current')).toBe('page')
})
