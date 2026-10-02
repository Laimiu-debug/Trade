import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { LegacySimPromotion } from './legacy-sim-promotion'
import { LegacyAttachmentEditor } from './legacy-attachments'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const sim = { account_name: '旧模拟独立副本', expected_revision: 2, preview_sha256: 'hash', can_import: true,
  notes: ['初始日期未知'], errors: [], summary: { fill_count: 2, open_lot_count: 1, closed_allocation_count: 1, archived_terminal_order_count: 0, cash: '9189.40', cost_basis: '1002.50', as_of_date: '2025-01-06' }, records: {}, source_sha256: 'source', logical_sha256: 'logical' }
const item = { key: 'attachment:1:0', source_path: 'D:/private/chart.png', review_date: '2025-01-02', status: 'needs_file', reason: '' }
const catalog = { expected_revision: 2, account_id: 'new-real', items: [item], notes: ['旧路径从不读取'] }
const attachment = { ...catalog, preview_sha256: 'imagehash', can_apply: true, selected: [{ ...item, target_revision: 1, existing: [], file: { filename: 'local.png', sha256: 'byteshash', byte_size: 3, mime_type: 'image/png', width: 1, height: 1 } }] }
beforeEach(() => { call.mockReset() })

it('simulation cannot create without exact reviewed snapshot and acknowledgement', async () => {
  const imported = vi.fn()
  call.mockImplementation((path: string) => Promise.resolve(path.endsWith('/preview') ? sim : { account_id: 'new-sim' }))
  render(<LegacySimPromotion importId="old" revision={2} onChanged={async () => {}} onImported={imported} />)
  expect(call).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '核验旧模拟账本并预览' }))
  const confirm = await screen.findByRole('button', { name: '确认创建独立模拟账户' })
  expect((confirm as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(confirm)
  await waitFor(() => expect(imported).toHaveBeenCalledWith('new-sim'))
  expect(call).toHaveBeenCalledWith('/legacy-imports/old/simulation/apply', 'POST', { expected_revision: 2, account_name: sim.account_name, expected_preview_sha256: 'hash', acknowledge_limitations: true })
})

it('malformed legacy simulation cannot be accepted and edited account name clears snapshot', async () => {
  call.mockResolvedValue({ ...sim, can_import: false, records: null, errors: [{ section: 'ledger', source_id: '', message: '现金不守恒' }] })
  render(<LegacySimPromotion importId="old" revision={2} onChanged={async () => {}} />)
  fireEvent.click(screen.getByRole('button', { name: '核验旧模拟账本并预览' }))
  await screen.findByText(/现金不守恒/)
  expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('新模拟账户名称'), { target: { value: '另一个' } })
  expect(screen.queryByRole('button', { name: '确认创建独立模拟账户' })).toBeNull()
})

it('selecting a file never reads old path or saves; conflicting apply retains exact selected file', async () => {
  call.mockImplementation((path: string) => path.endsWith('/apply') ? Promise.reject(new Error('日复盘版本已变化')) : Promise.resolve(path.endsWith('/preview') ? attachment : catalog))
  render(<LegacyAttachmentEditor importId="old" onChanged={async () => {}} />)
  const field = await screen.findByLabelText('为 attachment:1:0 选择图片')
  const file = new File(['abc'], 'local.png', { type: 'image/png' })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('abc').buffer })
  fireEvent.change(field, { target: { files: [file] } })
  await screen.findByText(/local.png · 3 B/)
  expect(call.mock.calls.every(([, method]) => method === undefined)).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '预览所选图片映射' }))
  const confirm = await screen.findByRole('button', { name: '确认迁入这批图片' })
  expect((confirm as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(confirm)
  await screen.findByText('日复盘版本已变化')
  expect(screen.getByText(/local.png · 3 B/)).toBeDefined()
  expect(call.mock.calls.find(([path]) => path.endsWith('/apply'))?.[2]).toMatchObject({ expected_revision: 2, expected_preview_sha256: 'imagehash', files: [{ source_key: 'attachment:1:0', filename: 'local.png', content_base64: 'YWJj' }] })
})
