import { chromium, expect } from '@playwright/test'

const base = process.env.TRADE_SMOKE_BASE || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, timezoneId: 'Asia/Shanghai' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const session = (await (await page.request.get(base + '/api/v1/session')).json()).data
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body,
      headers: { 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': crypto.randomUUID() } })
    if (!response.ok()) throw new Error(`${path}: ${response.status()} ${await response.text()}`)
    return (await response.json()).data
  }
  const closes = [...Array.from({ length: 60 }, (_, i) => 12 - i * 2 / 59),
    9.3, 9.5, 9.7, 9.9, 10.1, 10.3, 10.5, 10.7, 10.5, 10.3, 10.1, 9.9, 9.7, 9.5, 9.5,
    9.6, 9.6, 9.6, 9.6, 9.6, 9.2, 9.5, 10.2, 10.8, 11.2, 11.1, 11.2, 11.3, 11.4, 11.5]
  const volumes = { 58: 1800, 60: 10000, 74: 800, 80: 2000, 82: 1600, 83: 1800, 84: 2200, 85: 600 }
  const day = new Date('2025-01-01T00:00:00Z')
  const bars = closes.map((close, i) => {
    while ([0, 6].includes(day.getUTCDay())) day.setUTCDate(day.getUTCDate() + 1)
    const date = day.toISOString().slice(0, 10); day.setUTCDate(day.getUTCDate() + 1)
    const open = closes[i - 1] || close
    return { event_date: date, open: open.toFixed(4), close: close.toFixed(4),
      high: (i === 60 ? 10.2 : Math.max(open, close) + 0.15).toFixed(4),
      low: ([60, 74].includes(i) ? 9 : i === 80 ? 8.8 : Math.min(open, close) - 0.15).toFixed(4),
      volume: volumes[i] || 1000, available_at: `${date}T08:00:00+00:00` }
  })
  const dataset = await post('/market/datasets', { symbol: 'sh600000', bars })
  const run = await post('/research/runs', { dataset_id: dataset.id, strategy_id: 'wyckoff_trend_v2', params: {},
    decision_at: `${bars.at(-1).event_date}T09:00:00+00:00`, strict: true,
    event_profile_id: 'system_legacy_formula_v1', event_profile_revision: 0 })
  expect(run.result.indicator.event_chain.length).toBeGreaterThan(0)
  let newResearchCalls = 0
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/research/runs')) newResearchCalls += 1 })
  await page.goto(`${base}/rebuild.html?page=market&dataset=${dataset.id}`)
  const chart = page.locator('.market-chart')
  await expect(chart.getByRole('img', { name: '主力与散户量能独立坐标' })).toBeVisible()
  await chart.getByText('已保存研究的事件与策略图层', { exact: true }).click()
  await chart.getByRole('button', { name: '读取可叠加研究' }).click()
  await chart.getByLabel(`叠加研究 ${run.id}`).check()
  const events = chart.getByRole('img', { name: /^事件 / })
  await expect.poll(() => events.count()).toBeGreaterThan(0)
  const count = await events.count()
  await chart.getByLabel('吸筹事件', { exact: true }).uncheck()
  await expect.poll(() => events.count()).toBeLessThan(count)
  await chart.getByLabel('派发 / 风险事件', { exact: true }).uncheck()
  await chart.getByLabel('其他事件', { exact: true }).uncheck()
  await expect(events).toHaveCount(0)
  await chart.getByLabel('吸筹事件', { exact: true }).check()
  await chart.getByLabel('派发 / 风险事件', { exact: true }).check()
  await chart.getByLabel('其他事件', { exact: true }).check()
  await expect(events).toHaveCount(count)
  await chart.getByLabel('主力 / 散户量能', { exact: true }).uncheck()
  await expect(chart.getByRole('img', { name: '主力与散户量能独立坐标' })).toHaveCount(0)
  await chart.getByLabel('主力 / 散户量能', { exact: true }).check()
  await page.getByRole('button', { name: '深色', exact: true }).click()
  await expect(events).toHaveCount(count)
  await chart.getByLabel('K 线结束位置').focus()
  await page.keyboard.press('ArrowLeft')
  await expect(events).toHaveCount(0)
  await expect(chart.getByText(/个所选研究暂未绘制/)).toBeVisible()
  await page.keyboard.press('End')
  await expect(events).toHaveCount(count)
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(newResearchCalls).toBe(0)
  expect(errors).toEqual([])
  console.log(JSON.stringify({ ok: true, events: count, dataset: dataset.id, run: run.id, known_as_of: run.decision_at,
    independent_force_axis: true, toggles: true, past_view_hides_snapshot: true, theme_preserved: true, mobile320: true, auto_research_calls: 0 }))
} finally { await browser.close() }
