import { useState } from 'react'
import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { ResearchWorkspace } from './research-workspace'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
vi.mock('./research', () => ({ ResearchEditor: ({ initialStrategyId }: { initialStrategyId?: string }) => {
  const [value, setValue] = useState('')
  return <section aria-label="单股编辑器"><label>研究备注<input value={value} onChange={event => setValue(event.target.value)} /></label><span>{initialStrategyId}</span></section>
} }))
vi.mock('./screener', () => ({ ScreenerEditor: ({ preferredStrategy }: { preferredStrategy?: string }) => <section>筛选编辑器 · {preferredStrategy}</section> }))
vi.mock('./backtest', () => ({ BacktestEditor: () => <section>单股回测编辑器</section> }))
vi.mock('./portfolio-workspace', () => ({ PortfolioWorkspace: () => <section>组合编辑器</section> }))
vi.mock('./plateau-workspace', () => ({ PlateauWorkspace: () => <section>单股参数编辑器</section> }))
vi.mock('./portfolio-experiment-workspace', () => ({ PortfolioExperimentWorkspace: () => <section>组合参数编辑器</section> }))
vi.mock('./portfolio-walk-forward-workspace', () => ({ PortfolioWalkForwardWorkspace: () => <section>组合前推编辑器</section> }))
vi.mock('./report-library', () => ({ ReportLibrary: () => <section>单股报告编辑器</section> }))
vi.mock('./portfolio-report-library', () => ({ PortfolioReportLibrary: () => <section>组合报告编辑器</section> }))
const props = { simAccounts: [], onOpenSim: vi.fn(), onOpenMarket: vi.fn(), accountId: 'a' }
beforeEach(() => {
  window.history.replaceState(null, '', '/?page=research&account=a')
  mockApi.mockReset()
  mockApi.mockImplementation((path: string) => Promise.resolve(path === '/research/strategies' ? [
    { id: 'first', name: '可执行策略', family_name: '量价', version: '1', description: '冻结判断', signal_params: {}, limitations: [], enabled_in_rebuild: true },
    { id: 'matrix_signal_v1', name: '矩阵池', family_name: '矩阵', version: '1', description: '冻结池', signal_params: null, limitations: [], enabled_in_rebuild: true },
    { id: 'off', name: '停用策略', version: '1', description: '', signal_params: {}, limitations: [], enabled_in_rebuild: false },
  ] : []))
})
it('starts with the directory and never mounts the other working forms initially', async () => {
  render(<ResearchWorkspace {...props} />)
  await screen.findByText('可执行策略')
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('策略目录')
  expect(screen.queryByLabelText('研究备注')).toBeNull()
  expect(screen.queryByText('组合编辑器')).toBeNull()
  expect(screen.queryByText('单股回测编辑器')).toBeNull()
  expect(mockApi).toHaveBeenCalledTimes(2)
  expect(within(screen.getByText('停用策略').closest('article')!).getByRole('button')).toBeDisabled()
})
it('preserves unsaved inputs across separate URL pages and supports browser back navigation', async () => {
  render(<ResearchWorkspace {...props} />)
  fireEvent.click(within(screen.getByRole('navigation', { name: '研究子页面' })).getByRole('link', { name: /单股研究/ }))
  const note = await screen.findByLabelText('研究备注', {}, { timeout: 10_000 })
  fireEvent.change(note, { target: { value: '未提交参数备注' } })
  const previous = window.location.href
  fireEvent.click(within(screen.getByRole('navigation', { name: '研究子页面' })).getByRole('link', { name: /组合回测/ }))
  await screen.findByText('组合编辑器', {}, { timeout: 10_000 })
  expect(note).not.toBeVisible()
  expect(document.querySelectorAll('.research-page:not([hidden])')).toHaveLength(1)
  expect(new URLSearchParams(window.location.search).get('research')).toBe('portfolio')
  act(() => { window.history.replaceState(null, '', previous); window.dispatchEvent(new PopStateEvent('popstate')) })
  await waitFor(() => expect(note).toBeVisible())
  expect(note).toHaveValue('未提交参数备注')
  expect(screen.getByText('组合编辑器')).not.toBeVisible()
})
it('passes the selected strategy to the destination and separates experiment types', async () => {
  render(<ResearchWorkspace {...props} />)
  await screen.findByText('矩阵池')
  fireEvent.click(within(screen.getByText('矩阵池').closest('article')!).getByRole('button'))
  await screen.findByText('筛选编辑器 · matrix_signal_v1', {}, { timeout: 10_000 })
  expect(window.location.search).toContain('strategy=matrix_signal_v1')
  fireEvent.click(within(screen.getByRole('navigation', { name: '研究子页面' })).getByRole('link', { name: /参数实验/ }))
  await screen.findByText('单股参数编辑器', {}, { timeout: 10_000 })
  fireEvent.click(screen.getByRole('link', { name: '组合 Walk-forward' }))
  await screen.findByText('组合前推编辑器', {}, { timeout: 10_000 })
  expect(screen.getByText('单股参数编辑器')).not.toBeVisible()
  expect(window.location.search).toContain('research-mode=portfolio-wf')
})
