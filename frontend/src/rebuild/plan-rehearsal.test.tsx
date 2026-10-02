import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { PlanDateControl, PositionRehearsal, type RehearsalRow } from './plan-rehearsal'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
const calendar = { revision: 0, source: null, start_date: null, end_date: null, days: [] }
const base = { sha256: 'a'.repeat(64), source: 'snapshot', cash: null, snapshot_revision: 1,
  account_input_revision: 3, positions: [{ code: '000001', name: '股票', qty: 100 }],
  quality_flags: ['snapshot_cash_missing'], method: '确认快照',
  fee_settings: { version: 1, config: { commission_rate: '0.0003', minimum_commission: '5.00', sell_stamp_rate: '0.001', transfer_rate: '0.00001' } } }
const result = { sha256: 'b'.repeat(64), remaining_cash: null, estimated_fees: '5.01', projected_total: null,
  position_value: '2000.00', cash_status: 'unknown', quality_flags: ['snapshot_cash_missing'], method: '预演说明',
  rows: [{ code: '000001', current_qty: 100, target_qty: 200, quantity_delta: 100, price: '10.0000',
    fees: '5.01', cash_delta: '-1005.01', quality_flags: [], price_evidence: { source: 'manual_assumption', quote_date: null } }] }

function Editor({ initial = [] }: { initial?: RehearsalRow[] }) {
  const [rows, setRows] = useState(initial)
  return <PositionRehearsal accountId="account-a" day="2025-01-03" targetDate={null} rows={rows} onChange={setRows} />
}

beforeEach(() => {
  mockApi.mockReset()
  mockApi.mockImplementation((path: string) => {
    if (path.endsWith('/baseline?source=snapshot')) return Promise.resolve(base)
    if (path === '/market/datasets') return Promise.resolve([
      { id: 'stock', symbol: '000001.SZ', last_date: '2025-01-03' },
      { id: 'index', symbol: 'sh000001', last_date: '2025-01-03' },
    ])
    if (path.endsWith('/preview')) return Promise.resolve(result)
    if (path.includes('/plan-date')) return Promise.resolve({ suggested_date: null, target_status: 'unknown', calendar })
    throw new Error(`Unexpected API: ${path}`)
  })
})

describe('review planning', () => {
  it('keeps an unknown target blank and does not infer Monday', async () => {
    const change = vi.fn()
    render(<PlanDateControl day="2025-01-03" value={null} onChange={change} />)
    await screen.findByText('尚未指定执行日，计划不会自动指向周一。')
    expect((screen.getByLabelText('计划执行日') as HTMLInputElement).value).toBe('')
    expect(change).not.toHaveBeenCalled()
    expect(screen.queryByText(/采用本地日历下一交易日/)).toBeNull()
  })

  it('applies a local calendar suggestion only after an explicit click', async () => {
    mockApi.mockResolvedValue({ suggested_date: '2025-01-07', target_status: 'closed',
      calendar: { ...calendar, source: 'local source', revision: 2, start_date: '2025-01-01', end_date: '2025-01-31' } })
    const change = vi.fn()
    render(<PlanDateControl day="2025-01-03" value="2025-01-06" onChange={change} />)
    const button = await screen.findByRole('button', { name: '采用本地日历下一交易日 2025-01-07' })
    expect(change).not.toHaveBeenCalled()
    expect(screen.getByText('本地日历标记为休市日，请核对执行日。')).toBeTruthy()
    fireEvent.click(button)
    expect(change).toHaveBeenCalledWith('2025-01-07')
    expect(mockApi.mock.calls.every(call => call[1] === undefined)).toBe(true)
  })

  it('copies only the selected baseline into a draft without generating trades', async () => {
    render(<Editor />)
    fireEvent.click(await screen.findByRole('button', { name: '复制当前基准持仓到草稿' }))
    expect((screen.getByLabelText('代码') as HTMLInputElement).value).toBe('000001')
    expect((screen.getByLabelText('预演数量') as HTMLInputElement).value).toBe('100')
    expect((screen.getByLabelText('预计价格') as HTMLInputElement).value).toBe('')
    expect(screen.getByText(/基准现金：未知/)).toBeTruthy()
    expect(mockApi.mock.calls.every(call => call[1] === undefined)).toBe(true)
  })

  it('shows unknown cash and separate costs; editing discards the old preview', async () => {
    render(<Editor initial={[{ code: '000001', name: '', qty: 200, note: '', price: '10' }]} />)
    await screen.findByRole('button', { name: '复制当前基准持仓到草稿' })
    fireEvent.click(screen.getByRole('button', { name: '检查预演现金与费用' }))
    const section = await screen.findByRole('region', { name: '预演检查结果' })
    expect(within(section).getByText(/预计剩余现金 未知/)).toBeTruthy()
    const call = mockApi.mock.calls.find(call => call[0].endsWith('/preview'))
    expect(call?.[2].expected_baseline_sha256).toBe(base.sha256)
    expect(call?.[2].target_date).toBeNull()
    fireEvent.change(screen.getByLabelText('预计价格'), { target: { value: '11' } })
    expect(screen.queryByRole('region', { name: '预演检查结果' })).toBeNull()
  })

  it('filters same digits from another exchange out of the quotation selector', async () => {
    render(<Editor initial={[{ code: 'sz000001', name: '', qty: 100, note: '' }]} />)
    const select = await screen.findByLabelText('000001 行情')
    await waitFor(() => expect(within(select).getAllByRole('option').length).toBe(2))
    expect(within(select).getByRole('option', { name: /000001.SZ/ })).toBeTruthy()
    expect(within(select).queryByRole('option', { name: /sh000001/ })).toBeNull()
  })

  it('ignores an in-flight estimate after quantities change', async () => {
    let finish: ((value: typeof result) => void) | undefined
    const defaults = mockApi.getMockImplementation()!
    mockApi.mockImplementation((...args) => args[0].endsWith('/preview')
      ? new Promise(resolve => { finish = resolve }) : defaults(...args))
    render(<Editor initial={[{ code: '000001', name: '', qty: 200, note: '', price: '10' }]} />)
    await screen.findByRole('button', { name: '复制当前基准持仓到草稿' })
    fireEvent.click(screen.getByRole('button', { name: '检查预演现金与费用' }))
    fireEvent.change(screen.getByLabelText('预演数量'), { target: { value: '300' } })
    finish!(result)
    await screen.findByRole('button', { name: '检查预演现金与费用' })
    expect(screen.queryByRole('region', { name: '预演检查结果' })).toBeNull()
  })
})
