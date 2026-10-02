import { beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DailyReviewEditor } from './daily-review'
import { PeriodEditor } from './period-editor'
import { RoundNoteEditor } from './round-note'
import { clearReviewDraftMemoryForTests } from './review-drafts'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call, apiUpload: vi.fn() }))
vi.mock('./review-scores', () => ({ ReviewScores: () => null }))
vi.mock('./plan-comparison', () => ({ PlanComparison: () => null }))
vi.mock('./plan-rehearsal', () => ({ PlanDateControl: () => null, PositionRehearsal: () => null }))
const day = '2026-01-02'
const daily = (revision = 1, text = '') => ({ review_date: day, revision, title: '', overall_summary: text, market_observation: '', decision_review: '', reflection: '', mistakes: '', tomorrow_plan: '', tags: [], next_market_forecast: '', next_watchlist: [], next_position_plan: '', next_risk_plan: '', next_position_rehearsal: [], next_target_date: null })
const dailyKey = `trade-rebuild:daily-draft:a:${day}`
const note = (revision = 1, text = '') => ({ round_id: 'r1', revision, summary: text, round_exists: true, association_changed: false, linked_trade_ids: ['t'], current_trade_ids: ['t'], projection_status: 'fresh', related: { daily: [], periods: [] } })
const derived = { start_date: day, end_date: day, closed_rounds: 0, closed_pnl: '0', win_rate_pct: null, last_nav: null, return_pct: null, min_drawdown_pct: null, node_events: [], first_achievements: [], confirmed_snapshot_count: 0, status: 'fresh', trade_count: 0, trades: [], rounds: [] }
const period = (revision = 1, text = '') => ({ revision, period_key: '2026-W01', sections: { core_goals: text }, derived })
const seed = (text: string, revision = 1) => localStorage.setItem(dailyKey, JSON.stringify({ draft: daily(revision, text), saved_at: 'now' }))
function defaults() {
  call.mockImplementation(async (path: string, method?: string, body?: Record<string, unknown>) => {
    if (method === 'PUT') return { ...daily(2), ...body, revision: 2 }
    if (/daily-reviews\/\d{4}-\d\d-\d\d$/.test(path)) return daily()
    return []
  })
}
beforeEach(() => { vi.restoreAllMocks(); call.mockReset(); localStorage.clear(); clearReviewDraftMemoryForTests(); defaults() })

it('daily text load is independent of auxiliary history or attachment failures', async () => {
  call.mockImplementation(async (path: string) => { if (path.endsWith('/' + day)) return daily(1, '正式日正文'); throw new Error('附件暂不可用') })
  render(<DailyReviewEditor accountId="a" initialDate={day} />)
  expect((await screen.findByLabelText('当日总述') as HTMLTextAreaElement).value).toBe('正式日正文')
})

it('offline daily recovery is immediate and changed server revision requires explicit comparison before writing', async () => {
  seed('断网保留')
  call.mockRejectedValue(new Error('offline'))
  render(<DailyReviewEditor accountId="a" initialDate={day} />)
  expect((await screen.findByLabelText('当日总述') as HTMLTextAreaElement).value).toBe('断网保留')
  expect((screen.getByRole('button', { name: '立即保存' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('当日总述'), { target: { value: '离线续写' } })
  call.mockImplementation(async (path: string, method?: string, body?: Record<string, unknown>) => method === 'PUT' ? { ...daily(3), ...body, revision: 3 } : path.endsWith('/' + day) ? daily(2, '别人保存') : [])
  fireEvent.click(screen.getByRole('button', { name: '重新读取正式日复盘' }))
  await screen.findByRole('button', { name: '用本机草稿覆盖' })
  expect(call.mock.calls.some(([, method]) => method === 'PUT')).toBe(false)
  expect((screen.getByLabelText('当日总述') as HTMLTextAreaElement).value).toBe('离线续写')
  fireEvent.click(screen.getByRole('button', { name: '用本机草稿覆盖' }))
  await waitFor(() => expect(localStorage.getItem(dailyKey)).toBeNull())
  expect(call.mock.calls.find(([, method]) => method === 'PUT')?.[2]).toMatchObject({ expected_revision: 2, overall_summary: '离线续写' })
})

it('late save errors and old attachment responses cannot alter the next date', async () => {
  let rejectSave!: (reason: Error) => void, finishAttachment!: (value: unknown) => void
  call.mockImplementation((path: string, method?: string) => {
    if (method === 'PUT') return new Promise((_, reject) => { rejectSave = reject })
    if (path.includes(day + '/attachments')) return new Promise(resolve => { finishAttachment = resolve })
    return Promise.resolve(/daily-reviews\/\d{4}-\d\d-\d\d$/.test(path) ? { ...daily(1, path.endsWith(day) ? '旧日' : '新日'), review_date: path.slice(-10) } : [])
  })
  render(<DailyReviewEditor accountId="a" initialDate={day} />)
  fireEvent.change(await screen.findByLabelText('当日总述'), { target: { value: '旧日未提交' } })
  fireEvent.click(screen.getByRole('button', { name: '立即保存' }))
  fireEvent.change(screen.getByLabelText('复盘日期'), { target: { value: '2026-01-03' } })
  await waitFor(() => expect((screen.getByLabelText('当日总述') as HTMLTextAreaElement).value).toBe('新日'))
  await act(async () => { rejectSave(Object.assign(new Error('旧请求冲突'), { code: 'REVISION_CONFLICT' })); finishAttachment([{ id: 'old', original_name: '旧图', url: '/old', byte_size: 1, revision: 1 }]) })
  expect(screen.queryByText('旧请求冲突')).toBeNull(); expect(screen.queryByAltText('旧图')).toBeNull()
  expect(JSON.parse(localStorage.getItem(dailyKey)!).draft.overall_summary).toBe('旧日未提交')
})

it('typing during daily save preserves new text and rebases only its own revision', async () => {
  let finish!: (value: unknown) => void
  call.mockImplementation((path: string, method?: string) => method === 'PUT' ? new Promise(resolve => { finish = resolve }) : Promise.resolve(path.endsWith('/' + day) ? daily() : []))
  render(<DailyReviewEditor accountId="a" initialDate={day} />)
  fireEvent.change(await screen.findByLabelText('当日总述'), { target: { value: '已发送的段落' } })
  fireEvent.click(screen.getByRole('button', { name: '立即保存' }))
  fireEvent.change(screen.getByLabelText('当日总述'), { target: { value: '请求中继续打字' } })
  await act(async () => finish(daily(2, '已发送的段落')))
  expect((screen.getByLabelText('当日总述') as HTMLTextAreaElement).value).toBe('请求中继续打字')
  expect(JSON.parse(localStorage.getItem(dailyKey)!).draft).toMatchObject({ revision: 2, overall_summary: '请求中继续打字' })
})

it('storage quota failure keeps daily text across in-app unmount and offline remount', async () => {
  const ui = render(<DailyReviewEditor accountId="a" initialDate={day} />)
  await screen.findByLabelText('当日总述')
  vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError') })
  fireEvent.change(screen.getByLabelText('当日总述'), { target: { value: '额度不足仍保留' } })
  await screen.findByText(/本机存储写入失败/)
  ui.unmount(); call.mockRejectedValue(new Error('offline'))
  render(<DailyReviewEditor accountId="a" initialDate={day} />)
  expect((await screen.findByLabelText('当日总述') as HTMLTextAreaElement).value).toBe('额度不足仍保留')
})

it('cross-tab daily changes pause writes and require explicit local choice', async () => {
  render(<DailyReviewEditor accountId="a" initialDate={day} />)
  fireEvent.change(await screen.findByLabelText('当日总述'), { target: { value: '本页' } })
  const other = JSON.stringify({ draft: daily(1, '另一页'), saved_at: 'later' })
  localStorage.setItem(dailyKey, other)
  fireEvent(window, new StorageEvent('storage', { key: dailyKey, newValue: other }))
  expect((screen.getByRole('button', { name: '立即保存' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '采用另一页面日草稿' }))
  expect((screen.getByLabelText('当日总述') as HTMLTextAreaElement).value).toBe('另一页')
})

it('period drafts remain editable offline without invented derived statistics', async () => {
  const key = 'trade-rebuild:period-draft:/accounts/a/period-reviews/weekly/2026-W01'
  localStorage.setItem(key, JSON.stringify({ base_revision: 1, sections: { core_goals: '离线周正文' } }))
  call.mockRejectedValue(new Error('offline'))
  render(<PeriodEditor accountId="a" />)
  fireEvent.change(screen.getByLabelText('选择日期'), { target: { value: day } })
  expect((await screen.findByLabelText('核心目标') as HTMLTextAreaElement).value).toBe('离线周正文')
  expect((screen.getByRole('button', { name: '保存周期复盘' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.queryByText('交易 0 笔')).toBeNull()
  fireEvent.change(screen.getByLabelText('核心目标'), { target: { value: '离线周续写' } })
  call.mockImplementation(async (path: string) => /\/(weekly|monthly)$/.test(path) ? [] : period())
  fireEvent.click(screen.getByRole('button', { name: '重新读取周期正文' }))
  await waitFor(() => expect((screen.getByRole('button', { name: '保存周期复盘' }) as HTMLButtonElement).disabled).toBe(false))
  expect((screen.getByLabelText('核心目标') as HTMLTextAreaElement).value).toBe('离线周续写')
})

it('round text survives quick navigation and offline return, scoped by account and round', async () => {
  call.mockResolvedValue(note())
  const ui = render(<RoundNoteEditor accountId="a" roundId="r1" onSaved={() => {}} />)
  fireEvent.change(await screen.findByLabelText('人工回合摘要'), { target: { value: '回合长文' } })
  ui.rerender(<RoundNoteEditor accountId="a" roundId="r2" onSaved={() => {}} />)
  await waitFor(() => expect((screen.getByLabelText('人工回合摘要') as HTMLTextAreaElement).value).toBe(''))
  call.mockRejectedValue(new Error('offline'))
  ui.rerender(<RoundNoteEditor accountId="a" roundId="r1" onSaved={() => {}} />)
  expect((await screen.findByLabelText('人工回合摘要') as HTMLTextAreaElement).value).toBe('回合长文')
  expect((screen.getByRole('button', { name: '保存回合摘要' }) as HTMLButtonElement).disabled).toBe(true)
  ui.rerender(<RoundNoteEditor accountId="b" roundId="r1" onSaved={() => {}} />)
  expect(screen.queryByLabelText('人工回合摘要')).toBeNull()
  await screen.findByText(/正式回合摘要读取失败/)
})

it('round server conflict keeps draft until explicit rebase and old loads cannot cross rounds', async () => {
  const key = 'trade-rebuild:round-draft:/accounts/a/round-notes/r1'
  localStorage.setItem(key, JSON.stringify({ base_revision: 1, summary: '我的回合草稿' }))
  call.mockImplementation(async (_path: string, method?: string, body?: { summary: string }) => method === 'PUT' ? note(3, body!.summary) : note(2, '更新正文'))
  render(<RoundNoteEditor accountId="a" roundId="r1" onSaved={() => {}} />)
  await screen.findByRole('button', { name: '以最新版本继续编辑回合草稿' })
  expect((screen.getByRole('button', { name: '保存回合摘要' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '以最新版本继续编辑回合草稿' }))
  fireEvent.click(screen.getByRole('button', { name: '保存回合摘要' }))
  await screen.findByText('回合摘要已保存')
  expect(call).toHaveBeenCalledWith('/accounts/a/round-notes/r1', 'PUT', { expected_revision: 2, summary: '我的回合草稿' })
  expect(localStorage.getItem(key)).toBeNull()
})
