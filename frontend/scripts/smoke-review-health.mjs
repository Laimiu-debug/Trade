import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-review-health-ui-'))
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

  const account = await api('/accounts', 'POST', { name: '提醒与统计验收' })
  const root = '/accounts/' + account.id
  await api(root + '/cash-flows', 'POST', { flow_date: '2025-01-01', kind: 'initial', amount: '10000' })
  await api(root + '/trades', 'POST', { trade_date: '2025-01-02', symbol: '600000', side: 'buy', quantity: 100, price: '10' })
  await api(root + '/snapshots/2025-01-03', 'PUT', { snap_date: '2025-01-03', total_assets: '12345', expected_revision: 0 })
  await page.goto(`${base}/rebuild.html?page=overview&account=${account.id}`)
  const reminder = page.getByRole('region', { name: '记录遗漏提醒' })
  await expect(reminder.getByRole('row').filter({ hasText: '2025-01-03' }).getByRole('button', { name: '填写复盘' })).toBeVisible()
  await reminder.getByRole('row').filter({ hasText: '2025-01-03' }).getByRole('button', { name: '填写复盘' }).click()
  await expect(page.getByLabel('复盘日期', { exact: true })).toHaveValue('2025-01-03')
  await page.getByRole('button', { name: '资产快照', exact: true }).click()
  const confirmation = page.locator('section').filter({ has: page.getByRole('heading', { name: '确认资产', exact: true }) })
  await page.getByRole('row').filter({ hasText: '2025-01-03' }).getByRole('button', { name: '编辑', exact: true }).click()
  await expect(confirmation.getByLabel('总资产', { exact: true })).toHaveValue('12345.00')
  await page.getByRole('button', { name: '总览', exact: true }).click()
  await reminder.getByRole('row').filter({ hasText: '2025-01-02' }).filter({ has: page.getByRole('button', { name: '核对资产' }) }).getByRole('button', { name: '核对资产' }).click()
  await expect(confirmation.getByLabel('日期', { exact: true })).toHaveValue('2025-01-02')
  await expect(confirmation.getByLabel('总资产', { exact: true })).toHaveValue('')
  expect((await api(root + '/snapshots')).length).toBe(1)

  const sim = await api('/sim-accounts', 'POST', { name: 'FIFO月份归属', initial_capital: '100000', start_date: '2025-01-30' })
  const simPath = '/sim-accounts/' + sim.id
  async function fill(day, side, price) {
    const order = await api(simPath + '/orders', 'POST', { symbol: '600000', side, quantity: 100, limit_price: price, signal_date: day, submit_date: day })
    return await api(simPath + '/orders/' + order.id + '/fill', 'POST', { expected_revision: 1, fill_date: day, fill_price: price })
  }
  await fill('2025-01-30', 'buy', '10')
  await api(simPath + '/settle', 'POST', { to_date: '2025-02-03' })
  await fill('2025-02-03', 'sell', '12')
  await page.goto(`${base}/rebuild.html?page=simulation&simulation-view=review&account=${sim.id}`)
  const perf = page.locator('section').filter({ has: page.getByRole('heading', { name: '模拟已实现交易统计', exact: true }) })
  await expect(perf.getByRole('cell', { name: '2025-02', exact: true })).toBeVisible()
  await perf.getByLabel('月份归属日期').selectOption('buy')
  await expect(perf.getByRole('cell', { name: '2025-01', exact: true })).toBeVisible()
  await perf.getByRole('button', { name: '2025-02-03' }).click()
  await expect(perf.getByText(/含买入费用成本/)).toBeVisible()

  let probes = 0
  await page.route('**/api/v1/market/providers/akshare/probe', async route => {
    probes++
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: {
      status: probes === 1 ? 'ok' : 'failed', symbol: 'sh600000', checked_at: '2025-01-15T10:00:00Z', elapsed_ms: 10,
      sample_count: 2, ...(probes === 1 ? {} : { error_code: 'MARKET_PROVIDER_TIMEOUT', message: '受控超时' }) } }) })
  })
  await page.goto(`${base}/rebuild.html?page=settings&settings_group=market_sources&account=${account.id}`)
  const health = page.getByRole('region', { name: '行情能力与连通性' })
  await expect(health.getByRole('heading', { name: '行情能力与连通性' })).toBeVisible()
  expect(probes).toBe(0)
  await health.getByRole('button', { name: '联网测试 AKShare / 东方财富', exact: true }).click()
  await expect(health.getByText('本次日线样本有效', { exact: true })).toBeVisible()
  await health.getByRole('button', { name: '联网测试 AKShare / 东方财富', exact: true }).click()
  await expect(health.getByText('测试失败', { exact: true })).toBeVisible()
  expect(probes).toBe(2)
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  expect(errors).toEqual([]); expect(external).toEqual([])
  console.log(JSON.stringify({ status: 'passed', dataDir, assertions: ['review_date_navigation', 'snapshot_clears_other_day', 'read_only_reminders', 'FIFO_buy_month', 'explicit_provider_probe', 'provider_failure', '320px', 'no_external_network'] }))
} finally {
  await browser?.close()
  server.kill()
  await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); setTimeout(resolve, 5000).unref() })
}
