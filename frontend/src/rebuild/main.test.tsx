import { beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { App } from './main'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi, connect: async () => undefined }))
vi.mock('./ai-workspace', () => ({ AIWorkspace: () => null }))
vi.mock('./pending-trades', () => ({ PendingTradesEditor: () => null }))
vi.mock('./real-fees', () => ({ RealFeeEditor: () => null }))
vi.mock('./asset-estimate', () => ({ AssetEstimate: () => null }))

const accountA = 'a'.repeat(32), accountB = 'b'.repeat(32)
const accounts = [{ id: accountA, name: '账户 A', kind: 'real', currency: 'CNY', input_revision: 1 }, { id: accountB, name: '账户 B', kind: 'real', currency: 'CNY', input_revision: 1 }]
const read = async (path: string) => path === '/accounts' ? accounts : path.endsWith('/analytics') ? { status: 'ready', account_input_revision: 1, projection_input_revision: 1, error: null, result: null } : []
const deferred = () => { let resolve!: (value: unknown) => void; const promise = new Promise(resolveValue => { resolve = resolveValue }); return { promise, resolve } }

beforeEach(() => {
  mockApi.mockReset()
  mockApi.mockImplementation(read)
  window.history.replaceState(null, '', '/?page=trades&account=' + accountA)
})

it.each([
  ['trades', '证券代码', '600000', '新增交易', '/trades', '价格', '10'],
  ['flows', '金额', '1000', '保存流水', '/cash-flows', '', ''],
  ['snapshots', '总资产', '100000', '保存快照', '/snapshots/', '', ''],
])('locks immediate duplicate %s submissions until the accepted write and refresh finish', async (page, field, value, buttonName, suffix, extraField, extraValue) => {
  window.history.replaceState(null, '', '/?page=' + page + '&account=' + accountA)
  const save = deferred()
  const accepted: string[] = []
  mockApi.mockImplementation(async (path: string, method = 'GET') => {
    if (method === 'GET') return read(path)
    await save.promise
    accepted.push(path)
    return {}
  })
  render(<App />)
  const button = await screen.findByRole('button', { name: buttonName }, { timeout: 10_000 })
  fireEvent.change(screen.getByLabelText(field), { target: { value } })
  if (extraField) fireEvent.change(screen.getByLabelText(extraField), { target: { value: extraValue } })
  const form = button.closest('form')!
  // Submit twice in one React batch, bypassing DOM disabled controls as well.
  // The synchronous lock must protect even before state reaches the screen.
  act(() => { fireEvent.submit(form); fireEvent.submit(form) })
  expect(mockApi.mock.calls.filter(([path, method]) => method !== undefined && method !== 'GET' && path.includes(suffix))).toHaveLength(1)
  expect(button).toBeDisabled()
  expect(form).toHaveAttribute('aria-busy', 'true')
  expect(screen.getByText('正在保存，请稍候…')).toBeInTheDocument()
  await act(async () => { save.resolve({}); await save.promise })
  await waitFor(() => expect(button).not.toBeDisabled())
  expect(accepted).toHaveLength(1)
})

it('releases the submission lock after failure so a corrected write can be saved', async () => {
  let attempts = 0
  mockApi.mockImplementation(async (path: string, method = 'GET') => {
    if (method === 'GET') return read(path)
    if (++attempts === 1) throw new Error('校验失败，请修改价格')
    return {}
  })
  render(<App />)
  const button = await screen.findByRole('button', { name: '新增交易' }, { timeout: 10_000 })
  fireEvent.change(screen.getByLabelText('证券代码'), { target: { value: '600000' } })
  fireEvent.change(screen.getByLabelText('价格'), { target: { value: '10' } })
  fireEvent.submit(button.closest('form')!)
  await screen.findByText('校验失败，请修改价格')
  expect(button).not.toBeDisabled()
  fireEvent.submit(button.closest('form')!)
  await waitFor(() => expect(attempts).toBe(2))
  await screen.findByText('交易已保存')
})

it('keeps another account draft when an earlier account save completes and allows that account to save independently', async () => {
  window.history.replaceState(null, '', '/?page=flows&account=' + accountA)
  const first = deferred()
  const writes: string[] = []
  mockApi.mockImplementation(async (path: string, method = 'GET') => {
    if (method === 'GET') return read(path)
    writes.push(path)
    if (path.includes(accountA)) await first.promise
    return {}
  })
  render(<App />)
  const button = await screen.findByRole('button', { name: '保存流水' }, { timeout: 10_000 })
  fireEvent.change(screen.getByLabelText('金额'), { target: { value: '1000' } })
  fireEvent.submit(button.closest('form')!)
  expect(button).toBeDisabled()
  fireEvent.change(screen.getByLabelText('账户'), { target: { value: accountB } })
  fireEvent.change(screen.getByLabelText('金额'), { target: { value: '2000' } })
  expect(button).not.toBeDisabled()
  await act(async () => { first.resolve({}); await first.promise })
  expect(screen.getByLabelText('金额')).toHaveValue(2000)
  expect(screen.queryByText('资金流水已保存')).not.toBeInTheDocument()
  fireEvent.submit(button.closest('form')!)
  await waitFor(() => expect(writes).toEqual(['/accounts/' + accountA + '/cash-flows', '/accounts/' + accountB + '/cash-flows']))
  await screen.findByText('资金流水已保存')
})
