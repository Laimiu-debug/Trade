// Real loopback API + browser controls + workers, with an isolated temporary store.
// Run after npm run build; never opens external sources or writes a user account.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-classic-strategies-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const base = `http://127.0.0.1:${port}`
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: backend, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port), PYTHONIOENCODING: 'utf-8' },
})
const strategies = [
  { id: 'classic_donchian_breakout_v1', params: { entry_period: '5', exit_period: '3' }, fields: [
    { label: '突破通道周期', defaultValue: '55', value: '5', min: '5', max: '500' },
    { label: '退出通道周期', defaultValue: '20', value: '3', min: '2', max: '250' },
  ], entry: 'DONCHIAN_PRIOR_HIGH_BREAKOUT', exit: 'DONCHIAN_PRIOR_LOW_BREAKDOWN', indicator: 'prior_entry_high' },
  { id: 'classic_sma_trend_v1', params: { period: '10', buffer_pct: '0' }, fields: [
    { label: '趋势均线周期', defaultValue: '200', value: '10', min: '2', max: '500' },
    { label: '均线双向缓冲比例（0.01=1%）', defaultValue: '0', value: '0', min: '0', max: '0.2' },
  ], entry: 'SMA_TREND_ABOVE', exit: 'SMA_TREND_BELOW', indicator: 'sma' },
  { id: 'classic_bollinger_reentry_v1', params: { period: '20', stddev_multiplier: '2' }, fields: [
    { label: '布林带周期', defaultValue: '20', value: '20', min: '5', max: '250' },
    { label: '总体标准差倍数', defaultValue: '2', value: '2', min: '0.5', max: '4' },
  ], entry: 'BOLLINGER_LOWER_REENTRY', exit: 'BOLLINGER_MIDDLE_REACHED', indicator: 'lower' },
]
function fixture(strategyId) {
  const bars = Array.from({ length: 40 }, (_, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    return { event_date: day, open: '10', high: '10.2', low: '9.8', close: '10', volume: 1000, available_at: day + 'T07:00:00Z' }
  })
  const changed = strategyId === 'classic_bollinger_reentry_v1'
    ? { 31: '6', 32: '8', 33: '8', 34: '10', 35: '10' }
    : { 32: '12', 33: '12', 34: '8', 35: '8' }
  for (const [index, value] of Object.entries(changed)) Object.assign(bars[index], {
    open: value, close: value, high: (Number(value) + .2).toFixed(1), low: (Number(value) - .2).toFixed(1),
  })
  return bars
}
let logs = '', browser, page
for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { logs = (logs + chunk.toString()).slice(-18000) })
try {
  for (let index = 0; index < 125; index++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded startup */ }
    if (server.exitCode !== null || index === 124) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const browserContext = await browser.newContext({ viewport: { width: 1440, height: 980 }, timezoneId: 'Asia/Shanghai' })
  const externalRequests = [], pageErrors = [], evidence = []
  await browserContext.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.origin === base) return route.continue()
    externalRequests.push(url.href)
    return route.abort()
  })
  page = await browserContext.newPage()
  page.on('pageerror', error => pageErrors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  let serial = 0
  async function post(url, body) {
    const response = await page.request.post(base + '/api/v1' + url, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `classic-browser-fixture-${++serial}` } })
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  async function get(url) {
    const response = await page.request.get(base + '/api/v1' + url)
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  async function clickedPost(control, endpoint) {
    const responsePromise = page.waitForResponse(response => response.url() === base + '/api/v1' + endpoint && response.request().method() === 'POST')
    await control.click()
    const response = await responsePromise
    assert.equal(response.status(), 200, await response.text())
    return { value: (await response.json()).data, body: response.request().postDataJSON() }
  }
  const account = await post('/accounts', { name: '经典策略浏览器隔离验收' })
  for (const strategy of strategies) {
    strategy.bars = fixture(strategy.id)
    strategy.dataset = await post('/market/datasets', { symbol: 'sh600000', adjustment: 'none', bars: strategy.bars })
  }
  const landing = await page.goto(base + `/rebuild.html?page=research&research=catalog&account=${account.id}`)
  assert.equal(landing?.status(), 200, 'Built frontend must exist before starting the isolated server; do not run concurrently with a build.')
  await expect(page.getByRole('heading', { name: '策略目录', exact: true })).toBeVisible()
  for (const strategy of strategies) await expect(page.locator('.research-strategy-row').filter({ hasText: strategy.id })).toBeVisible()
  for (const strategy of strategies) {
    const active = page.locator('.research-page:not([hidden])')
    const row = active.locator('.research-strategy-row').filter({ hasText: strategy.id })
    await row.getByText('适用范围与执行规则', { exact: true }).click()
    await expect(row).toContainText('参考')
    await row.getByRole('button', { name: '打开单股研究', exact: true }).click()
    await expect(page.getByRole('heading', { name: '单股研究', exact: true })).toBeVisible()
    await expect(active.getByLabel('策略', { exact: true })).toHaveValue(strategy.id)
    await expect(page).toHaveURL(new RegExp(`strategy=${strategy.id}`))
    for (const field of strategy.fields) {
      const input = active.getByLabel(field.label, { exact: true })
      await expect(input).toHaveValue(field.defaultValue)
      await expect(input).toHaveAttribute('min', field.min)
      await expect(input).toHaveAttribute('max', field.max)
      await input.fill(field.value)
    }
    await active.getByLabel(/^冻结行情样本/).selectOption(strategy.dataset.id)
    await active.getByLabel('决策时间', { exact: true }).fill(strategy.bars[32].event_date + 'T17:00')
    const entry = await clickedPost(active.getByRole('button', { name: '运行信号判断', exact: true }), '/research/runs')
    assert.equal(entry.body.strategy_id, strategy.id)
    assert.deepEqual(entry.body.params, strategy.params)
    assert.equal(entry.value.result.signal, true)
    assert.equal(entry.value.result.exit_signal, false)
    assert.equal(entry.value.result.evaluation.entry_reason, strategy.entry)
    assert.equal(entry.value.result.source_date, strategy.bars[32].event_date)
    assert.ok(entry.value.result.indicator[strategy.indicator])
    const classicEvidence = active.getByRole('region', { name: '经典策略信号依据', exact: true })
    await expect(classicEvidence).toBeVisible()
    await expect(classicEvidence).toContainText('入场信号：是')
    await expect(classicEvidence).toContainText('退出信号：否')
    await expect(active.getByText(/模式 A \/ B \/ C/)).toHaveCount(0)
    await active.getByLabel('决策时间', { exact: true }).fill(strategy.bars[34].event_date + 'T17:00')
    const exit = await clickedPost(active.getByRole('button', { name: '运行信号判断', exact: true }), '/research/runs')
    assert.equal(exit.value.result.signal, false)
    assert.equal(exit.value.result.exit_signal, true)
    assert.equal(exit.value.result.evaluation.exit_reason, strategy.exit)
    await expect(classicEvidence).toContainText('入场信号：否')
    await expect(classicEvidence).toContainText('退出信号：是')
    await expect(active.getByRole('heading', { name: '加入模拟委托草稿', exact: true })).toHaveCount(0)

    await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: /^单股回测/ }).click()
    await expect(page.getByRole('heading', { name: '单股回测', exact: true })).toBeVisible()
    await active.getByLabel(/^回测策略/).selectOption(strategy.id)
    await active.getByLabel(/^冻结行情样本/).selectOption(strategy.dataset.id)
    await active.getByLabel('持有 K 线数', { exact: true }).fill('60')
    await active.getByLabel('初始资金', { exact: true }).fill('10000')
    const parameters = active.locator('details').filter({ has: page.locator('summary', { hasText: /^策略参数$/ }) })
    if ((await parameters.getAttribute('open')) === null) await parameters.locator(':scope > summary').click()
    for (const field of strategy.fields) {
      const input = active.getByLabel(field.label, { exact: true })
      await expect(input).toHaveValue(field.defaultValue)
      await input.fill(field.value)
    }
    const queued = await clickedPost(active.getByRole('button', { name: '提交后台回测', exact: true }), '/backtests')
    assert.equal(queued.body.strategy_id, strategy.id)
    assert.deepEqual(queued.body.params, strategy.params)
    let finished
    await expect.poll(async () => {
      finished = await get('/backtests/' + queued.value.id)
      if (['failed', 'cancelled'].includes(finished.state)) throw new Error(JSON.stringify(finished))
      return finished.state
    }, { timeout: 45000, intervals: [250, 500, 1000] }).toBe('succeeded')
    const [buy, sell] = finished.result.trades
    assert.equal(buy.date, strategy.bars[33].event_date)
    assert.equal(buy.signal_date, strategy.bars[32].event_date)
    assert.equal(sell.date, strategy.bars[35].event_date)
    assert.equal(sell.reason, 'CLASSIC_SIGNAL_NEXT_OPEN')
    assert.equal(sell.reason_metrics.evaluation.exit_reason, strategy.exit)
    assert.ok(Number(buy.fees) > 0 && Number(sell.fees) > 0)
    assert.ok(Date.parse(buy.known_at) < Date.parse(buy.execution_at))
    assert.ok(Date.parse(sell.known_at) < Date.parse(sell.execution_at))
    const report = active.getByRole('region', { name: '单股回测结果', exact: true })
    await expect(report).toContainText('状态：已完成', { timeout: 10000 })
    const tradeTable = report.locator('table').filter({ has: page.getByRole('columnheader', { name: '成交依据', exact: true }) })
    const saleRow = tradeTable.locator('tbody tr').filter({ has: page.getByRole('cell', { name: strategy.bars[35].event_date, exact: true }) }).filter({ hasText: '卖出' })
    await expect(saleRow).toContainText('经典策略退出')
    await expect(saleRow).toContainText('下一开盘')
    await page.screenshot({ path: path.join(dataDir, strategy.id + '.png'), fullPage: true })
    evidence.push({ strategy_id: strategy.id, dataset_id: strategy.dataset.id, params: strategy.params,
      entry_run: entry.value.id, exit_run: exit.value.id, backtest_id: finished.id,
      buy_date: buy.date, sell_date: sell.date, sell_reason: sell.reason,
      checks: ['catalog_navigation', 'default_and_custom_params', 'entry_and_exit_evidence', 'no_exit_buy_draft', 'real_worker_next_open_exit'] })
    await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: /^策略目录/ }).click()
    await expect(page.getByRole('heading', { name: '策略目录', exact: true })).toBeVisible()
  }
  assert.deepEqual(pageErrors, [])
  assert.deepEqual(externalRequests, [])
  assert.equal((await get('/backtests')).length, 3)
  await writeFile(path.join(dataDir, 'browser-evidence.json'), JSON.stringify({ evidence, pageErrors, externalRequests }, null, 2), 'utf8')
  console.log(`classic strategy browser passed: 3 catalog selections -> defaults/custom parameters -> entry/exit research -> real UI backtests and next-open exits; no external requests; isolated ${dataDir}`)
} catch (error) {
  await page?.screenshot({ path: path.join(dataDir, 'failure.png'), fullPage: true }).catch(() => {})
  await writeFile(path.join(dataDir, 'server.log'), logs, 'utf8')
  console.error(`classic strategy browser failed; evidence ${dataDir}`)
  throw error
} finally {
  await browser?.close()
  if (server.exitCode === null) { const ended = new Promise(resolve => server.once('exit', resolve)); server.kill(); await ended }
}
