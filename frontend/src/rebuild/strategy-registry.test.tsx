import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { StrategyRegistry } from './strategy-registry'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const base = { revision: 2, catalog_sha256: 'catalog', enabled_ids: ['one', 'two'], default_strategy_id: 'one', defaults: { enabled_ids: ['one', 'two'], default_strategy_id: 'one' }, notes: ['已冻结任务继续执行'], strategies: [
  { id: 'one', name: '策略一', version: '1', execution_entry: 'single_symbol', enabled_in_legacy: true, is_legacy_default: true, description: '说明一', capabilities: { supports_matrix: false }, playbook: { intent: '条件验证' }, params_schema: {} },
  { id: 'two', name: 'B1 策略', version: '1', execution_entry: 'b1_scanner', enabled_in_legacy: true, is_legacy_default: false, description: '说明二', capabilities: {}, playbook: {}, params_schema: {} },
] }

beforeEach(() => {
  call.mockReset()
  call.mockImplementation((path: string, method?: string, body?: Record<string, unknown>) => {
    if (path === '/research/registry' && !method) return Promise.resolve(base)
    if (path.endsWith('/preview')) return Promise.resolve({ revision: 2, preview_sha256: 'frozen-diff', before: { enabled_ids: base.enabled_ids, default_strategy_id: 'one' }, after: body, diff: [{ strategy_id: 'two', name: 'B1 策略', before_enabled: true, after_enabled: false }], default_changed: false, notes: ['已冻结任务继续执行'] })
    if (path === '/research/registry' && method === 'PUT') return Promise.resolve({ ...base, ...body, revision: 3 })
    throw new Error(path)
  })
})

it('reads without saving and requires preview before a revision-bound change', async () => {
  render(<StrategyRegistry />)
  fireEvent.click(await screen.findByLabelText('启用策略 two'))
  expect(call.mock.calls.every(row => !row[1])).toBe(true)
  expect(screen.queryByRole('button', { name: '确认保存策略设置' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '预览策略设置差异' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认保存策略设置' }))
  await screen.findByText('策略设置已保存 · 版本 3')
  expect(call).toHaveBeenCalledWith('/research/registry', 'PUT', { enabled_ids: ['one'], default_strategy_id: 'one', expected_revision: 2, expected_preview_sha256: 'frozen-diff' })
})

it('does not silently switch the default when its strategy is disabled', async () => {
  render(<StrategyRegistry />)
  fireEvent.click(await screen.findByLabelText('启用策略 one'))
  expect((screen.getByRole('button', { name: '预览策略设置差异' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('新建默认策略'), { target: { value: 'two' } })
  fireEvent.click(screen.getByRole('button', { name: '预览策略设置差异' }))
  await screen.findByRole('button', { name: '确认保存策略设置' })
  fireEvent.click(screen.getByRole('button', { name: '填入策略默认值' }))
  expect(screen.queryByRole('button', { name: '确认保存策略设置' })).toBeNull()
  expect(call.mock.calls.some(row => row[1] === 'PUT')).toBe(false)
})

it('keeps edits on a concurrent revision conflict and requires a new preview', async () => {
  call.mockImplementation((path: string, method?: string) => {
    if (!method) return Promise.resolve(base)
    if (path.endsWith('/preview')) return Promise.resolve({ revision: 2, preview_sha256: 'old', before: base, after: { enabled_ids: ['one'], default_strategy_id: 'one' }, diff: [], notes: [] })
    return Promise.reject(new Error('策略设置版本已变化，请重新读取并预览'))
  })
  render(<StrategyRegistry />)
  fireEvent.click(await screen.findByLabelText('启用策略 two'))
  fireEvent.click(screen.getByRole('button', { name: '预览策略设置差异' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认保存策略设置' }))
  await screen.findByRole('alert')
  expect((screen.getByLabelText('启用策略 two') as HTMLInputElement).checked).toBe(false)
  expect(screen.queryByRole('button', { name: '确认保存策略设置' })).toBeNull()
})
