import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 }, timezoneId: 'Asia/Shanghai' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = 'http://127.0.0.1:8011'
  const session = await (await page.request.get(`${base}/api/v1/session`)).json()
  const serial = Date.now()
  const headers = suffix => ({ 'X-CSRF-Token': session.data.csrf_token, 'Idempotency-Key': `browser-wulong-${serial}-${suffix}` })
  const closes = [
    ...Array.from({ length: 60 }, (_, index) => Number((8 + index * 2 / 59 - (index % 7 === 3 ? 0.1 : 0)).toFixed(4))),
    ...Array(25).fill(10), 10.3, 10.6, 10.9,
  ]
  const bars = closes.map((close, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    return { event_date: day, open: (close - 0.05).toFixed(4), high: (close + 0.1).toFixed(4),
      low: (close - 0.1).toFixed(4), close: close.toFixed(4),
      volume: index === closes.length - 1 ? 3000 : index === 0 || close >= closes[index - 1] ? 1200 : 1000,
      available_at: `${day}T08:00:00+00:00` }
  })
  bars.push({ event_date: '2025-03-30', open: '10.9000', high: '15.0000', low: '10.0000',
    close: '14.0000', volume: 1000000, available_at: '2025-03-30T08:00:00+00:00' })
  const imported = await page.request.post(`${base}/api/v1/market/datasets`, {
    data: { symbol: '600000', adjustment: 'none', bars },
    headers: headers('market'),
  })
  if (!imported.ok()) throw new Error(await imported.text())
  const dataset = (await imported.json()).data
  const simResponse = await page.request.post(`${base}/api/v1/sim-accounts`, {
    data: { name: `五龙入池验证 ${serial}`, initial_capital: '10000', start_date: '2025-03-29' },
    headers: headers('account'),
  })
  if (!simResponse.ok()) throw new Error(await simResponse.text())
  const simAccount = (await simResponse.json()).data
  await page.goto(`${base}/rebuild.html`)
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  const panel = page.getByRole('heading', { name: '固定样本信号' }).locator('..')
  await panel.getByLabel('策略').selectOption('wulong_cluster_v1')
  await panel.getByLabel('冻结行情样本').selectOption(dataset.id)
  await panel.getByLabel('决策时间').fill('2025-03-29T17:00')
  await panel.getByLabel('严格可得时间').check()

  async function runSignal() {
    const pending = page.waitForResponse(response => response.url().endsWith('/api/v1/research/runs') && response.request().method() === 'POST')
    await panel.getByRole('button', { name: '运行信号判断' }).click()
    const response = await pending
    if (!response.ok()) throw new Error(await response.text())
    await page.getByText('研究输入和结果已按版本保存').waitFor()
    return (await response.json()).data
  }

  const accepted = await runSignal()
  assert.equal(accepted.decision_at, '2025-03-29T09:00:00+00:00')
  assert.equal(accepted.result.source_date, '2025-03-29')
  assert.equal(accepted.result.universe.observed_bars, 88)
  assert.equal(accepted.result.shape_signal, true)
  assert.equal(accepted.result.signal, true)
  assert.equal(accepted.result.draft_eligible, true)
  const detail = page.getByRole('heading', { name: /^运行结果 ·/ }).locator('..')
  await expect(detail.getByText('形态触发：是', { exact: true })).toBeVisible()
  await expect(detail.getByText('入池筛选：通过', { exact: true })).toBeVisible()
  await expect(detail.getByText(/观察信号：是/)).toBeVisible()
  await detail.getByText('候选入池条件明细', { exact: true }).click()
  await expect(detail.getByText(/旧单股候选口径 · store\._build_row_from_candles/)).toBeVisible()
  const checkRows = detail.locator('details tbody tr')
  await expect(checkRows).toHaveCount(8)
  await expect(detail.locator('details').getByRole('cell', { name: '通过', exact: true })).toHaveCount(8)
  await expect(detail.getByRole('heading', { name: '加入模拟委托草稿' })).toBeVisible()
  await detail.getByLabel('模拟账户').selectOption(simAccount.id)

  await panel.getByText(/^策略参数 ·/).click()
  await panel.getByLabel('40 日涨幅下限', { exact: true }).fill('0.2')
  const rejected = await runSignal()
  assert.equal(rejected.result.shape_signal, true)
  assert.equal(rejected.result.signal, false)
  assert.equal(rejected.result.draft_eligible, false)
  assert.deepEqual(rejected.result.universe.failed_conditions, ['min_ret40'])
  await expect(detail.getByText('形态触发：是', { exact: true })).toBeVisible()
  await expect(detail.getByText('入池筛选：未通过', { exact: true })).toBeVisible()
  await expect(detail.getByText(/观察信号：否/)).toBeVisible()
  await expect(checkRows.filter({ hasText: '40 日涨幅下限' })).toContainText('40日涨幅未达下限')
  await expect(detail.getByRole('heading', { name: '加入模拟委托草稿' })).toHaveCount(0)
  const orders = await page.request.get(`${base}/api/v1/sim-accounts/${simAccount.id}/orders`)
  assert.deepEqual((await orders.json()).data, [])
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: Wulong real shape + 8 candidate gates, future bar exclusion, accepted and rejected draft visibility')
} finally { await browser.close() }
