// Full browser workflow in a fresh temporary data directory and owned server.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-planning-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app',
  '--host', '127.0.0.1', '--port', String(port)], {
  cwd: backend, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port) },
})
let logs = ''
for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { logs = (logs + data.toString()).slice(-12000) })
let browser
try {
  const base = `http://127.0.0.1:${port}`
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded startup */ }
    if (server.exitCode !== null || i === 99) throw new Error(`Owned test server not ready: ${logs}`)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 }, timezoneId: 'Asia/Shanghai' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  let serial = 0
  async function write(url, body, method = 'POST') {
    const response = await page.request.fetch(base + '/api/v1' + url, { method, data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `planning-ui-${++serial}` } })
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  const account = await write('/accounts', { name: '预演隔离验证' })
  const root = `/accounts/${account.id}`
  await write(root + '/cash-flows', { flow_date: '2025-01-01', kind: 'initial', amount: '2000' })
  await write(root + '/snapshots/2025-01-03', { snap_date: '2025-01-03', total_assets: '2000', available_cash: '1000', expected_revision: 0,
    positions: [{ symbol: '600000', name: '测试持仓', quantity: 100, market_value: '1000' }] }, 'PUT')
  await page.goto(base + '/rebuild.html')
  await page.getByRole('button', { name: '每日复盘', exact: true }).click()
  await page.getByLabel('复盘日期').fill('2025-01-03')
  await expect(page.getByText(/基准现金：¥ 1000.00/)).toBeVisible()
  await expect(page.getByLabel('计划执行日')).toHaveValue('')
  await page.getByRole('button', { name: '复制当前基准持仓到草稿' }).click()
  await expect(page.getByLabel('预演数量')).toHaveValue('100')
  await page.getByLabel('预演数量').fill('200')
  await page.getByLabel('预计价格').fill('10')
  await page.getByRole('button', { name: '检查预演现金与费用' }).click()
  const result = page.getByRole('region', { name: '预演检查结果' })
  await expect(result).toContainText('现金检查：预计不足')
  await expect(result).toContainText('预计剩余现金 ¥ -5.01')
  await expect(result).toContainText('预计费用 ¥ 5.01')
  await page.getByLabel('预计价格').fill('')
  await expect(result).toHaveCount(0)
  await page.getByRole('button', { name: '检查预演现金与费用' }).click()
  await expect(result).toContainText('现金检查：信息不足')
  await expect(result).toContainText('预计剩余现金 未知')
  await page.getByText('导入或核对本地交易日历', { exact: true }).click()
  const days = ['2025-01-04', '2025-01-05', '2025-01-06', '2025-01-07'].map(date => ({ date, is_open: date === '2025-01-07' }))
  await page.getByLabel('选择日历 JSON').setInputFiles({ name: 'isolated-calendar.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({ source: '隔离UI测试日历，非真实交易安排', start_date: days[0].date, end_date: days.at(-1).date, days })) })
  await page.getByRole('button', { name: '确认导入本地日历' }).click()
  await page.getByRole('button', { name: '采用本地日历下一交易日 2025-01-07' }).click()
  await expect(page.getByLabel('计划执行日')).toHaveValue('2025-01-07')
  await expect(page.getByText('本地日历标记为开市日。')).toBeVisible()
  await page.getByLabel('预计价格').fill('10')
  await expect(page.getByRole('status')).toContainText('已保存', { timeout: 10000 })
  const saved = (await (await page.request.get(base + '/api/v1' + root + '/daily-reviews/2025-01-03')).json()).data
  assert.equal(saved.next_target_date, '2025-01-07')
  assert.equal(saved.next_position_rehearsal[0].price, '10.0000')
  assert.deepEqual((await (await page.request.get(base + '/api/v1' + root + '/trades')).json()).data, [])
  await page.setViewportSize({ width: 375, height: 812 })
  await expect(page.getByRole('heading', { name: '每日复盘', exact: true, level: 2 })).toBeVisible()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  assert.deepEqual(errors, [])
  console.log(`planning browser smoke passed: draft copy, fees shortage, unknown price, local calendar import/adoption, persistence, mobile; isolated ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) {
    const ended = new Promise(resolve => server.once('exit', resolve))
    server.kill()
    await ended
  }
}
