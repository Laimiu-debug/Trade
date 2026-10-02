import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-registry-ui-'))
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
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `registry-ui-${++serial}` } })
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  async function get(url) { const response = await page.request.get(base + '/api/v1' + url); assert.equal(response.status(), 200, await response.text()); return (await response.json()).data }
  await post('/accounts', { name: '策略设置隔离验证' })
  const bars = Array.from({ length: 64 }, (_, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    return { event_date: day, open: '10', high: '11', low: '9', close: '10', volume: 1000, available_at: day + 'T08:00:00Z' }
  })
  const dataset = await post('/market/datasets', { symbol: '600000', adjustment: 'none', bars })
  await page.goto(base + '/rebuild.html?page=settings&settings_group=strategies')
  await expect(page.getByRole('heading', { name: '策略启用与默认' })).toBeVisible()
  const catalog = await get('/research/strategies')
  assert.equal(catalog.length, 19)
  for (const id of ['classic_donchian_breakout_v1', 'classic_sma_trend_v1', 'classic_bollinger_reentry_v1']) {
    assert.ok(catalog.some(strategy => strategy.id === id))
  }
  const strategyControls = page.getByRole('checkbox', { name: /^启用策略 / })
  await expect(strategyControls).toHaveCount(catalog.length)
  assert.deepEqual((await strategyControls.evaluateAll(inputs => inputs.map(input => input.getAttribute('aria-label').replace('启用策略 ', '')))).sort(), catalog.map(strategy => strategy.id).sort())
  await page.getByLabel('启用策略 wulong_cluster_v1', { exact: true }).uncheck()
  await page.getByLabel('新建默认策略').selectOption('b1_mtf_v1')
  await page.getByRole('button', { name: '预览策略设置差异' }).click()
  const registryWrite = page.waitForResponse(response => response.url().endsWith('/research/registry') && response.request().method() === 'PUT')
  await page.getByRole('button', { name: '确认保存策略设置' }).click()
  const registryResponse = await registryWrite
  token = registryResponse.request().headers()['x-csrf-token']
  assert.equal(registryResponse.status(), 200)
  const saved = (await registryResponse.json()).data
  assert.equal(saved.default_strategy_id, 'b1_mtf_v1')
  assert.ok(!saved.enabled_ids.includes('wulong_cluster_v1'))
  const disabled = await page.request.post(base + '/api/v1/research/runs', { data: { dataset_id: dataset.id, strategy_id: 'wulong_cluster_v1', decision_at: '2025-03-05T09:00:00Z', strict: true, params: {} }, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': 'disabled-new-run' } })
  assert.equal(disabled.status(), 409)
  assert.equal((await disabled.json()).error.code, 'STRATEGY_DISABLED')
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  await expect(page.getByLabel('策略', { exact: true })).toHaveValue('b1_mtf_v1')
  await expect(page.getByRole('button', { name: '运行信号判断', exact: true })).toHaveCount(0)
  await expect(page.getByText('默认 / 所选策略为 B1，请在选股筛选页的 B1 多周期入口选择样本并运行。')).toBeVisible()
  assert.equal(await page.getByLabel('策略', { exact: true }).locator('option[value="wulong_cluster_v1"]').count(), 0)
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: 'B1 多周期', exact: true }).click()
  await page.getByLabel('当日量 / 20 日均量上限', { exact: true }).fill('0.6')
  const panel = page.getByLabel('策略参数预设', { exact: true })
  await panel.locator(':scope > summary').click()
  await panel.getByLabel('预设名称', { exact: true }).fill('B1 真实页面预设')
  const presetWrite = page.waitForResponse(response => response.url().endsWith('/research/presets') && response.request().method() === 'POST')
  await panel.getByRole('button', { name: '保存当前参数为新预设' }).click()
  const presetResponse = await presetWrite
  assert.equal(presetResponse.status(), 200)
  const preset = (await presetResponse.json()).data
  assert.equal(preset.strategy_id, 'b1_mtf_v1')
  assert.equal(preset.params.vol_ratio, '0.6')
  assert.equal(Object.keys(preset.params).length, 5)
  const shareRequest = page.waitForResponse(response => response.url().endsWith('/export') && response.request().method() === 'GET')
  await panel.getByRole('button', { name: '生成参数分享码' }).click()
  const shareResponse = await shareRequest
  assert.equal(shareResponse.status(), 200, await shareResponse.text())
  const codeField = panel.locator('textarea[readonly]')
  await expect(codeField).toHaveCount(1)
  const code = await codeField.inputValue()
  assert.ok(code.startsWith('TRADE-PRESET-1.'))
  await page.getByLabel('当日量 / 20 日均量上限', { exact: true }).fill('0.8')
  await panel.getByText('导入参数分享码', { exact: true }).click()
  await panel.getByLabel('待导入参数分享码').fill(code)
  await panel.getByRole('button', { name: '检查分享码并预览差异' }).click()
  await expect(panel.getByRole('cell', { name: '0.8', exact: true })).toBeVisible()
  await expect(panel.getByRole('cell', { name: '0.6', exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '预览导入参数填入表单' }).click()
  await panel.getByRole('button', { name: '确认填入当前表单' }).click()
  await expect(page.getByLabel('当日量 / 20 日均量上限', { exact: true })).toHaveValue('0.6')
  await page.getByText('选择 B1 冻结样本 · 已选 0 只', { exact: true }).click()
  await page.getByLabel(`B1 样本 ${dataset.symbol} ${dataset.id.slice(0, 8)}`, { exact: true }).check()
  await page.getByLabel('B1 截至日期', { exact: true }).fill('2025-03-05')
  const b1Write = page.waitForResponse(response => response.url().endsWith('/research/b1-runs') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '运行 B1（已选 1 只）', exact: true }).click()
  const b1Response = await b1Write
  assert.equal(b1Response.status(), 200)
  const run = (await b1Response.json()).data
  assert.equal(run.result.b1_params.vol_ratio, .6)
  assert.equal(run.result.excluded[0].reason, 'INSUFFICIENT_BARS_AS_OF_DATE')
  await page.goto(base + '/rebuild.html?page=settings&settings_group=strategies')
  await page.getByLabel('启用策略 b1_mtf_v1', { exact: true }).uncheck()
  await expect(page.getByRole('button', { name: '预览策略设置差异' })).toBeDisabled()
  await page.getByLabel('新建默认策略').selectOption('wyckoff_trend_v1')
  await page.getByRole('button', { name: '预览策略设置差异' }).click()
  await page.getByRole('button', { name: '确认保存策略设置' }).click()
  await expect(page.getByText('策略设置已保存 · 版本 2')).toBeVisible()
  await page.getByRole('button', { name: '读取策略设置历史' }).click()
  await expect(page.getByRole('cell', { name: '2', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: 'B1 多周期', exact: true }).click()
  await expect(page.getByText('B1 策略已停用，新扫描请先在系统设置启用；历史记录保留。')).toBeVisible()
  await expect(page.getByRole('button', { name: '运行 B1（已选 1 只）', exact: true })).toBeDisabled()
  assert.equal((await get('/research/b1-runs/' + run.id)).result.b1_params.vol_ratio, .6)
  await page.goto(base + '/rebuild.html?page=settings&settings_group=strategies')
  await expect(page.getByLabel('启用策略 b1_mtf_v1', { exact: true })).not.toBeChecked()
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  assert.deepEqual(errors, [])
  console.log(`strategy registry browser passed: versioned disable/default -> server409 -> native B1 default -> real preset/share/diff/apply -> scan -> disable preserves history -> reload/mobile; isolated ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) { const ended = new Promise(resolve => server.once('exit', resolve)); server.kill(); await ended }
}
