import { chromium, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const base = process.env.TRADE_SMOKE_URL || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1550, height: 1080 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const prefix = `portfolio-wf-${Date.now()}`; let serial = 0
  async function post(path, body) { const response = await page.request.post(base + '/api/v1' + path, { data: body, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `${prefix}-${++serial}` } }); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  async function get(path) { const response = await page.request.get(base + '/api/v1' + path); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  const datasets = []
  for (let stock = 0; stock < 2; stock++) {
    const bars = Array.from({ length: 120 }, (_, index) => { const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10), close = 10 + stock + index * .03; return { event_date: day, open: (close - .01).toFixed(4), high: (close + .01).toFixed(4), low: (close - .02).toFixed(4), close: close.toFixed(4), volume: (index % 7 === 0 ? 5000 : 1000 + index) + Date.now() % 100, available_at: `${day}T07:00:00+00:00` } })
    datasets.push(await post('/market/datasets', { symbol: `sh60005${stock}`, bars }))
  }
  const body = { dataset_ids: datasets.map(row => row.id), mode: 'traditional_runtime14', strategy_id: 'relative_strength_breakout_v1', params: { min_ret40: '.01', min_vol_slope20: '0' }, start_date: '2025-03-02', config: { max_holding_bars: 3 } }
  const preview = await post('/research/portfolios/preview', body)
  const source = await post('/research/portfolios', { ...body, name: `${prefix}-source`, expected_preview_sha256: preview.preview_sha256 })
  await expect.poll(async () => { const row = await get('/research/portfolios/' + source.id); if (row.state === 'failed') throw new Error(row.error); return row.state }, { timeout: 90000 }).toBe('succeeded')
  await page.goto(base + '/rebuild.html'); await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) { await page.getByRole('textbox', { name: '账户名称' }).fill('组合WF验收账户'); await page.getByRole('button', { name: '创建实盘账户' }).click() }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '组合 Walk-forward', exact: true }).click()
  const panel = page.getByRole('region', { name: '组合滚动验证', exact: true })
  await panel.locator('label').filter({ has: page.getByText('组合WF来源', { exact: true }) }).locator('select').selectOption(source.id)
  await panel.getByLabel('组合WF名称', { exact: true }).fill(prefix)
  await panel.getByLabel('组合WF取值 1', { exact: true }).fill('2,5')
  await panel.getByLabel('组合首次训练日期数', { exact: true }).fill('32')
  await panel.getByLabel('组合每折测试日期数', { exact: true }).fill('10')
  await panel.getByLabel('组合最多折数', { exact: true }).fill('2')
  await panel.getByRole('button', { name: '预览组合滚动计划', exact: true }).click()
  await expect(panel.getByRole('region', { name: '组合WF冻结计划', exact: true }).getByText(/2 折 · 每折 2 个训练候选/)).toBeVisible()
  await expect(panel.getByRole('region', { name: '组合WF冻结计划', exact: true }).getByRole('table', { name: '组合滚动时间折', exact: true }).locator('tbody tr')).toHaveCount(2)
  const response = page.waitForResponse(reply => reply.url() === base + '/api/v1/research/portfolio-walk-forwards' && reply.request().method() === 'POST')
  await panel.getByRole('button', { name: '确认创建组合滚动验证', exact: true }).click()
  const reply = await response; if (!reply.ok()) throw new Error(await reply.text()); const created = (await reply.json()).data
  const detail = panel.getByRole('region', { name: '组合滚动验证详情', exact: true })
  await detail.getByRole('button', { name: '暂停组合WF', exact: true }).click()
  await expect.poll(async () => (await get('/research/portfolio-walk-forwards/' + created.id)).state, { timeout: 15000 }).toBe('paused')
  await detail.getByRole('button', { name: '续跑组合WF', exact: true }).click()
  await expect.poll(async () => { const run = await get('/research/portfolio-walk-forwards/' + created.id); if (run.state === 'failed') throw new Error(run.error); return run.state }, { timeout: 180000, intervals: [1000] }).toBe('succeeded')
  await panel.getByRole('button', { name: '刷新组合WF', exact: true }).click()
  const history = panel.getByRole('table', { name: '组合WF历史', exact: true }).locator('tbody tr').filter({ hasText: prefix })
  await history.getByRole('button', { name: '查看组合WF', exact: true }).click()
  await expect(detail.getByRole('table', { name: '组合WF阶段', exact: true }).locator('tbody tr')).toHaveCount(6)
  await expect(detail.getByRole('img', { name: '组合样本外归一化曲线', exact: true })).toBeVisible()
  const complete = await get('/research/portfolio-walk-forwards/' + created.id)
  if (complete.completed_tasks !== 6 || complete.result.completed_test_folds !== 2 || complete.result.oos_normalized_curve.length !== 20 || !complete.result_sha256) throw new Error('Incomplete temporal validation')
  for (const fold of complete.folds) {
    const task = complete.tasks.find(item => item.fold_index === fold.index && item.phase === 'test')
    if (!fold.selection_sha256 || JSON.stringify(task.axis_values) !== JSON.stringify(fold.selection.axis_values)) throw new Error('Test does not match immutable training choice')
    const child = await get('/research/portfolios/' + task.child_run_id + '/result')
    if (child.frozen_context.start_date !== fold.test_start || child.result.equity.length !== 10 || child.result.trades.some(trade => trade.date < fold.test_start)) throw new Error('Warmup created a test trade or date leak')
  }
  const testRow = detail.getByRole('table', { name: '组合WF阶段', exact: true }).locator('tbody tr').filter({ hasText: '后续测试' }).first()
  await testRow.getByRole('button', { name: '查看阶段组合', exact: true }).click()
  const taskDetail = detail.getByRole('region', { name: '组合WF阶段详情', exact: true })
  await expect(taskDetail.getByText(/独立测试/)).toBeVisible()
  const reports = taskDetail.getByRole('region', { name: '组合报告库', exact: true })
  await reports.getByRole('button', { name: '将当前组合保存为报告', exact: true }).click()
  await expect(reports.getByRole('region', { name: '组合报告详情', exact: true })).toBeVisible()
  const download = page.waitForEvent('download'); await detail.getByRole('link', { name: '导出组合WF分析', exact: true }).click()
  const exported = JSON.parse(await readFile(await (await download).path(), 'utf8')); if (exported.result_sha256 !== complete.result_sha256) throw new Error('WF export digest mismatch')
  await page.goto(base + `/rebuild.html?page=backtest&task=portfolio_walk_forward:${created.id}`)
  await page.waitForLoadState('networkidle')
  await expect(panel).toBeVisible()
  await expect(detail.getByRole('heading', { name: `${prefix} · 已完成`, exact: true })).toBeVisible()
  await history.getByRole('button', { name: '删除组合WF', exact: true }).click(); await panel.getByRole('button', { name: '确认删除组合WF', exact: true }).click(); await expect(history).toHaveCount(0)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: actual fixed portfolio -> 2 train-only temporal folds -> pause/resume -> 6 immutable stages -> 20 OOS dates -> stage report -> export -> exact-task deep link/delete')
} finally { await browser.close() }
