import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-events-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: backend, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port) },
})
let logs = ''
for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { logs = (logs + data.toString()).slice(-12000) })
let browser
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
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  let serial = 0
  async function post(url, body) {
    const response = await page.request.post(base + '/api/v1' + url, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `events-ui-${++serial}` } })
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  await post('/accounts', { name: '事件仓隔离验证' })
  const closes = [...Array.from({ length: 60 }, (_, i) => 12 - i * 2 / 59),
    9.3, 9.5, 9.7, 9.9, 10.1, 10.3, 10.5, 10.7, 10.5, 10.3,
    10.1, 9.9, 9.7, 9.5, 9.5, 9.6, 9.6, 9.6, 9.6, 9.6,
    9.2, 9.5, 10.2, 10.8, 11.2, 11.1, 11.2, 11.3, 11.4, 11.5]
  const volumes = { 58: 1800, 60: 10000, 74: 800, 80: 2000, 82: 1600, 83: 1800, 84: 2200, 85: 600 }
  const bars = closes.map((close, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    const open = closes[index - 1] ?? close
    return { event_date: day, open: open.toFixed(4), high: (Math.max(open, close) + 0.15).toFixed(4), low: (index === 60 || index === 74 ? 9 : index === 80 ? 8.8 : Math.min(open, close) - 0.15).toFixed(4), close: close.toFixed(4), volume: volumes[index] || 1000, available_at: day + 'T08:00:00Z' }
  })
  const last = bars.at(-1).event_date
  bars.push({ event_date: '2025-04-01', open: '9999', high: '9999', low: '9999', close: '9999', volume: 10000000, available_at: '2025-04-01T08:00:00Z' })
  const dataset = await post('/market/datasets', { symbol: '600000.SH', adjustment: 'none', bars })
  await page.goto(base + '/rebuild.html?page=events')
  await expect(page.getByRole('heading', { name: '维科夫事件仓', exact: true })).toBeVisible()
  await page.getByLabel('冻结行情样本（可多选）').selectOption(dataset.id)
  await page.getByLabel('回填开始').fill(last)
  await page.getByLabel('回填结束').fill(last)
  await page.getByRole('button', { name: '预览版本与缺失快照' }).click()
  await expect(page.getByText(/本次 1 个判断 · 已缓存 0 · 待计算 1/)).toBeVisible()
  const createdResponse = page.waitForResponse(response => response.url().endsWith('/research/event-store/jobs') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '确认创建回填任务' }).click()
  const firstJob = (await (await createdResponse).json()).data
  await expect(page.getByRole('heading', { name: '当前回填：完成', exact: true })).toBeVisible({ timeout: 30000 })
  await page.getByRole('button', { name: '查看事件证据' }).click()
  await expect(page.getByRole('heading', { name: `事件证据 · sh600000 · ${last}` })).toBeVisible()
  await expect(page.getByText(/可见样本 90 \/ 最少 30/)).toBeVisible()
  const prior = (await (await page.request.get(base + `/api/v1/research/event-store/records?job_id=${firstJob.id}`)).json()).data[0]
  await page.getByRole('button', { name: '预览版本与缺失快照' }).click()
  await expect(page.getByText(/本次 1 个判断 · 已缓存 1 · 待计算 0/)).toBeVisible()
  await page.getByLabel('回填开始').fill('2025-01-01')
  await page.getByLabel('观察窗口（逗号分隔）').fill('40,60,90')
  await page.getByRole('button', { name: '预览版本与缺失快照' }).click()
  await expect(page.getByText(/本次 270 个判断 · 已缓存 1 · 待计算 269/)).toBeVisible()
  const widerResponse = page.waitForResponse(response => response.url().endsWith('/research/event-store/jobs') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '确认创建回填任务' }).click()
  const wider = (await (await widerResponse).json()).data
  await page.getByRole('button', { name: '取消回填', exact: true }).click()
  await expect(page.getByRole('heading', { name: '当前回填：已取消' })).toBeVisible({ timeout: 15000 })
  await page.getByRole('button', { name: '恢复未完成回填' }).click()
  await expect(page.getByRole('heading', { name: '当前回填：完成', exact: true })).toBeVisible({ timeout: 90000 })
  const done = (await (await page.request.get(base + '/api/v1/research/event-store/jobs/' + wider.id)).json()).data
  assert.equal(done.completed_count, 270)
  assert.equal(done.cache_hits, 1)
  const unchanged = (await (await page.request.get(base + '/api/v1/research/event-store/records/' + prior.id)).json()).data
  assert.equal(unchanged.created_at, prior.created_at)
  assert.equal(unchanged.result.observed_bars, 90)
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  assert.deepEqual(errors, [])
  console.log(`event store browser smoke passed: explicit preview, frozen future exclusion, cache reuse, 270-point cancel/resume, evidence, mobile; isolated ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) {
    const ended = new Promise(resolve => server.once('exit', resolve))
    server.kill()
    await ended
  }
}
