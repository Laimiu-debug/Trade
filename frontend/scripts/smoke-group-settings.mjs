import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-settings-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const base = `http://127.0.0.1:${port}`
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: fileURLToPath(new URL('../../backend', import.meta.url)), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port) },
})
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', value => { logs = (logs + value.toString()).slice(-12000) })
try {
  for (let count = 0; count < 100; count++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded owned server startup */ }
    if (server.exitCode !== null || count === 99) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = [], external = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (!request.url().startsWith(base)) external.push(request.url()) })
  let csrf = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  page.on('response', async response => { if (response.url().endsWith('/api/v1/session') && response.ok()) csrf = (await response.json()).data.csrf_token })
  const api = async (url, method = 'GET', data) => {
    const response = await page.request.fetch(base + '/api/v1' + url, { method, data,
      headers: method === 'GET' ? {} : { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() } })
    if (!response.ok()) throw new Error(`${url} ${response.status()} ${await response.text()}`)
    return (await response.json()).data
  }
  const a = await api('/accounts', 'POST', { name: '设置隔离账户 A' })
  const b = await api('/accounts', 'POST', { name: '设置隔离账户 B' })
  const fees = { commission_rate: '0.0008', minimum_commission: '9.00', sell_stamp_rate: '0.001', transfer_rate: '0.00001' }
  await api(`/settings/groups/fees?account_id=${a.id}`, 'PUT', { expected_revision: 1, value: fees })
  await api(`/settings/groups/fees?account_id=${b.id}`, 'PUT', { expected_revision: 1, value: fees })
  await api(`/settings/groups/targets?account_id=${a.id}`, 'PUT', { expected_revision: 1, value: { multiplier: '1.5', node_count: 10 } })
  const aiBefore = await api('/settings/groups/ai')
  await api('/settings/groups/ai', 'PUT', { expected_revision: 0, value: { ...aiBefore.value, text: { base_url: 'http://127.0.0.1:9/v1', model: 'local-no-request', secret_ref: '' } } })
  await api('/settings/groups/calendar', 'PUT', { expected_revision: 0, value: { source: '仅隔离测试日历', start_date: '2025-01-01', end_date: '2025-01-02', days: [{ date: '2025-01-01', is_open: false }, { date: '2025-01-02', is_open: true }] } })
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('settings-smoke-initialized')) {
      localStorage.setItem('trade-market-provider', 'auto'); localStorage.setItem('trade-market-preferred-provider', 'akshare')
      localStorage.setItem('trade-theme-mode', 'dark'); localStorage.setItem('trade-list-density', 'compact')
      sessionStorage.setItem('settings-smoke-initialized', 'true')
    }
  })
  await page.goto(`${base}/rebuild.html?page=settings&account=${a.id}`)
  const workspace = page.locator('.settings-workspace')
  const panel = name => workspace.getByRole('tabpanel', { name, exact: true })
  const tab = name => workspace.getByRole('tab', { name, exact: true }).click()
  await expect(panel('行情来源').getByText(/版本 0/)).toBeVisible()
  expect((await api('/settings/groups/market_sources')).revision).toBe(0)
  await panel('行情来源').getByText('迁入旧本机行情来源偏好', { exact: true }).click()
  await panel('行情来源').getByRole('button', { name: '预览旧来源偏好' }).click()
  await expect(panel('行情来源').getByText(/旧来源迁入预览/)).toBeVisible()
  expect((await api('/settings/groups/market_sources')).revision).toBe(0)
  await panel('行情来源').getByRole('button', { name: '确认迁入旧来源偏好' }).click()
  await expect(panel('行情来源').getByText(/旧来源偏好已迁入全局配置/)).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('trade-market-preferred-provider'))).toBe('akshare')
  await page.getByRole('button', { name: '行情样本', exact: true }).click()
  await page.getByRole('navigation', { name: '行情页面' }).getByRole('button', { name: '在线同步', exact: true }).click()
  await expect(page.getByLabel('自动优先来源')).toHaveValue('akshare')
  let captured = null
  await page.route('**/api/v1/market/online-sync', async route => {
    captured = route.request().postDataJSON()
    await route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: { code: 'SMOKE_CAPTURE', message: '隔离测试仅核对请求，不联网' } }) })
  })
  await page.getByLabel('在线同步证券代码').fill('600000')
  await page.getByRole('button', { name: '同步在线日线', exact: true }).click()
  await expect.poll(() => captured?.provider_order).toEqual(['akshare', 'baostock'])
  await page.getByRole('button', { name: '系统设置', exact: true }).click()
  await tab('账户费用')
  await expect(panel('账户费用').getByLabel('佣金率')).toHaveValue('0.0008')
  await panel('账户费用').getByRole('button', { name: '预览本组默认值' }).click()
  await expect(panel('账户费用').getByRole('button', { name: '确认仅恢复此组默认值' })).toBeVisible()
  expect((await api(`/settings/groups/fees?account_id=${a.id}`)).revision).toBe(2)
  await panel('账户费用').getByRole('button', { name: '确认仅恢复此组默认值' }).click()
  await expect(panel('账户费用').getByLabel('佣金率')).toHaveValue('0.0003')
  expect((await api(`/settings/groups/fees?account_id=${b.id}`)).value.commission_rate).toBe('0.0008')
  expect((await api(`/settings/groups/targets?account_id=${a.id}`)).value.multiplier).toBe('1.5')
  await tab('AI 模型')
  await expect(panel('AI 模型').getByLabel('文本 · 模型名称')).toHaveValue('local-no-request')
  await panel('AI 模型').getByRole('button', { name: '预览本组默认值' }).click()
  await panel('AI 模型').getByRole('button', { name: '确认仅恢复此组默认值' }).click()
  await expect(panel('AI 模型').getByLabel('文本 · 模型名称')).toHaveValue('')
  await tab('本地日历')
  await expect(panel('本地日历').getByLabel('本地日历内容')).toContainText('2025-01-02')
  await panel('本地日历').getByRole('button', { name: '预览本组默认值' }).click()
  await panel('本地日历').getByRole('button', { name: '确认仅恢复此组默认值' }).click()
  await expect(panel('本地日历').getByText('仅此设置组已恢复默认。')).toBeVisible()
  expect((await api('/market/trading-calendar/plan-date?written_on=2025-01-01')).suggested_date).toBe(null)
  expect((await api('/market/trading-calendar')).revision).toBe(2)
  await tab('显示与密度')
  await panel('显示与密度').getByRole('button', { name: '预览显示默认值' }).click()
  await panel('显示与密度').getByRole('button', { name: '确认仅恢复显示默认值' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable')
  await tab('存储与备份')
  await expect(panel('存储与备份').getByRole('heading', { name: '数据与备份', exact: true })).toBeVisible()
  await page.reload()
  await expect(panel('存储与备份').getByRole('heading', { name: '数据与备份', exact: true })).toBeVisible()
  expect(new URL(page.url()).searchParams.get('settings_group')).toBe('storage')
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(errors).toEqual([]); expect(external).toEqual([])
  console.log(JSON.stringify({ ok: true, dataDir, group_reset_preview: true, account_isolation: true, local_legacy_preserved: true,
    actual_market_request_uses_global_order: true, no_provider_network: true, calendar_reset_unknown: true, local_display_reset: true, storage_reload_kept: true, mobile320: true }))
} finally {
  if (browser) await browser.close()
  server.kill()
  await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit', resolve))
}
