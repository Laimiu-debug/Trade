import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-sim-equity-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: backend, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port) },
})
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { logs = (logs + data.toString()).slice(-12000) })
try {
  const base = `http://127.0.0.1:${port}`
  for (let index = 0; index < 100; index++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded startup */ }
    if (server.exitCode !== null || index === 99) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 }, timezoneId: 'Asia/Shanghai' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  let token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  let serial = 0
  async function post(url, body) {
    const response = await page.request.post(base + '/api/v1' + url, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `equity-ui-${++serial}` } })
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  async function get(url) { const response = await page.request.get(base + '/api/v1' + url); assert.equal(response.status(), 200, await response.text()); return (await response.json()).data }
  const account = await post('/sim-accounts', { name: '资产曲线隔离验证', initial_capital: '10000', start_date: '2025-03-28' })
  const root = `/sim-accounts/${account.id}`
  await post(root + '/settle', { to_date: '2025-03-29' })
  const order = await post(root + '/orders', { symbol: 'sh600000', side: 'buy', quantity: 100, limit_price: '10', signal_date: '2025-03-28', submit_date: '2025-03-29' })
  await post(root + '/orders/' + order.id + '/fill', { expected_revision: order.revision, fill_date: '2025-03-29', fill_price: '10' })
  await post(root + '/settle', { to_date: '2025-03-31' })
  const closes = [...Array.from({ length: 60 }, (_, index) => Number((8 + index * 2 / 59 - (index % 7 === 3 ? .1 : 0)).toFixed(4))), ...Array(25).fill(10), 10.3, 10.6, 10.9]
  const bars = closes.map((close, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    return { event_date: day, open: (close - .05).toFixed(4), high: (close + .1).toFixed(4), low: (close - .1).toFixed(4), close: close.toFixed(4), volume: index === 87 ? 3000 : index === 0 || close >= closes[index - 1] ? 1200 : 1000, available_at: day + 'T08:00:00Z' }
  })
  for (const [index, close] of [9, 12, 9999].entries()) {
    const day = new Date(Date.UTC(2025, 2, 30 + index)).toISOString().slice(0, 10)
    bars.push({ event_date: day, open: String(close), high: String(close), low: String(close), close: String(close), volume: 1000, available_at: day + 'T08:00:00Z' })
  }
  const dataset = await post('/market/datasets', { symbol: '600000.SH', adjustment: 'none', bars })
  const research = await post('/research/runs', { dataset_id: dataset.id, strategy_id: 'wulong_cluster_v1', decision_at: '2025-03-29T09:00:00Z', strict: true, params: {} })
  assert.equal(research.result.signal, true)
  await page.goto(base + '/rebuild.html?page=simulation&simulation-view=review')
  await page.getByLabel('账户', { exact: true }).selectOption(account.id)
  await expect(page.getByRole('heading', { name: '模拟资产与回撤曲线' })).toBeVisible()
  // Missing data is explicit and preview performs no persisted write.
  await page.getByRole('button', { name: '预览资产曲线' }).click()
  await expect(page.getByText('期末总资产：缺失')).toBeVisible()
  assert.deepEqual(await get(root + '/equity-reports'), [])
  await page.getByLabel('sh600000 估值样本（当前 100 股）').selectOption(dataset.id)
  await expect(page.getByRole('button', { name: '保存资产报告' })).toHaveCount(0)
  await page.getByRole('button', { name: '预览资产曲线' }).click()
  await expect(page.getByText('期末总资产：¥ 10194.99')).toBeVisible()
  await expect(page.getByRole('img', { name: '模拟总资产（元）' })).toBeVisible()
  const savedResponse = page.waitForResponse(response => response.url().endsWith('/equity-reports') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '保存资产报告' }).click()
  const saved = (await (await savedResponse).json()).data
  assert.ok(saved.id)
  assert.equal(saved.result.points.length, 4)
  assert.equal(saved.result.summary.ending_assets, '10194.99')
  assert.equal(saved.result.points.at(-1).positions[0].quote_date, '2025-03-31')
  assert.equal(saved.result.monthly[0].denominator_assets, '10000.00')
  assert.equal(saved.result.monthly[0].return_pct, '1.9499')
  await page.getByLabel('资产观察日期').selectOption('2025-03-30')
  await expect(page.getByText('总资产：¥ 9894.99', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '查看资产报告', exact: true }).click()
  await expect(page.getByRole('heading', { name: /已保存资产报告 · 2025-03-28 至 2025-03-31/ })).toBeVisible()
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  await page.setViewportSize({ width: 1365, height: 900 })
  const ordersBefore = await get(root + '/orders'), cashBefore = (await get(root + '/portfolio')).cash
  await page.goto(base + '/rebuild.html?page=research&run=' + research.id)
  await expect(page.getByRole('heading', { name: '加入模拟委托草稿' })).toBeVisible()
  await page.getByLabel('委托限价', { exact: true }).fill('10')
  await page.getByLabel('换算方式').selectOption('asset_percent')
  await page.getByLabel('总资产分母报告').selectOption(saved.id)
  await page.getByLabel('比例（%）').fill('20')
  await page.getByRole('button', { name: '估算数量与费用' }).click()
  await expect(page.getByText(/总资产分母：¥ 10194.99/)).toBeVisible()
  await expect(page.getByLabel('草稿数量（股）')).toHaveValue('200')
  const draftResponse = page.waitForResponse(response => response.url().endsWith('/equity-reports/drafts') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  const draftHttp = await draftResponse
  token = draftHttp.request().headers()['x-csrf-token']
  const drafted = (await draftHttp.json()).data
  assert.equal(drafted.draft.quantity, 200)
  assert.equal(drafted.sizing.requested_budget, '2038.99')
  assert.equal(drafted.sizing.denominator_assets, '10194.99')
  assert.equal((await get(root + '/portfolio')).cash, cashBefore)
  assert.deepEqual(await get(root + '/orders'), ordersBefore)
  // Change the wallet outside the page. Stale quote cannot be saved again.
  await post(root + '/orders', { symbol: 'sh600000', side: 'buy', quantity: 100, limit_price: '10', signal_date: '2025-03-29', submit_date: '2025-03-31' })
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '模拟钱包、时钟或费用已变化' })).toBeVisible()
  assert.equal((await get(root + '/drafts')).length, 1)
  assert.deepEqual(errors, [])
  console.log(`simulation equity browser passed: unknown quotes -> explicit data -> assets/drawdown -> frozen history -> asset-percent sizing -> evidence-only draft -> stale-wallet rejection, aliases and mobile; isolated ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) { const ended = new Promise(resolve => server.once('exit', resolve)); server.kill(); await ended }
}
