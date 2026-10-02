import { beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { PeriodEditor } from './period-editor'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const derived = { start_date: '2026-01-01', end_date: '2026-01-07', closed_rounds: 0, closed_pnl: '0', win_rate_pct: null, last_nav: null, return_pct: null, min_drawdown_pct: null, node_events: [], first_achievements: [], confirmed_snapshot_count: 0, status: 'ready', trades: [], rounds: [] }
const saved = (revision = 1, text = '正式正文') => ({ revision, sections: { core_goals: text }, derived })
function setup() { call.mockImplementation(async (path: string, method?: string, body?: { sections: object }) => /\/(weekly|monthly)$/.test(path) ? [] : method === 'PUT' ? { ...saved(2), sections: body?.sections } : saved()) }
beforeEach(() => { call.mockReset(); localStorage.clear(); setup() })

it('retains per-account and ISO-week drafts across period changes without sending a write', async () => {
  render(<PeriodEditor accountId="account-a" />)
  fireEvent.change(screen.getByLabelText('选择日期'), { target: { value: '2025-12-29' } })
  await screen.findByLabelText('核心目标')
  fireEvent.change(screen.getByLabelText('核心目标'), { target: { value: '跨年周的本机长文' } })
  expect(JSON.parse(localStorage.getItem('trade-rebuild:period-draft:/accounts/account-a/period-reviews/weekly/2026-W01')!).sections.core_goals).toBe('跨年周的本机长文')
  fireEvent.change(screen.getByLabelText('选择日期'), { target: { value: '2026-01-12' } })
  await waitFor(() => expect((screen.getByLabelText('核心目标') as HTMLTextAreaElement).value).toBe('正式正文'))
  fireEvent.change(screen.getByLabelText('选择日期'), { target: { value: '2026-01-01' } })
  await waitFor(() => expect((screen.getByLabelText('核心目标') as HTMLTextAreaElement).value).toBe('跨年周的本机长文'))
  expect(call.mock.calls.some(([, method]) => method === 'PUT')).toBe(false)
})

it('requires explicit conflict resolution against newer formal text before saving local text', async () => {
  const key = 'trade-rebuild:period-draft:/accounts/account-a/period-reviews/weekly/2026-W01'
  localStorage.setItem(key, JSON.stringify({ base_revision: 1, sections: { core_goals: '保留我的草稿' } }))
  call.mockImplementation(async (path: string, method?: string, body?: { sections: object }) => /\/(weekly|monthly)$/.test(path) ? [] : method === 'PUT' ? { ...saved(3), sections: body?.sections } : saved(2, '他处更新的正式正文'))
  render(<PeriodEditor accountId="account-a" />)
  fireEvent.change(screen.getByLabelText('选择日期'), { target: { value: '2026-01-01' } })
  await screen.findByRole('button', { name: '采用正式周期正文' })
  expect((screen.getByRole('button', { name: '保存周期复盘' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '以最新版本继续编辑周期草稿' }))
  fireEvent.click(screen.getByRole('button', { name: '保存周期复盘' }))
  await waitFor(() => expect(call).toHaveBeenCalledWith('/accounts/account-a/period-reviews/weekly/2026-W01', 'PUT', { expected_revision: 2, sections: { core_goals: '保留我的草稿' } }))
  await waitFor(() => expect(localStorage.getItem(key)).toBeNull())
})

it('a late save cannot replace another account or erase a newer local draft', async () => {
  let resolveSave!: (value: unknown) => void
  call.mockImplementation((path: string, method?: string) => method === 'PUT' ? new Promise(resolve => { resolveSave = resolve }) : Promise.resolve(/\/(weekly|monthly)$/.test(path) ? [] : saved(1, path.includes('account-b') ? 'B账户' : '正式正文')))
  const ui = render(<PeriodEditor accountId="account-a" />)
  fireEvent.change(screen.getByLabelText('选择日期'), { target: { value: '2026-01-01' } })
  await screen.findByLabelText('核心目标')
  fireEvent.change(screen.getByLabelText('核心目标'), { target: { value: '发送中的草稿' } })
  fireEvent.click(screen.getByRole('button', { name: '保存周期复盘' }))
  ui.rerender(<PeriodEditor accountId="account-b" />)
  await waitFor(() => expect((screen.getByLabelText('核心目标') as HTMLTextAreaElement).value).toBe('B账户'))
  const key = 'trade-rebuild:period-draft:/accounts/account-a/period-reviews/weekly/2026-W01'
  localStorage.setItem(key, JSON.stringify({ base_revision: 1, sections: { core_goals: '另一页更新的草稿' } }))
  await act(async () => resolveSave(saved(2, '发送中的草稿')))
  expect((screen.getByLabelText('核心目标') as HTMLTextAreaElement).value).toBe('B账户')
  expect(JSON.parse(localStorage.getItem(key)!).sections.core_goals).toBe('另一页更新的草稿')
})
