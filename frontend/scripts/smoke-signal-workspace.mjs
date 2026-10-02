import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-signals-ui-'))
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
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  let serial = 0
  async function post(url, body) {
    const response = await page.request.post(base + '/api/v1' + url, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `signals-ui-${++serial}` } })
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  await post('/accounts', { name: '信号工作区隔离验证' })
  const closes = [...Array.from({ length: 60 }, (_, index) => Number((8 + index * 2 / 59 - (index % 7 === 3 ? .1 : 0)).toFixed(4))), ...Array(25).fill(10), 10.3, 10.6, 10.9]
  const bars = closes.map((close, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    return { event_date: day, open: (close - .05).toFixed(4), high: (close + .1).toFixed(4), low: (close - .1).toFixed(4), close: close.toFixed(4), volume: index === 87 ? 3000 : index === 0 || close >= closes[index - 1] ? 1200 : 1000, available_at: day + 'T08:00:00Z' }
  })
  for (const [index, close] of [11, 9, 9999].entries()) {
    const day = new Date(Date.UTC(2025, 2, 30 + index)).toISOString().slice(0, 10)
    bars.push({ event_date: day, open: String(close), high: String(close), low: String(close), close: String(close), volume: 1000, available_at: day + 'T08:00:00Z' })
  }
  const dataset = await post('/market/datasets', { symbol: '600000.SH', adjustment: 'none', bars })
  await page.goto(base + '/rebuild.html?page=signals')
  await expect(page.getByRole('heading', { name: '信号工作区', exact: true })).toBeVisible()
  await page.getByText('从固定样本 / 趋势池 / 本地通达信集合启动扫描', { exact: true }).click()
  await page.getByLabel(`选择信号样本 ${dataset.symbol}`).check()
  await page.getByLabel('信号策略').selectOption('wulong_cluster_v1')
  await page.getByLabel('扫描模式').selectOption('range')
  await page.getByLabel('扫描开始日').fill('2025-03-28')
  await page.getByLabel('扫描截至日').fill('2025-03-29')
  await page.getByRole('button', { name: '预览冻结扫描' }).click()
  await expect(page.getByText(/2 次判断 · 所选完整集合/)).toBeVisible()
  await page.getByRole('button', { name: '确认启动后台扫描' }).click()
  await expect(page.getByText('扫描已完成，可以预览信号报告。')).toBeVisible({ timeout: 30000 })
  await page.getByRole('button', { name: '预览信号报告', exact: true }).click()
  await expect(page.getByRole('cell', { name: /Active · 有效/ }).first()).toBeVisible()
  await expect(page.getByText(/等待后续行情/).first()).toBeVisible()
  const savedResponse = page.waitForResponse(response => response.url().endsWith('/signal-workspace/reports') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '保存这份报告' }).click()
  const saved = (await (await savedResponse).json()).data
  assert.ok(saved?.id)
  const oldRow = saved.result.rows.find(row => row.decision_date === '2025-03-29')
  assert.equal(oldRow.signal, true)
  assert.equal(oldRow.executable_date, null)
  assert.equal(saved.result.per_symbol[0].range_performance.end_date, '2025-03-29')
  assert.equal(saved.result.per_symbol[0].range_performance.return_pct, 2.83)
  await page.getByRole('button', { name: '查看信号证据' }).last().click()
  await expect(page.getByText(/确认日为保存观察的上界/)).toBeVisible()
  await page.getByLabel('报告截至日').fill('2025-04-01')
  await page.getByLabel('确认后延迟入场 K 线').fill('2')
  const previewResponse = page.waitForResponse(response => response.url().endsWith('/signal-workspace/preview') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '预览信号报告', exact: true }).click()
  const later = (await (await previewResponse).json()).data
  const newRow = later.result.rows.find(row => row.run_id === oldRow.run_id)
  assert.equal(newRow.rank_score, oldRow.rank_score)
  assert.equal(newRow.executable_date, '2025-03-31')
  assert.equal(newRow.timeliness, 'expired')
  assert.equal(later.result.per_symbol[0].range_performance.return_pct, 2.83)
  await page.getByLabel('交易所').selectOption('bj')
  await page.getByRole('button', { name: '预览信号报告', exact: true }).click()
  await expect(page.getByText('没有通过当前过滤的信号，原判断仍保留。')).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '查看信号报告', exact: true }).click()
  await expect(page.getByRole('heading', { name: '已保存报告 · 截至 2025-03-29' })).toBeVisible()
  await expect(page.getByRole('cell', { name: /Active · 有效/ }).first()).toBeVisible()
  // Full scanner path freezes candidate semantics, gates, rank weights and profile.
  await page.getByText('从固定样本 / 趋势池 / 本地通达信集合启动扫描', { exact: true }).click()
  await page.getByLabel(`选择信号样本 ${dataset.symbol}`).check()
  await page.getByLabel('信号计算口径').selectOption('full')
  await page.getByLabel('信号策略').selectOption('relative_strength_breakout_v1')
  await page.getByLabel('扫描截至日').fill('2025-03-29')
  await page.getByText('策略参数 · 冻结当前值', { exact: true }).click()
  for (const label of ['综合分下限', '最少正向事件数', '健康分下限', '事件分下限']) await page.getByLabel(label, { exact: true }).fill('0')
  await page.getByLabel('排名权重：健康', { exact: true }).fill('0.5')
  await page.getByRole('button', { name: '加入多策略组合', exact: true }).click()
  await page.getByLabel('信号策略').selectOption('wulong_cluster_v1')
  await page.getByRole('button', { name: '加入多策略组合', exact: true }).click()
  const completePreviewPromise = page.waitForResponse(response => response.url().endsWith('/signal-workspace/scan-preview') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '预览冻结扫描' }).click()
  const completePreviewResponse = await completePreviewPromise
  assert.equal(completePreviewResponse.status(), 200, await completePreviewResponse.text())
  const sent = completePreviewResponse.request().postDataJSON().scan
  assert.deepEqual(sent.signal_context, { candidate_path: 'legacy_store', window_days: 60, universe_mode: 'strategy_filtered' })
  assert(sent.event_profile_id && Number.isInteger(sent.event_profile_revision) && sent.event_profile_revision >= 0)
  assert.equal(sent.strategies.length, 2)
  assert.equal(sent.strategies[0].params.rank_weight_health, '0.5')
  await page.getByRole('button', { name: '确认启动后台扫描' }).click()
  await expect(page.getByText('扫描已完成，可以预览信号报告。')).toBeVisible({ timeout: 45000 })
  await page.getByRole('button', { name: '预览信号报告', exact: true }).click()
  const completeReportPromise = page.waitForResponse(response => response.url().endsWith('/signal-workspace/reports') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '保存这份报告' }).click()
  const complete = (await (await completeReportPromise).json()).data
  const ranked = complete.result.rows.find(row => row.strategy_id === 'relative_strength_breakout_v1')
  assert(ranked && ranked.rank_score !== null && ranked.rank_score !== undefined)
  assert.equal(ranked.rank_metric_source, 'legacy_store')
  assert(ranked.rank_components && ranked.signal_context && ranked.health_score !== null)
  const rankedRow = page.getByRole('row').filter({ hasText: '相对强弱' }).filter({ has: page.getByRole('button', { name: '查看信号证据' }) })
  await rankedRow.getByRole('button', { name: '查看信号证据' }).click()
  await expect(page.getByText(/排名来源：legacy_store/)).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: '导出交叉验证 CSV' }).click()
  const downloaded = await downloadPromise
  const csv = await readFile(await downloaded.path(), 'utf8')
  assert(csv.includes('sh600000'))
  await page.getByLabel('同策略排名分下限').fill('99')
  assert((await page.getByRole('link', { name: '导出交叉验证 CSV' }).getAttribute('href')).includes(complete.id))
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  await page.reload()
  // Select by newest record instead of an ephemeral form state.
  await page.getByRole('button', { name: '查看信号报告', exact: true }).first().click()
  await expect(page.getByRole('link', { name: '导出交叉验证 CSV' })).toHaveAttribute('href', `/api/v1/research/signal-workspace/reports/${complete.id}/cross-validation.csv`)
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  assert.deepEqual(errors, [])
  console.log(`signal workspace browser passed: source preview -> durable scan -> frozen report, old rank parity, as-of expiry, delayed entry, future range exclusion, filters, full candidate context, multi-strategy parameters/profile/rank, frozen CSV, reload, mobile; isolated ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) { const ended = new Promise(resolve => server.once('exit', resolve)); server.kill(); await ended }
}
