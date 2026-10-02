import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SettingsWorkspace } from './settings-workspace'
vi.mock('./provider-health', () => ({ ProviderHealth: () => null }))
import { MarketEditor } from './market'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
vi.mock('./market-diagnostics', () => ({ MarketDiagnostics: () => null }))
vi.mock('./market-close', () => ({ MarketCloseLookup: () => null }))
vi.mock('./stock-search', () => ({ StockSearch: () => null }))
const initial = { group: 'market_sources', scope: 'global_data_directory', account_id: null, account_name: null, account_kind: null,
  revision: 3, value: { provider: 'auto', provider_order: ['akshare', 'baostock'] }, notes: [], readonly: false }
const props = { accountId: 'a', accountKind: 'real', themeMode: 'dark' as const, density: 'compact' as const, onThemeChange: vi.fn(), onDensityChange: vi.fn() }
beforeEach(() => { call.mockReset(); localStorage.clear(); window.history.replaceState(null, '', '/'); props.onThemeChange.mockReset(); props.onDensityChange.mockReset() })

it('does not import legacy source on mount; preview binds the confirmed revision', async () => {
  localStorage.setItem('trade-market-provider', 'baostock')
  const preview = { expected_revision: 3, operation: 'update', before: initial.value, after: { provider: 'baostock', provider_order: ['baostock', 'akshare'] },
    diff: [{ path: 'provider', before: 'auto', after: 'baostock' }], preview_sha256: 'hash', notes: [] }
  call.mockImplementation((path: string) => Promise.resolve(path.endsWith('/preview') ? preview : path.endsWith('/import') ? { ...initial, revision: 4, value: preview.after } : initial))
  render(<SettingsWorkspace {...props} />)
  await screen.findByText(/版本 3/)
  expect(call.mock.calls.every(([, method]) => method === undefined)).toBe(true)
  fireEvent.click(screen.getByText('迁入旧本机行情来源偏好'))
  fireEvent.click(screen.getByRole('button', { name: '预览旧来源偏好' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认迁入旧来源偏好' }))
  await screen.findByText('旧来源偏好已迁入全局配置，本机原键仍保留。')
  expect(call).toHaveBeenCalledWith('/settings/groups/market_sources/import', 'POST', { expected_revision: 3, preview_sha256: 'hash', value: preview.after })
  expect(localStorage.getItem('trade-market-provider')).toBe('baostock')
})

it('default reset shows diff before write and stale revisions keep unsaved values', async () => {
  call.mockImplementation((path: string) => {
    if (path.endsWith('/defaults-preview')) return Promise.resolve({ expected_revision: 3, operation: 'defaults', before: initial.value,
      after: { ...initial.value, provider_order: ['baostock', 'akshare'] }, diff: [{ path: 'provider_order', before: ['akshare', 'baostock'], after: ['baostock', 'akshare'] }], preview_sha256: 'hash' })
    if (path.endsWith('/reset')) return Promise.reject(Object.assign(new Error('stale'), { code: 'SETTINGS_REVISION_CONFLICT' }))
    return Promise.resolve({ ...initial, revision: call.mock.calls.some(([path]) => String(path).endsWith('/reset')) ? 4 : 3 })
  })
  render(<SettingsWorkspace {...props} />)
  await screen.findByText(/版本 3/)
  fireEvent.change(screen.getByLabelText('默认在线来源'), { target: { value: 'akshare' } })
  fireEvent.click(screen.getByRole('button', { name: '预览本组默认值' }))
  await screen.findByRole('button', { name: '确认仅恢复此组默认值' })
  expect(call.mock.calls.some(([path]) => String(path).endsWith('/reset'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认仅恢复此组默认值' }))
  await screen.findByText('服务端已有版本 4')
  expect((screen.getByLabelText('默认在线来源') as HTMLSelectElement).value).toBe('akshare')
  expect((screen.getByRole('button', { name: '保存本组设置' }).closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true)
})

it('account change remounts only account editor and never carries draft into another account', async () => {
  call.mockImplementation((path: string) => Promise.resolve(path.includes('/fees?') ? { ...initial, group: 'fees', scope: 'account', account_id: path.endsWith('a') ? 'a' : 'b',
    account_name: path.endsWith('a') ? 'Account A' : 'Account B', account_kind: 'real', value: { commission_rate: '.0003', minimum_commission: '5', sell_stamp_rate: '.001', transfer_rate: '.00001' } } : initial))
  const ui = render(<SettingsWorkspace {...props} />)
  fireEvent.click(screen.getByRole('tab', { name: '账户费用' }))
  await screen.findByText(/当前账户：Account A/)
  fireEvent.change(screen.getByLabelText('佣金率'), { target: { value: '.0008' } })
  ui.rerender(<SettingsWorkspace {...props} accountId="b" />)
  await screen.findByText(/当前账户：Account B/)
  expect((screen.getByLabelText('佣金率') as HTMLInputElement).value).toBe('.0003')
  expect(call.mock.calls.some(([, method]) => method === 'PUT')).toBe(false)
})

it('browser display defaults reject changes made in another tab after preview', async () => {
  call.mockResolvedValue(initial)
  localStorage.setItem('trade-theme-mode', 'dark'); localStorage.setItem('trade-list-density', 'compact')
  render(<SettingsWorkspace {...props} />)
  fireEvent.click(screen.getByRole('tab', { name: '显示与密度' }))
  fireEvent.click(screen.getByRole('button', { name: '预览显示默认值' }))
  localStorage.setItem('trade-theme-mode', 'system')
  fireEvent.click(screen.getByRole('button', { name: '确认仅恢复显示默认值' }))
  await screen.findByText(/本机显示设置已变化/)
  expect(props.onThemeChange).not.toHaveBeenCalled()
  expect(localStorage.getItem('trade-theme-mode')).toBe('system')
})

it('actual market batch requests use global server order while preserving old local keys', async () => {
  localStorage.setItem('trade-market-preferred-provider', 'baostock')
  call.mockImplementation((path: string, method?: string) => {
    if (path === '/settings/groups/market_sources') return Promise.resolve(initial)
    if (path === '/market/sync-jobs' && method === 'POST') return Promise.resolve({ id: 'new', state: 'queued', total: 1, completed: 0, symbols: ['600000'], provider: 'auto', results: [] })
    return Promise.resolve([])
  })
  render(<MarketEditor accountId="a" accountKind="real" />)
  fireEvent.click(screen.getByRole('button', { name: '在线同步' }))
  await waitFor(() => expect((screen.getByLabelText('自动优先来源') as HTMLSelectElement).value).toBe('akshare'))
  fireEvent.change(screen.getByLabelText('证券代码（逗号、空格或换行分隔）'), { target: { value: '600000' } })
  fireEvent.click(screen.getByRole('button', { name: '提交批量任务' }))
  await waitFor(() => expect(call).toHaveBeenCalledWith('/market/sync-jobs', 'POST', expect.objectContaining({ provider: 'auto', provider_order: ['akshare', 'baostock'] })))
  expect(localStorage.getItem('trade-market-preferred-provider')).toBe('baostock')
})
