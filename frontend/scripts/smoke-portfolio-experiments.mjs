import { chromium, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const base = process.env.TRADE_SMOKE_URL || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1550, height: 1080 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const prefix = `portfolio-params-${Date.now()}`; let serial = 0
  async function post(path, body) { const response = await page.request.post(base + '/api/v1' + path, { data: body, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `${prefix}-${++serial}` } }); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  async function get(path) { const response = await page.request.get(base + '/api/v1' + path); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  const datasets = []
  for (let stock = 0; stock < 2; stock++) {
    const bars = Array.from({ length: 80 }, (_, index) => { const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10), close = 10 + stock + index * .03; return { event_date: day, open: (close - .01).toFixed(4), high: (close + .01).toFixed(4), low: (close - .02).toFixed(4), close: close.toFixed(4), volume: (index % 7 === 0 ? 5000 : 1000 + index) + Date.now() % 100, available_at: `${day}T07:00:00+00:00` } })
    datasets.push(await post('/market/datasets', { symbol: `sh60004${stock}`, bars }))
  }
  const body = { dataset_ids: datasets.map(row => row.id), mode: 'matrix_raw_s1_s9', start_date: '2025-03-02', config: { max_holding_bars: 3, trailing_stop_pct: '.04' } }
  const preview = await post('/research/portfolios/preview', body)
  const source = await post('/research/portfolios', { ...body, name: `${prefix}-source`, expected_preview_sha256: preview.preview_sha256 })
  await expect.poll(async () => { const row = await get('/research/portfolios/' + source.id); if (row.state === 'failed') throw new Error(row.error); return row.state }, { timeout: 60000 }).toBe('succeeded')
  await page.goto(base + '/rebuild.html'); await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) { await page.getByRole('textbox', { name: '账户名称' }).fill('组合参数验收账户'); await page.getByRole('button', { name: '创建实盘账户' }).click() }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '组合收益平原', exact: true }).click()
  const panel = page.getByRole('region', { name: '组合收益平原', exact: true })
  await panel.locator('label').filter({ has: page.getByText('组合实验来源', { exact: true }) }).locator('select').selectOption(source.id)
  await panel.getByLabel('组合实验名称', { exact: true }).fill(prefix)
  await panel.getByLabel('组合取值 1', { exact: true }).fill('2,3')
  await panel.getByRole('button', { name: '增加组合维度', exact: true }).click()
  await panel.getByLabel('组合取值 2', { exact: true }).fill('.5,1')
  await panel.getByRole('button', { name: '预览组合参数计划', exact: true }).click()
  await expect(panel.getByRole('region', { name: '组合参数计划', exact: true }).getByText(/4 个完整候选/)).toBeVisible()
  const response = page.waitForResponse(reply => reply.url() === base + '/api/v1/research/portfolio-experiments' && reply.request().method() === 'POST')
  await panel.getByRole('button', { name: '确认创建组合实验', exact: true }).click()
  const reply = await response; if (!reply.ok()) throw new Error(await reply.text()); const created = (await reply.json()).data
  const detail = panel.getByRole('region', { name: '组合实验详情', exact: true })
  await detail.getByRole('button', { name: '暂停参数实验', exact: true }).click()
  await expect.poll(async () => (await get('/research/portfolio-experiments/' + created.id)).state, { timeout: 15000 }).toBe('paused')
  await detail.getByRole('button', { name: '续跑参数实验', exact: true }).click()
  await expect.poll(async () => { const run = await get('/research/portfolio-experiments/' + created.id); if (run.state === 'failed') throw new Error(run.error); return run.state }, { timeout: 120000, intervals: [1000] }).toBe('succeeded')
  await panel.getByRole('button', { name: '刷新组合实验', exact: true }).click()
  const history = panel.getByRole('table', { name: '组合参数实验历史', exact: true }).locator('tbody tr').filter({ hasText: prefix })
  await history.getByRole('button', { name: '查看组合实验', exact: true }).click()
  await expect(detail.getByRole('table', { name: '组合参数候选', exact: true }).locator('tbody tr')).toHaveCount(4)
  await expect(detail.getByRole('img', { name: '组合参数收益切片', exact: true })).toBeVisible()
  const complete = await get('/research/portfolio-experiments/' + created.id)
  if (complete.completed_points !== 4 || complete.points.some(row => row.chunk_count !== 4 || row.completed_days !== 20) || !complete.result_sha256) throw new Error('Incomplete candidate checkpoint chain')
  const ordinary = await get('/research/portfolios'); if (complete.points.some(point => ordinary.some(row => row.id === point.child_run_id))) throw new Error('Internal candidates leaked into ordinary portfolio history')
  await detail.getByRole('table', { name: '组合参数候选', exact: true }).locator('tbody tr').first().getByRole('button', { name: '查看候选组合', exact: true }).click()
  const candidate = detail.getByRole('region', { name: '组合候选详情', exact: true })
  await expect(candidate.getByRole('table', { name: '组合候选成交', exact: true })).toBeVisible()
  const report = candidate.getByRole('region', { name: '组合报告库', exact: true })
  await report.getByRole('button', { name: '将当前组合保存为报告', exact: true }).click()
  await expect(report.getByRole('region', { name: '组合报告详情', exact: true })).toBeVisible()
  const download = page.waitForEvent('download'); await detail.getByRole('link', { name: '导出组合参数分析', exact: true }).click()
  const exported = JSON.parse(await readFile(await (await download).path(), 'utf8')); if (exported.result_sha256 !== complete.result_sha256) throw new Error('Experiment export digest mismatch')
  await page.reload(); await page.waitForLoadState('networkidle'); await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '组合收益平原', exact: true }).click()
  await history.getByRole('button', { name: '删除组合实验', exact: true }).click(); await panel.getByRole('button', { name: '确认删除组合实验', exact: true }).click(); await expect(history).toHaveCount(0)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: frozen portfolio -> 4 grid candidates -> pause/resume -> 16 child checkpoints -> full-cycle scores/heatmap/candidate evidence -> report -> export/reload/delete')
} finally { await browser.close() }
