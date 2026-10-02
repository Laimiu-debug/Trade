import { chromium, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const base = 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1050 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const prefix = `plateau-${Date.now()}`
  let serial = 0
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `${prefix}-${++serial}` } })
    if (!response.ok()) throw new Error(await response.text())
    return (await response.json()).data
  }
  async function get(path) { const response = await page.request.get(base + '/api/v1' + path); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  const offset = Date.now() % 10000
  const bars = Array.from({ length: 75 }, (_, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    const close = 10 + index * .12
    return { event_date: day, open: (close - .03).toFixed(4), high: (close + .2).toFixed(4), low: (close - .2).toFixed(4), close: close.toFixed(4), volume: 1000000 + index * 1000 + offset, available_at: `${day}T07:00:00+00:00` }
  })
  const dataset = await post('/market/datasets', { symbol: '600000', bars })
  const source = await post('/backtests', { dataset_id: dataset.id, strategy_id: 'relative_strength_breakout_v1', params: { min_ret40: '.01' }, holding_bars: 3, strict: true })
  await expect.poll(async () => { const run = await get('/backtests/' + source.id); if (run.state === 'failed') throw new Error(run.error); return run.state }, { timeout: 30000 }).toBe('succeeded')
  await page.goto(base + '/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('平原验收')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '单股收益平原', exact: true }).click()
  const panel = page.getByRole('region', { name: '单股收益平原', exact: true })
  const labelControl = (label, tag = 'input') => panel.locator('label').filter({ has: page.getByText(label, { exact: true }) }).locator(tag)
  await labelControl('来源已完成回测', 'select').selectOption(source.id)
  await labelControl('实验名称').fill(prefix + '-grid')
  await panel.getByLabel('网格取值 1', { exact: true }).fill('2,3,4,5,6,7')
  await labelControl('点数上限').fill('5')
  await panel.getByRole('button', { name: '预览采样计划', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('完整网格有 6 点')
  await labelControl('点数上限').fill('6')
  await panel.getByRole('button', { name: '预览采样计划', exact: true }).click()
  await expect(panel.getByText(/请求 6 点 → 实际 6 点/)).toBeVisible()
  const creation = page.waitForResponse(response => response.url() === base + '/api/v1/research/plateaus' && response.request().method() === 'POST')
  await panel.getByRole('button', { name: '确认并创建参数实验', exact: true }).click()
  const createdResponse = await creation
  if (!createdResponse.ok()) throw new Error(await createdResponse.text())
  const created = (await createdResponse.json()).data
  const row = panel.locator('tbody tr').filter({ hasText: prefix + '-grid' }).first()
  await row.getByRole('button', { name: '暂停实验', exact: true }).click()
  await expect.poll(async () => (await get('/research/plateaus/' + created.id)).state, { timeout: 15000 }).toBe('paused')
  const paused = await get('/research/plateaus/' + created.id)
  const completedAtPause = paused.counts.succeeded || 0
  await page.waitForTimeout(600)
  if (((await get('/research/plateaus/' + created.id)).counts.succeeded || 0) !== completedAtPause) throw new Error('Paused experiment started another point')
  await row.getByRole('button', { name: '继续实验', exact: true }).click()
  await expect.poll(async () => { const run = await get('/research/plateaus/' + created.id); if (run.state === 'failed') throw new Error(run.error); return run.state }, { timeout: 60000, intervals: [500, 1000] }).toBe('succeeded')
  await row.getByRole('button', { name: '查看实验', exact: true }).click()
  const detail = panel.getByRole('region', { name: '收益平原详情', exact: true })
  await expect(detail.getByRole('img', { name: '参数切片收益热图', exact: true })).toBeVisible()
  await expect(detail.getByRole('table', { name: '收益平原参数点', exact: true }).locator('tbody tr')).toHaveCount(6)
  await detail.getByRole('table', { name: '收益平原参数点', exact: true }).getByRole('button', { name: '点详情', exact: true }).first().click()
  const point = panel.getByRole('region', { name: '收益平原点详情', exact: true })
  await expect(point.getByText(/邻居 5/)).toBeVisible()
  await point.getByRole('button', { name: '将该点另存为参数预设', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('候选参数已另存为预设')
  const completed = await get('/research/plateaus/' + created.id)
  if (completed.points.some(point => point.attempt_number !== 1) || !completed.result_sha256) throw new Error('Expected immutable one-attempt successful checkpoints')
  const downloadEvent = page.waitForEvent('download')
  await detail.getByRole('link', { name: '导出采样与评估 JSON', exact: true }).click()
  const downloaded = await downloadEvent
  const exported = JSON.parse(await readFile(await downloaded.path(), 'utf8'))
  if (exported.analysis.points.length !== 6 || !exported.analysis.scoring_version) throw new Error('Expected versioned scored point export')

  await labelControl('采样方式', 'select').selectOption('lhs')
  await labelControl('实验名称').fill(prefix + '-lhs')
  await labelControl('LHS 请求点数').fill('4')
  await labelControl('随机种子').fill('12345')
  await panel.getByLabel('采样下限 1', { exact: true }).fill('2')
  await panel.getByLabel('采样上限 1', { exact: true }).fill('20')
  await panel.getByRole('button', { name: '预览采样计划', exact: true }).click()
  await expect(panel.getByText(/冻结种子 12345/)).toBeVisible()
  const lhsEvent = page.waitForResponse(response => response.url() === base + '/api/v1/research/plateaus' && response.request().method() === 'POST')
  await panel.getByRole('button', { name: '确认并创建参数实验', exact: true }).click()
  const lhsResponse = await lhsEvent
  if (!lhsResponse.ok()) throw new Error(await lhsResponse.text())
  const lhs = (await lhsResponse.json()).data
  await expect.poll(async () => (await get('/research/plateaus/' + lhs.id)).state, { timeout: 40000 }).toBe('succeeded')
  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '单股收益平原', exact: true }).click()
  const restoredRow = panel.locator('tbody tr').filter({ hasText: prefix + '-grid' }).first()
  await restoredRow.getByRole('button', { name: '查看实验', exact: true }).click()
  await expect(detail.getByRole('table', { name: '收益平原参数点', exact: true }).locator('tbody tr')).toHaveCount(6)
  await restoredRow.getByRole('button', { name: '删除实验', exact: true }).click()
  await panel.getByRole('button', { name: '确认删除实验', exact: true }).click()
  await expect(restoredRow).toHaveCount(0)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: actual frozen backtest -> grid limit/preview/checkpoint pause/resume -> scores/heatmap/detail/preset/export -> seeded LHS -> reload/delete')
} finally {
  await browser.close()
}
