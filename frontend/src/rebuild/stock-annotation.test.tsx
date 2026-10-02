import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StockAnnotationEditor } from './stock-annotation'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const saved = { symbol: 'sh600000', start_date: '2025-01-02', stage: 'Early', trend_class: 'Unknown', decision: '保留', notes: '正式标注', revision: 1, updated_at: '2025-01-02' }
beforeEach(() => { call.mockReset(); localStorage.clear() })

it('persists drafts by market identity and restores after switching symbols', async () => {
  call.mockResolvedValue(saved)
  const ui = render(<StockAnnotationEditor symbol="sh600000" lastDate="2025-01-02" />)
  const notes = () => screen.getByLabelText('人工备注') as HTMLTextAreaElement
  await waitFor(() => expect(notes().value).toBe('正式标注'))
  fireEvent.change(notes(), { target: { value: '离开前未保存的草稿' } })
  ui.rerender(<StockAnnotationEditor symbol="sz000001" lastDate="2025-01-02" />)
  await waitFor(() => expect(notes().value).toBe('正式标注'))
  ui.rerender(<StockAnnotationEditor symbol="600000.SH" lastDate="2025-01-02" />)
  await waitFor(() => expect(notes().value).toBe('离开前未保存的草稿'))
  expect(call.mock.calls.some(([, method]) => method === 'PUT')).toBe(false)
})

it('does not overwrite a newer server annotation without explicit reconciliation', async () => {
  localStorage.setItem('trade-rebuild:stock-annotation-draft:sh600000', JSON.stringify({ form: { ...saved, notes: '旧版本本机草稿' }, base_revision: 1 }))
  call.mockResolvedValue({ ...saved, notes: '他处修改后的标注', revision: 2 })
  render(<StockAnnotationEditor symbol="600000" lastDate="2025-01-02" />)
  await screen.findByRole('button', { name: '以最新版本继续编辑本机草稿' })
  expect((screen.getByRole('button', { name: '保存人工标注' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '以最新版本继续编辑本机草稿' }))
  call.mockResolvedValue({ ...saved, notes: '旧版本本机草稿', revision: 3 })
  fireEvent.click(screen.getByRole('button', { name: '保存人工标注' }))
  await waitFor(() => expect(call).toHaveBeenCalledWith('/market/annotations/600000', 'PUT', expect.objectContaining({ expected_revision: 2, notes: '旧版本本机草稿' })))
  await waitFor(() => expect(localStorage.getItem('trade-rebuild:stock-annotation-draft:sh600000')).toBeNull())
})

it('late reads cannot replace the next selected symbol or its manual chart marker', async () => {
  let finish: (value: unknown) => void = () => undefined
  call.mockImplementation((path: string) => path.endsWith('/600000') ? new Promise(resolve => { finish = resolve }) : Promise.resolve({ ...saved, symbol: 'sz000001', notes: '第二只证券' }))
  const marker = vi.fn()
  const ui = render(<StockAnnotationEditor symbol="600000" lastDate="2025-01-02" onStartDate={marker} />)
  ui.rerender(<StockAnnotationEditor symbol="000001" lastDate="2025-01-02" onStartDate={marker} />)
  await waitFor(() => expect((screen.getByLabelText('人工备注') as HTMLTextAreaElement).value).toBe('第二只证券'))
  finish({ ...saved, start_date: '2025-01-01' })
  await Promise.resolve()
  expect(marker).not.toHaveBeenCalledWith('2025-01-01')
})
