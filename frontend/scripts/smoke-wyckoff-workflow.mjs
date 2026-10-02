import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 }, timezoneId: 'Asia/Shanghai' })
  page.setDefaultTimeout(8000)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = 'http://127.0.0.1:8011'
  const session = (await (await page.request.get(`${base}/api/v1/session`)).json()).data
  const serial = Date.now()
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, {
      data: body, headers: { 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': crypto.randomUUID() },
    })
    assert(response.ok(), await response.text())
    return (await response.json()).data
  }
  const closes = [...Array.from({ length: 60 }, (_, i) => 12 - i * 2 / 59),
    9.3, 9.5, 9.7, 9.9, 10.1, 10.3, 10.5, 10.7, 10.5, 10.3, 10.1, 9.9, 9.7, 9.5, 9.5,
    9.6, 9.6, 9.6, 9.6, 9.6, 9.2, 9.5, 10.2, 10.8, 11.2, 11.1, 11.2, 11.3, 11.4, 11.5]
  const volumes = { 58: 1800, 60: 10000, 74: 800, 80: 2000, 82: 1600, 83: 1800, 84: 2200, 85: 600 }
  const day = new Date('2025-01-01T00:00:00Z')
  const bars = closes.map((close, i) => {
    while ([0, 6].includes(day.getUTCDay())) day.setUTCDate(day.getUTCDate() + 1)
    const date = day.toISOString().slice(0, 10)
    day.setUTCDate(day.getUTCDate() + 1)
    const open = closes[i - 1] || close
    return { event_date: date, open: open.toFixed(4), close: close.toFixed(4),
      high: (i === 60 ? 10.2 : Math.max(open, close) + 0.15).toFixed(4),
      low: ([60, 74].includes(i) ? 9 : i === 80 ? 8.8 : Math.min(open, close) - 0.15).toFixed(4),
      volume: volumes[i] || 1000, available_at: `${date}T08:00:00+00:00` }
  })
  const ds = await post('/market/datasets', { symbol: 'sh600000', bars })
  const future = Array.from({ length: 7 }, (_, i) => {
    while ([0, 6].includes(day.getUTCDay())) day.setUTCDate(day.getUTCDate() + 1)
    const date = day.toISOString().slice(0, 10); day.setUTCDate(day.getUTCDate() + 1)
    const close = 11.6 + i * 0.1
    return { event_date: date, open: (close - 0.05).toFixed(4), high: (close + 0.1).toFixed(4), low: (close - 0.1).toFixed(4), close: close.toFixed(4), volume: 1000, available_at: `${date}T08:00:00+00:00` }
  })
  const forward = await post('/market/datasets', { symbol: '600000.SH', bars: [...bars, ...future] })
  await post('/accounts', { name: `策略联调 ${serial}` })
  const catalog = (await (await page.request.get(`${base}/api/v1/research/strategies`)).json()).data
  await page.goto(`${base}/rebuild.html`)
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '事件模板', exact: true }).click()
  const templates = page.getByRole('region', { name: '事件判定模板', exact: true })
  await templates.getByLabel('查看或编辑模板').selectOption('system_legacy_formula_v1')
  await expect(templates.getByLabel('模板名称')).toBeDisabled()
  await templates.getByRole('button', { name: '复制为自定义模板' }).click()
  await templates.getByLabel('模板名称').fill(`浏览器模板 ${serial}`)
  let responsePromise = page.waitForResponse(response => response.url().endsWith('/research/event-profiles') && response.request().method() === 'POST')
  await templates.getByRole('button', { name: '保存自定义模板' }).click()
  let response = await responsePromise
  assert(response.ok(), await response.text())
  const profile = (await response.json()).data
  await templates.getByRole('button', { name: '设为当前模板' }).click()
  await expect(templates.getByRole('button', { name: '设为当前模板' })).toBeDisabled()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  const form = page.getByRole('heading', { name: '固定样本信号' }).locator('..')
  await form.getByLabel('策略', { exact: true }).selectOption('wyckoff_trend_v2')
  await form.getByLabel('冻结行情样本').selectOption(ds.id)
  await form.getByLabel('决策时间').fill(`${bars.at(-1).event_date}T17:00`)
  await form.getByLabel('本次事件模板').selectOption(profile.profile_id)
  await expect(form.getByLabel('事件等级下限')).toHaveValue('B')
  responsePromise = page.waitForResponse(response => response.url().endsWith('/research/runs') && response.request().method() === 'POST')
  await form.getByRole('button', { name: '运行信号判断' }).click()
  response = await responsePromise
  assert(response.ok(), await response.text())
  const run = (await response.json()).data
  assert.equal(run.result.indicator.entry_quality_score, 71.9)
  assert.equal(run.result.event_profile.profile_id, profile.profile_id)
  const detail = page.getByRole('heading', { name: /^运行结果 ·/ }).locator('..')
  await expect(detail.getByRole('heading', { name: '维科夫事件与评分' })).toBeVisible()
  await expect(detail.getByText('入场质量分：71.9', { exact: true })).toBeVisible()
  await expect(detail.getByRole('cell', { name: '已确认', exact: true }).first()).toBeVisible()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '区间扫描', exact: true }).click()
  const scan = page.getByRole('region', { name: '多策略扫描', exact: true })
  await scan.getByRole('button', { name: '刷新历史与模板' }).click()
  await scan.getByLabel(`选择行情 ${ds.symbol} ${ds.id.slice(0, 8)}`, { exact: true }).check()
  for (const id of ['wyckoff_trend_v1', 'score_only_rank_v1']) {
    await scan.getByLabel(catalog.find(row => row.id === id).name, { exact: true }).check()
  }
  await scan.getByLabel('扫描日期').fill(bars.at(-1).event_date)
  await scan.getByLabel('事件判定模板').selectOption(profile.profile_id)
  responsePromise = page.waitForResponse(response => response.url().endsWith('/research/scan-jobs') && response.request().method() === 'POST')
  await scan.getByRole('button', { name: '运行策略扫描', exact: true }).click()
  response = await responsePromise
  assert(response.ok(), await response.text())
  const scanJob = (await response.json()).data
  let finishedScan
  await expect.poll(async () => {
    finishedScan = (await (await page.request.get(base + '/api/v1/research/scan-jobs/' + scanJob.id)).json()).data
    if (finishedScan.state === 'failed') throw new Error(finishedScan.error)
    return finishedScan.state
  }, { timeout: 30000 }).toBe('succeeded')
  const scanRun = (await (await page.request.get(base + '/api/v1/research/scans/' + finishedScan.scan_id)).json()).data
  assert.equal(scanRun.intersection_count, 1)
  await expect(scan.getByText('交集 1 个证券日', { exact: true })).toBeVisible()
  await scan.getByLabel('结果视图').selectOption('evidence')
  await expect(scan.getByRole('button', { name: '研究证据', exact: true })).toHaveCount(2)
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '策略交叉验证', exact: true }).click()
  const basketPanel = page.getByRole('region', { name: '信号篮子', exact: true })
  await basketPanel.getByRole('button', { name: '刷新篮子与来源' }).click()
  await basketPanel.getByLabel('篮子名称').fill(`策略篮子 ${serial}`)
  await basketPanel.getByLabel('来源方式').selectOption('scan')
  await basketPanel.getByLabel('来源扫描').selectOption(scanRun.id)
  await basketPanel.getByLabel('自动选取范围').selectOption('intersection')
  await basketPanel.getByLabel('持有期（样本交易日）').fill('3')
  responsePromise = page.waitForResponse(response => response.url().endsWith('/research/baskets') && response.request().method() === 'POST')
  await basketPanel.getByRole('button', { name: '创建信号篮子', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const basket = (await response.json()).data
  assert.equal(basket.total_constituents, 1)
  assert.equal(basket.constituents[0].signals.length, 2)
  await basketPanel.getByLabel('观察截止日期').fill(future.at(-1).event_date)
  await basketPanel.getByLabel('冻结行情 · sh600000').selectOption(forward.id)
  responsePromise = page.waitForResponse(response => response.url().endsWith(`/baskets/${basket.id}/evaluations`) && response.request().method() === 'POST')
  await basketPanel.getByRole('button', { name: '保存 T+1 / T+2 测算' }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const report = (await response.json()).data
  assert.equal(report.summary.t1.completed_count, 1)
  assert.equal(report.summary.t2.completed_count, 1)
  await expect(basketPanel.getByRole('heading', { name: /^观察结果 ·/ })).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await basketPanel.getByRole('link', { name: '导出测算明细 Excel' }).click()
  const download = await downloadPromise
  assert(download.suggestedFilename().endsWith('.xlsx'))
  await page.setViewportSize({ width: 320, height: 780 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), false)
  await page.setViewportSize({ width: 1365, height: 900 })
  await page.reload()
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '事件模板', exact: true }).click()
  await expect(page.getByRole('region', { name: '事件判定模板', exact: true }).getByLabel('查看或编辑模板')).toHaveValue(profile.profile_id)
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  const backtest = page.getByRole('heading', { name: '创建单股回测' }).locator('..')
  await backtest.getByLabel('冻结行情样本').selectOption(forward.id)
  await backtest.getByLabel('回测策略').selectOption('wyckoff_trend_v2')
  await backtest.getByLabel('回测事件模板').selectOption(profile.profile_id)
  responsePromise = page.waitForResponse(response => response.url().endsWith('/api/v1/backtests') && response.request().method() === 'POST')
  await backtest.getByRole('button', { name: '提交后台回测' }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const backtestRun = (await response.json()).data
  await expect.poll(async () => (await (await page.request.get(`${base}/api/v1/backtests/${backtestRun.id}`)).json()).data.state, { timeout: 20000 }).toBe('succeeded')
  const completed = (await (await page.request.get(`${base}/api/v1/backtests/${backtestRun.id}`)).json()).data
  assert.equal(completed.attempt_number, 1)
  assert.match(completed.result_sha256, /^[a-f0-9]{64}$/)
  assert(completed.result.trades.length > 0)
  await expect(page.getByRole('link', { name: '导出本次回测 Excel' })).toBeVisible()
  assert.deepEqual(errors, [])
  console.log('browser smoke: profile copy/save/apply, Wyckoff scores/confirmation, two-strategy intersection/evidence, automatic basket T1/T2 report+Excel, isolated background backtest, persisted profile and responsive layout')
} finally {
  await browser.close()
}
