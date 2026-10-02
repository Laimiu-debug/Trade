import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReviewScores } from './review-scores'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const score = (id: string, value: number) => ({ id, scope: 'daily', trade_ids: [], revision: 1,
  scores: { position: { final: value, comment: id, ai: null } }, comment: id })
beforeEach(() => { call.mockReset() })

it('changing dates with identical revisions loads the correct human score and comment', async () => {
  call.mockImplementation(async (path: string) => path.endsWith('/trades') ? [] : [score(path.includes('2025-01-01') ? 'first' : 'second', path.includes('2025-01-01') ? 3 : 7)])
  const ui = render(<ReviewScores accountId="real" day="2025-01-01" />)
  const selector = () => screen.getByLabelText(/仓位控制/) as HTMLSelectElement
  await waitFor(() => expect(selector().value).toBe('3'))
  ui.rerender(<ReviewScores accountId="real" day="2025-01-02" />)
  await waitFor(() => expect(selector().value).toBe('7'))
  expect((screen.getByLabelText('整体点评') as HTMLTextAreaElement).value).toBe('second')
})

it('zero is retained as a final score and adoption cannot discard unsaved human edits', async () => {
  const sheet = score('first', 0)
  call.mockImplementation(async (path: string) => path.endsWith('/trades') ? [] : [{ ...sheet, scores: { position: { ...sheet.scores.position, ai: 8, ai_comment: '建议依据' } } }])
  render(<ReviewScores accountId="real" day="2025-01-01" />)
  await waitFor(() => expect((screen.getByLabelText(/仓位控制/) as HTMLSelectElement).value).toBe('0'))
  fireEvent.change(screen.getByLabelText('整体点评'), { target: { value: '尚未保存的判断' } })
  expect((screen.getByRole('button', { name: '采用此维度建议' }) as HTMLButtonElement).disabled).toBe(true)
  expect(call.mock.calls.some(([, method]) => method === 'POST')).toBe(false)
})
