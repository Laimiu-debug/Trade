import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RealFeeEditor } from './real-fees'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
const settings = { version: 1, config: { commission_rate: '0.0003', minimum_commission: '5', sell_stamp_rate: '0.0005', transfer_rate: '0.00001' } }
const trade = { side: 'buy', quantity: 100, price: '10', fee: '0', fee_mode: 'auto' as const }
const deferred = () => { let resolve!: (value: unknown) => void; const promise = new Promise(resolveValue => { resolve = resolveValue }); return { promise, resolve } }
const preview = (value: string) => ({ fee: value, calculated_fee: value, fee_source: 'auto', breakdown: { commission: value, stamp: '0', transfer: '0' } })

beforeEach(() => { mockApi.mockReset() })
afterEach(() => { vi.useRealTimers() })

it('keeps the latest fee quote when an earlier price request finishes later', async () => {
  vi.useFakeTimers()
  const older = deferred(), newer = deferred()
  mockApi.mockImplementation(async (path: string) => path.endsWith('/fee-settings') ? settings : path.includes('price=10&') ? older.promise : newer.promise)
  const rendered = render(<RealFeeEditor accountId="account" trade={trade} />)
  await act(async () => { await vi.advanceTimersByTimeAsync(250) })
  expect(mockApi.mock.calls.filter(([path]) => path.includes('/fee-preview?'))).toHaveLength(1)
  rendered.rerender(<RealFeeEditor accountId="account" trade={{ ...trade, price: '20' }} />)
  await act(async () => { await vi.advanceTimersByTimeAsync(250) })
  await act(async () => { newer.resolve(preview('20')); await newer.promise })
  expect(screen.getByText(/当前输入预计费用/)).toHaveTextContent('¥ 20')
  await act(async () => { older.resolve(preview('10')); await older.promise })
  expect(screen.getByText(/当前输入预计费用/)).toHaveTextContent('¥ 20')
})

it('does not overwrite another account fee settings with an earlier account response', async () => {
  const older = deferred()
  mockApi.mockImplementation(async (path: string) => path === '/accounts/first/fee-settings' ? older.promise : path.endsWith('/fee-settings') ? { ...settings, version: 2 } : preview('5'))
  const rendered = render(<RealFeeEditor accountId="first" trade={trade} />)
  rendered.rerender(<RealFeeEditor accountId="second" trade={trade} />)
  fireEvent.click(screen.getByRole('button', { name: '设置费率' }))
  await waitFor(() => expect(screen.getByText('规则版本 2。费率填写小数，例如 0.0003 表示万分之三。')).toBeInTheDocument())
  await act(async () => { older.resolve(settings); await older.promise })
  expect(screen.getByText('规则版本 2。费率填写小数，例如 0.0003 表示万分之三。')).toBeInTheDocument()
})
