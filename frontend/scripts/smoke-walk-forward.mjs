import { chromium, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const base = 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1050 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const prefix = `walk-forward-${Date.now()}`
  let serial = 0
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `${prefix}-${++serial}` } })
    if (!response.ok()) throw new Error(await response.text())
    return (await response.json()).data
  }
  async function get(path) { const response = await page.request.get(base + '/api/v1' + path); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  const offset = Date.now() % 10000
  const bars = Array.from({ length: 105 }, (_, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    const close = 10 + index * .12
    return { event_date: day, open: (close - .03).toFixed(4), high: (close + .2).toFixed(4), low: (close - .2).toFixed(4), close: close.toFixed(4), volume: 100000 + index * 1000 + offset, available_at: `${day}T07:00:00+00:00` }
  })
  const dataset = await post('/market/datasets', { symbol: '600000', bars })
  const source = await post('/backtests', { dataset_id: dataset.id, strategy_id: 'relative_strength_breakout_v1', params: { min_ret40: '.01', min_vol_slope20: '0' }, holding_bars: 3, strict: true })
  await expect.poll(async () => { const run = await get('/backtests/' + source.id); if (run.state === 'failed') throw new Error(run.error); return run.state }, { timeout: 30000 }).toBe('succeeded')
  await page.goto(base + '/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('滚动验证验收')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '单股 Walk-forward', exact: true }).click()
  const panel = page.getByRole('region', { name: '滚动样本外验证', exact: true })
  const control = (label, tag = 'input') => panel.locator('label').filter({ has: page.getByText(label, { exact: true }) }).locator(tag)
  await control('滚动验证来源回测', 'select').selectOption(source.id)
  await control('滚动实验名称').fill(prefix)
  await panel.getByLabel('滚动网格取值 1', { exact: true }).fill('2,4')
  await control('首次训练 K 线数').fill('55')
  await control('每折测试 K 线数').fill('20')
  await control('训练测试间隔 K 线数').fill('1')
  await control('最多滚动折数').fill('2')
  await control('训练最少完整交易数').fill('1')
  await panel.getByRole('button', { name: '预览候选与时间折', exact: true }).click()
  const preview = panel.getByRole('region', { name: '滚动验证计划预览', exact: true })
  await expect(preview.getByText(/冻结 2 个候选 · 2 折 · 共 6 个计算点/)).toBeVisible()
  await expect(preview.getByRole('table', { name: '滚动时间折计划', exact: true }).locator('tbody tr')).toHaveCount(2)
  const creation = page.waitForResponse(response => response.url() === base + '/api/v1/research/walk-forwards' && response.request().method() === 'POST')
  await panel.getByRole('button', { name: '确认并创建滚动验证', exact: true }).click()
  const response = await creation
  if (!response.ok()) throw new Error(await response.text())
  const created = (await response.json()).data
  const row = panel.getByRole('table', { name: '滚动验证历史', exact: true }).locator('tbody tr').filter({ hasText: prefix })
  await row.getByRole('button', { name: '暂停滚动验证', exact: true }).click()
  await expect.poll(async () => (await get('/research/walk-forwards/' + created.id)).state, { timeout: 15000 }).toBe('paused')
  await row.getByRole('button', { name: '继续滚动验证', exact: true }).click()
  await expect.poll(async () => { const run = await get('/research/walk-forwards/' + created.id); if (run.state === 'failed') throw new Error(run.error); return run.state }, { timeout: 60000, intervals: [500, 1000] }).toBe('succeeded')
  await row.getByRole('button', { name: '查看滚动验证', exact: true }).click()
  const detail = panel.getByRole('region', { name: '滚动验证详情', exact: true })
  await expect(detail.getByRole('img', { name: '按折归一化样本外曲线', exact: true })).toBeVisible()
  await expect(detail.getByText('完成测试 2/2 折', { exact: true })).toBeVisible()
  await detail.getByText('第 1 折训练候选排名', { exact: true }).click()
  await detail.getByRole('button', { name: '查看训练点', exact: true }).first().click()
  await expect(panel.getByRole('region', { name: '滚动验证计算点', exact: true }).getByRole('heading')).toContainText('训练点')
  await detail.getByRole('button', { name: '查看第 1 折测试点', exact: true }).click()
  await expect(panel.getByRole('region', { name: '滚动验证计算点', exact: true }).getByRole('heading')).toContainText('测试点')
  const completed = await get('/research/walk-forwards/' + created.id)
  if (completed.tasks.some(task => task.attempt_number !== 1) || completed.folds.some(fold => !fold.selection_sha256)) throw new Error('Expected one-attempt immutable selection and compute checkpoints')
  for (const fold of completed.folds) {
    const test = completed.tasks.find(task => task.role === 'test' && task.fold_index === fold.index)
    if (test.candidate_sha256 !== fold.selection.selected_candidate_sha256) throw new Error('Test candidate differs from frozen training selection')
    const item = await get(`/research/walk-forwards/${created.id}/tasks/${test.id}`)
    if (item.result.equity.length !== 20 || item.result.equity[0].date !== fold.test_start || item.result.equity.at(-1).date !== fold.test_end) throw new Error('Warmup contributed to OOS equity')
  }
  const downloadEvent = page.waitForEvent('download')
  await detail.getByRole('link', { name: '导出滚动验证 JSON', exact: true }).click()
  const download = await downloadEvent
  const exported = JSON.parse(await readFile(await download.path(), 'utf8'))
  if (exported.analysis.oos_normalized_curve.length !== 40 || exported.result_sha256 !== completed.result_sha256) throw new Error('Expected frozen complete OOS export')
  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '单股 Walk-forward', exact: true }).click()
  await row.getByRole('button', { name: '查看滚动验证', exact: true }).click()
  await expect(detail.getByText('完成测试 2/2 折', { exact: true })).toBeVisible()
  await row.getByRole('button', { name: '删除滚动验证', exact: true }).click()
  await panel.getByRole('button', { name: '确认删除滚动验证', exact: true }).click()
  await expect(row).toHaveCount(0)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: actual backtest -> frozen temporal plan -> pause/resume -> 2 train-only selections -> 2 untouched test folds -> OOS curve/evidence/export -> reload/delete')
} finally {
  await browser.close()
}
