import { chromium, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const base = process.env.TRADE_SMOKE_URL || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1550, height: 1080 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const prefix = `portfolio-analysis-${Date.now()}`; let serial = 0
  async function post(path, body) { const response = await page.request.post(base + '/api/v1' + path, { data: body, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `${prefix}-${++serial}` } }); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  async function get(path) { const response = await page.request.get(base + '/api/v1' + path); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  const datasets = []
  for (let stock = 0; stock < 2; stock++) {
    const bars = Array.from({ length: 105 }, (_, index) => { const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10), close = 10 + stock + index * .03; return { event_date: day, open: (close - .01).toFixed(4), high: (close + .01).toFixed(4), low: (close - .02).toFixed(4), close: close.toFixed(4), volume: (index % 7 === 0 ? 5000 : 1000 + index) + Date.now() % 100, available_at: `${day}T07:00:00+00:00` } })
    datasets.push(await post('/market/datasets', { symbol: `sh60007${stock}`, bars }))
  }
  const body = { dataset_ids: datasets.map(row => row.id), mode: 'traditional_runtime14', strategy_id: 'relative_strength_breakout_v1', params: { min_ret40: '.01', min_vol_slope20: '0' }, start_date: '2025-03-02', config: { max_holding_bars: 3 } }
  const preview = await post('/research/portfolios/preview', body)
  const source = await post('/research/portfolios', { ...body, name: `${prefix}-source`, expected_preview_sha256: preview.preview_sha256 })
  await expect.poll(async () => { const row = await get('/research/portfolios/' + source.id); if (row.state === 'failed') throw new Error(row.error); return row.state }, { timeout: 90000 }).toBe('succeeded')
  await page.goto(base + '/rebuild.html'); await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) { await page.getByRole('textbox', { name: '账户名称' }).fill('组合分析验收账户'); await page.getByRole('button', { name: '创建实盘账户' }).click() }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '组合分析与计划', exact: true }).click()
  const panel = page.getByRole('region', { name: '组合高级分析', exact: true })
  await panel.locator('label').filter({ has: page.getByText('分析来源组合', { exact: true }) }).locator('select').selectOption(source.id)
  await panel.getByLabel('组合分析名称', { exact: true }).fill(prefix)
  await panel.getByLabel('Bootstrap 抽样次数', { exact: true }).fill('100')
  await expect(panel.getByLabel('持仓与条件计划日期', { exact: true })).toHaveValue('2025-04-15')
  await panel.getByLabel('持仓与条件计划日期', { exact: true }).fill('2025-03-08')
  const response = page.waitForResponse(reply => reply.url() === base + '/api/v1/research/portfolio-analyses' && reply.request().method() === 'POST')
  await panel.getByRole('button', { name: '创建组合分析与条件计划', exact: true }).click()
  const reply = await response; if (!reply.ok()) throw new Error(await reply.text()); const created = (await reply.json()).data
  await expect.poll(async () => { const row = await get('/research/portfolio-analyses/' + created.id); if (row.state === 'failed') throw new Error(row.error); return row.state }, { timeout: 90000 }).toBe('succeeded')
  await panel.getByRole('button', { name: '刷新组合分析', exact: true }).click()
  const history = panel.getByRole('table', { name: '组合分析历史', exact: true }).locator('tbody tr').filter({ hasText: prefix })
  await history.getByRole('button', { name: '查看组合分析', exact: true }).click()
  const detail = panel.getByRole('region', { name: '组合高级分析详情', exact: true })
  await expect(detail.getByRole('table', { name: '组合风险指标', exact: true })).toBeVisible()
  await expect(detail.getByRole('table', { name: '组合 Bootstrap 分位数', exact: true })).toBeVisible()
  await expect(detail.getByRole('table', { name: '组合条件计划', exact: true })).toBeVisible()
  const complete = await get('/research/portfolio-analyses/' + created.id)
  if (!complete.result_sha256 || complete.result.monte_carlo.iterations !== 100 || complete.result.daily_plan.as_of_date !== '2025-03-08' || complete.result.daily_plan.status !== 'generated') throw new Error('Incomplete frozen analysis')
  if (complete.result.daily_plan.plan_signals.some(row => row.price !== null || row.source_date > '2025-03-08')) throw new Error('Plan contains future or fabricated fill')
  const reportResponse = page.waitForResponse(reply => reply.url() === base + '/api/v1/research/portfolio-reports' && reply.request().method() === 'POST')
  await detail.getByRole('button', { name: '冻结含分析和条件计划的组合报告', exact: true }).click()
  const reportReply = await reportResponse; if (!reportReply.ok()) throw new Error(await reportReply.text()); const report = (await reportReply.json()).data
  if (report.payload.version !== 2 || report.payload.analysis.result_sha256 !== complete.result_sha256) throw new Error('Report lost frozen analysis')
  const zipDownload = page.waitForEvent('download'); await detail.getByRole('link', { name: '下载含分析完整 ZIP', exact: true }).click()
  const zip = await readFile(await (await zipDownload).path()); if (zip.readUInt32LE(0) !== 0x04034b50) throw new Error('Invalid report ZIP')
  const htmlResponse = await page.request.get(base + `/api/v1/research/portfolio-reports/${report.id}/report.html`)
  const html = await htmlResponse.text(); if (!htmlResponse.ok() || !html.includes('Bootstrap') || !html.includes('日终条件计划')) throw new Error('HTML missing analysis')
  const download = page.waitForEvent('download'); await detail.getByRole('link', { name: '导出组合分析 JSON', exact: true }).click()
  const exported = JSON.parse(await readFile(await (await download).path(), 'utf8')); if (exported.result_sha256 !== complete.result_sha256) throw new Error('Analysis export mismatch')
  await page.goto(base + `/rebuild.html?page=backtest&task=portfolio_analysis:${created.id}`); await page.waitForLoadState('networkidle')
  await expect(detail.getByRole('heading', { name: `${prefix} · 已完成`, exact: true })).toBeVisible()
  await detail.getByRole('button', { name: '删除组合分析', exact: true }).click()
  await detail.getByRole('button', { name: '确认删除组合分析', exact: true }).click()
  await expect(history).toHaveCount(0)
  const retained = await get('/research/portfolio-reports/' + report.id)
  if (retained.payload.analysis.result_sha256 !== complete.result_sha256) throw new Error('Analysis deletion changed report')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: actual 2-stock portfolio -> persisted risk/block-bootstrap -> exact-date holdings/causal plan -> reportv2 ZIP/HTML -> JSON -> exact deep link -> analysis deletion preserves frozen report')
} finally { await browser.close() }
