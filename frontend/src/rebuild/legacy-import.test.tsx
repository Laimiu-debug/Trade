import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { LegacyImportWorkspace } from './legacy-import'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
vi.mock('./legacy-supplements', () => ({ LegacySupplementEditor: () => null }))
vi.mock('./legacy-attachments', () => ({ LegacyAttachmentEditor: () => null }))
vi.mock('./legacy-sim-promotion', () => ({ LegacySimPromotion: () => null }))
const preview = { source: 'laimiu_json', filename: 'fixture.json', source_sha256: 'sourcehash', logical_sha256: 'logicalhash', source_bytes: 20,
  redacted_paths: ['/settings/0/value'], mode: 'new_real_account', account_name: '旧实盘账本副本',
  inventory: [{ section: 'trades', count: 1, target: 'trades', field_map: { code: 'symbol' }, note: '' }],
  records: [{ section: 'trades', source_id: '1', data: { symbol: 'sh600000' } }], errors: [], notes: ['旧研究只读'], mapped_record_count: 1, can_import: true, preview_sha256: 'previewhash' }

beforeEach(() => { call.mockReset(); call.mockResolvedValue([]) })

async function chooseFile() {
  const file = new File(['{}'], 'fixture.json', { type: 'application/json' })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('{}').buffer })
  fireEvent.change(screen.getByLabelText('旧资料文件'), { target: { files: [file] } })
  await screen.findByText('已选择：fixture.json，尚未保存。')
}

it('never imports on file selection or preview; explicit acknowledged save binds hash', async () => {
  const imported = vi.fn()
  call.mockImplementation((path: string, method?: string) => Promise.resolve(path.endsWith('/preview') ? preview : method === 'POST' ? { id: 'saved', account_id: 'new-account', account_name: '旧实盘账本副本' } : path.endsWith('/saved') ? { id: 'saved', filename: 'fixture.json', source_kind: 'laimiu_json', account_id: 'new-account', revision: 1, promoted: false, preview, archive: { trades: [] }, mappings: [] } : []))
  render(<LegacyImportWorkspace onImported={imported} />)
  await chooseFile()
  expect(call.mock.calls.every(([, method]) => method !== 'POST')).toBe(true)
  fireEvent.change(screen.getByLabelText('导入方式'), { target: { value: 'new_real_account' } })
  fireEvent.click(screen.getByRole('button', { name: '预览字段与来源映射' }))
  const confirm = await screen.findByRole('button', { name: '确认导入此预览' })
  expect((confirm as HTMLButtonElement).disabled).toBe(true)
  expect(call.mock.calls.filter(([path, method]) => path === '/legacy-imports' && method === 'POST')).toHaveLength(0)
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(confirm)
  await waitFor(() => expect(imported).toHaveBeenCalledWith('new-account'))
  const args = call.mock.calls.find(([path, method]) => path === '/legacy-imports' && method === 'POST')!
  expect(args[2].expected_preview_sha256).toBe('previewhash')
  expect(args[2].acknowledge_limitations).toBe(true)
  expect((screen.getByRole('button', { name: '预览字段与来源映射' }) as HTMLButtonElement).disabled).toBe(true)
})

it('editing name invalidates preview and validation errors cannot be confirmed', async () => {
  call.mockImplementation((path: string) => Promise.resolve(path.endsWith('/preview') ? { ...preview, can_import: false, errors: [{ section: 'trades', source_id: '1', message: '金额精度无效' }] } : []))
  render(<LegacyImportWorkspace />)
  await chooseFile()
  fireEvent.change(screen.getByLabelText('导入方式'), { target: { value: 'new_real_account' } })
  fireEvent.click(screen.getByRole('button', { name: '预览字段与来源映射' }))
  await screen.findByText('trades / 1：金额精度无效')
  expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('新账户名称'), { target: { value: '新名字' } })
  expect(screen.queryByRole('button', { name: '确认导入此预览' })).toBeNull()
})

it('promotes an archived Laimiu source only after a revision-bound second preview', async () => {
  const imported = vi.fn()
  const archived = { id: 'archive', filename: 'fixture.json', source_kind: 'laimiu_json', account_id: null, account_name: null, revision: 1, promoted: false, source_sha256: 'hash', logical_sha256: 'logical', mapped_record_count: 0, redaction_count: 1, mode: 'archive_only', created_at: 'today' }
  let promoted = false
  call.mockImplementation((path: string, method?: string) => {
    if (path.endsWith('/promotion-preview')) return Promise.resolve({ ...preview, promotion_of: 'archive', expected_revision: 1, account_name: '旧档案转换账户' })
    if (path.endsWith('/promote') && method === 'POST') { promoted = true; return Promise.resolve({ ...archived, account_id: 'converted', account_name: '旧档案转换账户', revision: 2, promoted: true }) }
    const current = promoted ? { ...archived, account_id: 'converted', account_name: '旧档案转换账户', revision: 2, promoted: true } : archived
    return Promise.resolve(path === '/legacy-imports' ? [current] : { ...current, preview, archive: { trades: [] }, mappings: [] })
  })
  render(<LegacyImportWorkspace onImported={imported} />)
  fireEvent.click(await screen.findByRole('button', { name: '查看档案' }))
  fireEvent.click(await screen.findByRole('button', { name: '预览此档案转换' }))
  const confirm = await screen.findByRole('button', { name: '确认从档案建立新账户' })
  expect((confirm as HTMLButtonElement).disabled).toBe(true)
  expect(promoted).toBe(false)
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(confirm)
  await waitFor(() => expect(imported).toHaveBeenCalledWith('converted'))
  expect(call).toHaveBeenCalledWith('/legacy-imports/archive/promote', 'POST', { expected_revision: 1, account_name: '旧档案转换账户', expected_preview_sha256: 'previewhash', acknowledge_limitations: true })
  expect(screen.queryByRole('button', { name: '预览此档案转换' })).toBeNull()
})
