import { chromium, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const base = process.env.TRADE_SMOKE_URL || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1550, height: 1080 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const prefix = `portfolio-${Date.now()}`
  let serial = 0
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `${prefix}-${++serial}` } })
    if (!response.ok()) throw new Error(await response.text())
    return (await response.json()).data
  }
  async function get(path) { const response = await page.request.get(base + '/api/v1' + path); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  const relative = (await get('/research/strategies')).find(item => item.id === 'relative_strength_breakout_v1')
  const sources = []
  const offset = Date.now() % 1000
  for (let stock = 0; stock < 2; stock++) {
    const bars = Array.from({ length: 96 }, (_, index) => {
      const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10), close = 10 + stock + index * .03
      return { event_date: day, open: (close - .01).toFixed(4), high: (close + .01).toFixed(4), low: (close - .02).toFixed(4), close: close.toFixed(4), volume: (index % 7 === 0 ? 5000 : 1000 + index) + offset, available_at: `${day}T07:00:00+00:00` }
    })
    sources.push(await post('/market/datasets', { symbol: `sh60002${stock}`, bars }))
  }
  const closes = [...Array.from({ length: 60 }, (_, index) => 12 - index * 2 / 59),
    9.3, 9.5, 9.7, 9.9, 10.1, 10.3, 10.5, 10.7, 10.5, 10.3, 10.1, 9.9, 9.7, 9.5, 9.5, 9.6, 9.6, 9.6, 9.6, 9.6,
    9.2, 9.5, 10.2, 10.8, 11.2, 11.1, 11.2, 11.3, 11.4, 11.5]
  const eventBars = [], volumes = { 58: 1800, 60: 10000, 74: 800, 80: 2000, 82: 1600, 83: 1800, 84: 2200, 85: 600 }
  let date = new Date(Date.UTC(2025, 0, 1))
  for (let index = 0; index < closes.length; index++) {
    while (date.getUTCDay() === 0 || date.getUTCDay() === 6) date.setUTCDate(date.getUTCDate() + 1)
    const day = date.toISOString().slice(0, 10), close = closes[index], opening = index ? closes[index - 1] : close
    eventBars.push({ event_date: day, open: opening.toFixed(4), close: close.toFixed(4),
      high: (index === 60 ? 10.2 : Math.max(opening, close) + .15).toFixed(4),
      low: (index === 60 || index === 74 ? 9 : index === 80 ? 8.8 : Math.min(opening, close) - .15).toFixed(4),
      volume: volumes[index] || 1000, available_at: `${day}T08:00:00+00:00` })
    date.setUTCDate(date.getUTCDate() + 1)
  }
  const eventSource = await post('/market/datasets', { symbol: 'sh600023', bars: eventBars })
  const systemProfile = (await get('/research/event-profiles')).profiles.find(item => item.revision === 0)
  await page.goto(base + '/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('组合验收账户')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '组合回测', exact: true }).click()
  const panel = page.getByRole('region', { name: '固定样本组合回测', exact: true })
  const control = (label, tag = 'input') => panel.locator('label').filter({ has: page.getByText(label, { exact: true }) }).locator(tag)
  const step = label => panel.getByRole('button', { name: new RegExp(label) }).click()
  await step('预览提交')
  await expect(panel.getByText(/历史市场成分、退市与复权未经核验/)).toBeVisible()
  await step('样本与日期')
  for (const source of sources) await panel.getByLabel(`组合样本 ${source.symbol} ${source.id.slice(0, 8)}`, { exact: true }).check()
  await control('组合开始日期').fill('2025-03-02')
  await step('退出与费用')
  await control('最长持有日线数').fill('3')
  const results = []
  for (const mode of ['matrix_raw_s1_s9', 'traditional_runtime14', 'aligned_wyckoff_events']) {
    await panel.getByRole('tab', { name: '配置组合', exact: true }).click()
    await step('样本与日期')
    await control('组合实验名称').fill(`${prefix}-${mode}`)
    await step('信号策略')
    await control('组合信号路径', 'select').selectOption(mode)
    if (mode === 'traditional_runtime14') {
      await control('组合传统策略', 'select').selectOption('relative_strength_breakout_v1')
      const parameterBox = panel.getByRole('heading', { name: '传统策略参数与预设', exact: true }).locator('..')
      await parameterBox.locator('label').filter({ has: page.getByText(relative.params_schema.min_ret40.title, { exact: true }) }).locator('input').fill('.01')
      await parameterBox.locator('label').filter({ has: page.getByText(relative.params_schema.min_vol_slope20.title, { exact: true }) }).locator('input').fill('0')
    }
    if (mode === 'aligned_wyckoff_events') {
      await step('样本与日期')
      for (const source of sources) await panel.getByLabel(`组合样本 ${source.symbol} ${source.id.slice(0, 8)}`, { exact: true }).uncheck()
      await panel.getByLabel(`组合样本 ${eventSource.symbol} ${eventSource.id.slice(0, 8)}`, { exact: true }).check()
      await control('组合开始日期').fill(eventBars[60].event_date)
      await step('信号策略')
      await control('组合冻结事件模板', 'select').selectOption(systemProfile.profile_id)
      await control('事件入场质量下限').fill('0')
      await control('最少事件数量').fill('0')
      for (const event of ['PS', 'SC', 'AR', 'ST', 'TSO']) await panel.getByLabel(`入场事件 ${event}`, { exact: true }).check()
      await expect(panel.getByRole('region', { name: '事件矩阵参数', exact: true }).getByText(/不把后续确认回写到过去/)).toBeVisible()
      await step('资金与执行')
      await control('买入延迟日线数 1–5').fill('2')
    }
    const days = mode === 'aligned_wyckoff_events' ? 30 : 36, stocks = mode === 'aligned_wyckoff_events' ? 1 : 2
    await step('预览提交')
    await panel.getByRole('button', { name: '预览组合冻结计划', exact: true }).click()
    const preview = panel.getByRole('region', { name: '组合冻结计划', exact: true })
    await expect(preview.getByText(new RegExp(`${stocks} 个固定研究样本 · ${days} 个交易日期`))).toBeVisible()
    const creation = page.waitForResponse(response => response.url() === base + '/api/v1/research/portfolios' && response.request().method() === 'POST')
    await panel.getByRole('button', { name: '确认创建组合回测', exact: true }).click()
    const response = await creation
    if (!response.ok()) throw new Error(await response.text())
    const created = (await response.json()).data
    const detail = panel.getByRole('region', { name: '组合结果', exact: true })
    if (mode === 'matrix_raw_s1_s9') {
      await detail.getByRole('button', { name: '暂停组合', exact: true }).click()
      await expect.poll(async () => (await get('/research/portfolios/' + created.id)).state, { timeout: 15000 }).toBe('paused')
      await detail.getByRole('button', { name: '续跑组合', exact: true }).click()
    }
    await expect.poll(async () => { const run = await get('/research/portfolios/' + created.id); if (run.state === 'failed') throw new Error(run.error); return run.state }, { timeout: 60000, intervals: [500, 1000] }).toBe('succeeded')
    await panel.getByRole('button', { name: '刷新组合历史', exact: true }).click()
    await panel.getByRole('tab', { name: /^任务历史/ }).click()
    const row = panel.getByRole('table', { name: '组合任务历史', exact: true }).locator('tbody tr').filter({ hasText: `${prefix}-${mode}` })
    await row.getByRole('button', { name: '查看组合', exact: true }).click()
    await detail.getByRole('button', { name: '加载组合曲线与成交证据', exact: true }).click()
    const evidence = panel.getByRole('region', { name: '组合成交证据', exact: true })
    await expect(evidence.getByRole('img', { name: '组合权益曲线', exact: true })).toBeVisible()
    if (await evidence.getByRole('img', { name: '组合权益曲线', exact: true }).locator('path').evaluate(path => getComputedStyle(path).stroke) === 'none') throw new Error('Portfolio curve has no visible stroke')
    await expect(evidence.getByRole('table', { name: '组合股票池信号', exact: true }).locator('tbody tr')).toHaveCount(stocks)
    const result = await get('/research/portfolios/' + created.id + '/result')
    if (result.chunk_count !== Math.ceil(days / 5) || result.completed_days !== days || !result.result.buy_count) throw new Error('Missing complete checkpointed portfolio evidence')
    if (mode === 'aligned_wyckoff_events' && (result.frozen_context.event_profile.revision !== 0 || !result.result.trades.filter(item => item.side === 'buy').every(item => item.reason_metrics.components.entry_events.length))) throw new Error('Missing frozen event profile or occurrence evidence')
    if (!result.result.quality_flags.includes('selection_membership_unverified')) throw new Error('Fixed sample quality flag missing')
    if (result.result.equity.some(item => Number(item.cash) < 0)) throw new Error('Negative portfolio cash')
    if (result.result.trades.filter(item => item.side === 'buy').some(item => item.signal_date >= item.date || item.known_at > item.execution_at)) throw new Error('Signal execution is not point in time')
    const downloadEvent = page.waitForEvent('download')
    await detail.getByRole('link', { name: '导出组合 JSON', exact: true }).click()
    const exported = JSON.parse(await readFile(await (await downloadEvent).path(), 'utf8'))
    if (exported.result_sha256 !== result.result_sha256 || exported.result.equity.length !== days) throw new Error('Export digest mismatch')
    await panel.getByRole('button', { name: '保存到组合报告库', exact: true }).click()
    const reports = page.getByRole('region', { name: '组合报告库', exact: true })
    await reports.getByLabel('组合报告标题', { exact: true }).fill(`${prefix}-${mode}-report`)
    const saveReport = page.waitForResponse(reply => reply.url() === base + '/api/v1/research/portfolio-reports' && reply.request().method() === 'POST')
    await reports.getByRole('button', { name: '将当前组合保存为报告', exact: true }).click()
    const savedReply = await saveReport
    if (!savedReply.ok()) throw new Error(await savedReply.text())
    const savedReport = (await savedReply.json()).data
    if (savedReport.payload.chunks.length !== Math.ceil(days / 5) || savedReport.payload.input.datasets.length !== stocks) throw new Error('Report omitted checkpoints or input datasets')
    const reportDetail = reports.getByRole('region', { name: '组合报告详情', exact: true })
    await expect(reportDetail.getByRole('img', { name: '冻结组合报告权益曲线', exact: true })).toBeVisible()
    await expect(reportDetail.getByRole('table', { name: '冻结组合报告成交', exact: true }).locator('tbody tr')).toHaveCount(Math.min(50, result.result.trades.length))
    let reportZip
    for (const label of ['下载完整组合 ZIP', '下载组合离线 HTML', '下载组合 Excel']) {
      const download = page.waitForEvent('download')
      await reportDetail.getByRole('link', { name: label, exact: true }).click()
      const content = await readFile(await (await download).path())
      if (label.endsWith('HTML')) {
        if (!content.toString('utf8').includes('Content-Security-Policy')) throw new Error('Missing report CSP')
      } else if (content.subarray(0, 2).toString() !== 'PK') throw new Error('Invalid report ZIP/XLSX')
      if (label.endsWith('ZIP')) reportZip = content
    }
    if (mode === 'matrix_raw_s1_s9') {
      const reportRow = reports.getByRole('table', { name: '组合报告目录', exact: true }).locator('tbody tr').filter({ hasText: `${prefix}-${mode}-report` })
      await reportRow.getByRole('button', { name: '删除组合报告', exact: true }).click()
      await reports.getByRole('button', { name: '确认删除组合报告', exact: true }).click()
      await expect(reportRow).toHaveCount(0)
      await reports.getByText('导入完整组合报告包', { exact: true }).click()
      await reports.getByLabel('组合报告 ZIP 文件', { exact: true }).setInputFiles({ name: 'portfolio.zip', mimeType: 'application/zip', buffer: reportZip })
      await reports.getByRole('button', { name: '校验并导入组合报告', exact: true }).click()
      await expect(reportDetail.getByText(/导入快照，未重新计算/)).toBeVisible()
      await expect(reportRow).toHaveCount(1)
    }
    results.push(created)
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '组合回测', exact: true }).click()
  }
  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '组合回测', exact: true }).click()
  await panel.getByRole('tab', { name: /^任务历史/ }).click()
  const row = panel.getByRole('table', { name: '组合任务历史', exact: true }).locator('tbody tr').filter({ hasText: `${prefix}-matrix_raw_s1_s9` })
  await row.getByRole('button', { name: '查看组合', exact: true }).click()
  await panel.getByRole('button', { name: '删除组合', exact: true }).click()
  await panel.getByRole('button', { name: '确认删除组合', exact: true }).click()
  await expect(row).toHaveCount(0)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: real datasets -> raw / traditional / aligned portfolio -> preview / pause / checkpoints / evidence -> full reports in all modes -> ZIP / HTML / Excel -> delete / reimport / history reload')
} finally {
  await browser.close()
}
