import { chromium, expect } from '@playwright/test'

const base = 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const session = await (await page.request.get(`${base}/api/v1/session`)).json()
  let serial = 0
  const keyPrefix = `browser-matrix-${Date.now()}`
  async function create(path, body) {
    const response = await page.request.post(base + path, {
      data: body,
      headers: { 'X-CSRF-Token': session.data.csrf_token, 'Idempotency-Key': `${keyPrefix}-${++serial}` },
    })
    if (!response.ok()) throw new Error(`${path}: ${await response.text()}`)
    return (await response.json()).data
  }
  function makeBars(declining = false) {
    return Array.from({ length: 260 }, (_, index) => {
      const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
      const close = declining ? 200 - index * 0.5 : 10 + index * 0.04
      const volume = 1_000_000 + index * 1_000
      return { event_date: day, open: (close - 0.1).toFixed(4), high: (close + 0.4).toFixed(4),
        low: (close - 0.4).toFixed(4), close: close.toFixed(4), volume,
        amount: (close * volume).toFixed(2), available_at: `${day}T07:00:00+00:00` }
    })
  }
  const risingBars = makeBars()
  const rising = await create('/api/v1/market/datasets', { symbol: '600000', bars: risingBars })
  const falling = await create('/api/v1/market/datasets', { symbol: '000001', bars: makeBars(true) })
  const source = await create('/api/v1/research/screener-runs', {
    datasets: [{ dataset_id: rising.id }, { dataset_id: falling.id }],
    as_of_date: risingBars.at(-1).event_date, return_window_days: 40, config: {},
  })

  await page.goto(`${base}/rebuild.html`)
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('矩阵股票池验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '矩阵信号', exact: true }).click()
  const panel = page.getByRole('heading', { name: '矩阵信号 · 冻结股票池', exact: true }).locator('..')
  await panel.locator('label').filter({ has: page.getByText('冻结输入池', { exact: true }) }).locator('select').selectOption(source.id)
  await expect(panel.getByRole('checkbox')).toBeChecked()
  const createdResponse = page.waitForResponse(response =>
    response.url() === `${base}/api/v1/research/matrix-runs` && response.request().method() === 'POST')
  await panel.getByRole('button', { name: '运行矩阵入池与信号', exact: true }).click()
  const created = await createdResponse
  if (!created.ok()) throw new Error(await created.text())
  const run = (await created.json()).data
  if (run.source_run_id !== source.id || run.result.ranking.join() !== rising.id) {
    throw new Error('Matrix run did not retain the selected source and expected single signal')
  }
  await expect(panel.getByText('源池：2', { exact: true })).toBeVisible()
  await expect(panel.getByText('参与排名：2', { exact: true })).toBeVisible()
  await expect(panel.getByText('入池：2', { exact: true })).toBeVisible()
  await expect(panel.getByText('买点：1', { exact: true })).toBeVisible()
  await expect(panel.locator('tbody tr')).toHaveCount(2)
  const hit = panel.locator('tbody tr').filter({ has: page.locator('td:first-child').filter({ hasText: '600000' }) })
  const miss = panel.locator('tbody tr').filter({ has: page.locator('td:first-child').filter({ hasText: '000001' }) })
  await expect(hit.getByRole('button', { name: '提升为观察信号', exact: true })).toBeVisible()
  await expect(miss.getByRole('button', { name: '提升为观察信号', exact: true })).toHaveCount(0)
  await expect(miss.getByText('未触发买点', { exact: true })).toBeVisible()
  const promotionResponse = page.waitForResponse(response =>
    response.url() === `${base}/api/v1/research/matrix-runs/${run.id}/signals/${rising.id}` &&
    response.request().method() === 'POST')
  await hit.getByRole('button', { name: '提升为观察信号', exact: true }).click()
  const promotion = await promotionResponse
  if (!promotion.ok()) throw new Error(await promotion.text())
  const signal = (await promotion.json()).data
  await expect(page.getByRole('heading', { name: `运行结果 · ${signal.id.slice(0, 16)}`, exact: true })).toBeVisible()
  const records = page.getByRole('heading', { name: '研究运行记录', exact: true }).locator('..')
  await expect(records.locator('tbody tr').filter({ hasText: 'matrix_signal_v1' }).first()).toBeVisible()
  await expect(records.getByText(`matrix_run_id: ${run.id}`, { exact: true })).toBeVisible()

  // Reload so reading history is proved independently of the component's current result.
  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '矩阵信号', exact: true }).click()
  const historyResponse = page.waitForResponse(response =>
    response.url() === `${base}/api/v1/research/matrix-runs/${run.id}` && response.request().method() === 'GET')
  await panel.getByLabel('历史矩阵运行', { exact: true }).selectOption(run.id)
  const history = await historyResponse
  if (!history.ok()) throw new Error(await history.text())
  if (JSON.stringify((await history.json()).data.result) !== JSON.stringify(run.result)) {
    throw new Error('Saved matrix history changed after page reload')
  }
  await expect(panel.getByText('买点：1', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '刷新输入池', exact: true }).click()
  await expect(panel.getByLabel('历史矩阵运行', { exact: true })).toHaveValue(run.id)
  await expect(panel.locator('tbody tr')).toHaveCount(2)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: matrix frozen source, pool/trigger explanations, observation promotion, refreshed persisted history')
} finally {
  await browser.close()
}
