import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { EventStoreWorkspace } from './event-store'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
const version = { window_days: 60, strict: true, code_sha256: 'c'.repeat(64), event_profile: {
  profile_id: 'profile-a', revision: 2, sha256: 'd'.repeat(64), snapshot: { name: '事件模板' } } }
const totals = { record_count: 0, stored_bytes: 0, algorithm_version: 'v1', backfill_cache_hits: 0,
  backfill_writes: 0, backfill_hit_rate: null, method: 'GET 不计算', versions: [] }
const preview = { input_sha256: 'a'.repeat(64), total_count: 20, cached_count: 3, missing_count: 17,
  start_date: '2025-01-01', end_date: '2025-01-30', versions: { 'version-a': version }, warnings: [] }
const cancelled = { id: 'job-a', state: 'cancelled', total_count: 20, completed_count: 10,
  cache_hits: 0, written_count: 10, elapsed_ms: 1000, result_bytes: 8000, error: null,
  capabilities: { cancel: false, resume: true } }

beforeEach(() => {
  mockApi.mockReset()
  mockApi.mockImplementation((path: string, method?: string) => {
    if (path === '/market/datasets') return Promise.resolve([{ id: 'dataset-a', symbol: '600000', first_date: '2025-01-01', last_date: '2025-01-30' }])
    if (path === '/research/event-profiles') return Promise.resolve({ profiles: [{ profile_id: 'profile-a', name: '事件模板', revision: 2 }], active_profile_id: 'profile-a' })
    if (path.endsWith('/stats')) return Promise.resolve(totals)
    if (path.endsWith('/jobs') && method === undefined) return Promise.resolve([])
    if (path.includes('/records?')) return Promise.resolve([])
    if (path.endsWith('/preview')) return Promise.resolve(preview)
    if (path.endsWith('/jobs') && method === 'POST') return Promise.resolve(cancelled)
    if (path.endsWith('/jobs/job-a')) return Promise.resolve(cancelled)
    if (path.endsWith('/resume')) return Promise.resolve({ ...cancelled, state: 'queued', capabilities: { cancel: true, resume: false } })
    throw new Error(`Unexpected ${method || 'GET'} ${path}`)
  })
})

it('only reads statistics/history when opened and never starts a job', async () => {
  render(<EventStoreWorkspace />)
  await screen.findByText('GET 不计算')
  expect(screen.getByText(/命中率 暂无任务/)).toBeTruthy()
  expect(mockApi.mock.calls.every(call => call[1] === undefined)).toBe(true)
})

it('requires explicit preview and confirmation with the frozen hash', async () => {
  render(<EventStoreWorkspace />)
  const select = await screen.findByLabelText('冻结行情样本（可多选）')
  await waitFor(() => expect(select.querySelectorAll('option').length).toBe(1))
  ;(select.querySelector('option') as HTMLOptionElement).selected = true
  fireEvent.change(select)
  fireEvent.click(screen.getByRole('button', { name: '预览版本与缺失快照' }))
  await screen.findByText(/本次 20 个判断 · 已缓存 3 · 待计算 17/)
  expect(mockApi.mock.calls.some(call => call[0].endsWith('/jobs') && call[1] === 'POST')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认创建回填任务' }))
  await screen.findByText('回填任务已保存；相同冻结请求复用已有任务。')
  const call = mockApi.mock.calls.find(call => call[0].endsWith('/jobs') && call[1] === 'POST')
  expect(call?.[2].expected_preview_sha256).toBe(preview.input_sha256)
  expect(call?.[2].event_profile_revision).toBe(2)
  expect(screen.getByRole('button', { name: '恢复未完成回填' })).toBeTruthy()
})

it('invalidates the previous preview when parameters change', async () => {
  render(<EventStoreWorkspace />)
  const select = await screen.findByLabelText('冻结行情样本（可多选）')
  await waitFor(() => expect(select.querySelectorAll('option').length).toBe(1))
  ;(select.querySelector('option') as HTMLOptionElement).selected = true
  fireEvent.change(select)
  fireEvent.click(screen.getByRole('button', { name: '预览版本与缺失快照' }))
  await screen.findByRole('button', { name: '确认创建回填任务' })
  fireEvent.change(screen.getByLabelText('观察窗口（逗号分隔）'), { target: { value: '90' } })
  expect(screen.queryByRole('button', { name: '确认创建回填任务' })).toBeNull()
  expect(mockApi.mock.calls.some(call => call[0].endsWith('/jobs') && call[1] === 'POST')).toBe(false)
})

it('exposes resume for a cancelled job without changing its frozen request', async () => {
  const fallback = mockApi.getMockImplementation()!
  mockApi.mockImplementation((...args) => args[0].endsWith('/jobs') && args[1] === undefined
    ? Promise.resolve([cancelled]) : fallback(...args))
  render(<EventStoreWorkspace />)
  fireEvent.click(await screen.findByRole('button', { name: '查看任务' }))
  fireEvent.click(await screen.findByRole('button', { name: '恢复未完成回填' }))
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith('/research/event-store/jobs/job-a/resume', 'POST', {}))
  expect(mockApi.mock.calls.some(call => call[0].endsWith('/preview'))).toBe(false)
})
