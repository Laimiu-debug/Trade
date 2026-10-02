import { beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReviewScores } from './review-scores'
import { clearReviewDraftMemoryForTests } from './review-drafts'
const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const day = '2026-01-02'
const base = `/accounts/a/daily-reviews/${day}/scores`
const key = `trade-rebuild:score-draft:/accounts/a/daily-reviews/${day}:daily:`
const sheet = (revision = 1) => ({ id: 'sheet', scope: 'daily', subject_id: day, trade_ids: [], association_changed: false, scores: { discipline: { final: 3, ai: 8, comment: '人工意见' } }, comment: '', revision })
beforeEach(() => { call.mockReset(); localStorage.clear(); clearReviewDraftMemoryForTests(); call.mockImplementation(async (path: string) => path.endsWith('/scores') ? [sheet()] : []) })
it('daily score and long comment survive scope changes and offline remount with zero preserved', async () => {
  const ui = render(<ReviewScores accountId="a" day={day} />)
  await waitFor(() => expect((screen.getByLabelText('计划执行') as HTMLSelectElement).value).toBe('3'))
  fireEvent.change(screen.getByLabelText('计划执行'), { target: { value: '0' } })
  fireEvent.change(screen.getByLabelText('整体点评'), { target: { value: '未保存的评分解释' } })
  fireEvent.change(screen.getByLabelText('评分范围'), { target: { value: 'trade' } })
  fireEvent.change(screen.getByLabelText('评分范围'), { target: { value: 'daily' } })
  expect((screen.getByLabelText('计划执行') as HTMLSelectElement).value).toBe('0')
  expect((screen.getByLabelText('整体点评') as HTMLTextAreaElement).value).toBe('未保存的评分解释')
  ui.unmount(); call.mockRejectedValue(new Error('offline'))
  render(<ReviewScores accountId="a" day={day} />)
  await screen.findByText('offline')
  expect((screen.getByLabelText('整体点评') as HTMLTextAreaElement).value).toBe('未保存的评分解释')
  expect((screen.getByRole('button', { name: '保存人工评分' }) as HTMLButtonElement).disabled).toBe(true)
})
it('changed formal score requires explicit rebase and never overwrites AI suggestions while saving final', async () => {
  localStorage.setItem(key, JSON.stringify({ base_revision: 1, scores: { discipline: { final: 0, comment: '人工草稿' } }, comment: '说明' }))
  call.mockImplementation(async (path: string, method?: string, body?: { scores: unknown; comment: string }) => method === 'PUT' ? { ...sheet(3), scores: body!.scores, comment: body!.comment } : path.endsWith('/scores') ? [sheet(2)] : [])
  render(<ReviewScores accountId="a" day={day} />)
  await screen.findByRole('button', { name: '以最新版本继续编辑评分草稿' })
  expect((screen.getByRole('button', { name: '保存人工评分' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '以最新版本继续编辑评分草稿' }))
  fireEvent.click(screen.getByRole('button', { name: '保存人工评分' }))
  await screen.findByText('人工最终评分已保存')
  expect(call).toHaveBeenCalledWith(base, 'PUT', { scope: 'daily', trade_ids: [], scores: { discipline: { final: 0, comment: '人工草稿' } }, comment: '说明', expected_revision: 2 })
  expect(localStorage.getItem(key)).toBeNull()
})
it('late score save cannot overwrite another account fields', async () => {
  let finish!: (value: unknown) => void
  call.mockImplementation((path: string, method?: string) => method === 'PUT' ? new Promise(resolve => { finish = resolve }) : Promise.resolve(path.endsWith('/scores') ? [sheet()] : []))
  const ui = render(<ReviewScores accountId="a" day={day} />)
  await waitFor(() => expect((screen.getByLabelText('计划执行') as HTMLSelectElement).value).toBe('3'))
  fireEvent.change(screen.getByLabelText('整体点评'), { target: { value: 'A保留' } })
  fireEvent.click(screen.getByRole('button', { name: '保存人工评分' }))
  ui.rerender(<ReviewScores accountId="b" day={day} />)
  await waitFor(() => expect((screen.getByLabelText('整体点评') as HTMLTextAreaElement).value).toBe(''))
  await act(async () => finish({ ...sheet(2), comment: 'A保留' }))
  expect((screen.getByLabelText('整体点评') as HTMLTextAreaElement).value).toBe('')
  expect(JSON.parse(localStorage.getItem(key)!).comment).toBe('A保留')
})
