import { chromium, expect } from '@playwright/test'

const base = process.env.TRADE_SMOKE_BASE || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  let refreshes = 0
  let lastBody
  const fixture = (status, request = {}) => ({
    request: { query: 'A股 热点', provider: 'auto', age_hours: 72, as_of_at: '2026-09-26T08:00:00+00:00',
      date_from: null, date_to: null, window_start: '2026-09-23T08:00:00+00:00', window_end: '2026-09-26T08:00:00+00:00', ...request },
    snapshot_id: status === 'not_loaded' ? null : 'news-test-snapshot', fetched_at: status === 'not_loaded' ? null : '2026-09-26T08:00:00+00:00',
    actual_provider: status === 'not_loaded' ? null : 'eastmoney', source_url: null,
    items: [], count: 0, source_item_count: 0, excluded: { unknown_publication_time: 0, after_as_of: 0, outside_window: 0 },
    cache_hit: false, cache_age_seconds: 0, cache_stale: false, cache_ttl_seconds: 300,
    attempted_providers: [], fallback_used: false, degraded: false, errors: [], status,
    availability_quality: 'retrieved_snapshot_not_historical_availability', notes: ['此浏览器测试使用本地受控响应，不调用外部来源。'],
  })
  // Backend parser/cache/CSRF are covered by test_market_news.py. Here isolate UI behavior
  // and never make a real provider request during a repeatable browser test.
  await page.route('**/api/v1/market/news**', async route => {
    const req = route.request()
    let response = fixture('not_loaded')
    if (req.method() === 'POST') {
      refreshes += 1
      lastBody = req.postDataJSON()
      response = fixture('ready', lastBody)
      response.items = Array.from({ length: 22 }, (_, i) => ({ id: `item-${i}`, title: `受控资讯 ${i + 1}`, snippet: '<img src=x onerror=alert(1)> 原文显示',
        published_at: '2026-09-26T07:00:00+00:00', provider: 'eastmoney', source_name: '测试来源', url: null }))
      response.count = 22; response.source_item_count = 25
      response.excluded = { unknown_publication_time: 1, after_as_of: 1, outside_window: 1 }
      if (refreshes > 1) {
        response.cache_hit = true; response.cache_stale = true; response.cache_age_seconds = 7200
        response.degraded = true; response.fallback_used = true
        response.errors = [{ provider: 'eastmoney', code: 'NEWS_SOURCE_TIMEOUT' }, { provider: 'google_rss', code: 'NEWS_SOURCE_UNAVAILABLE' }]
      }
    } else if (refreshes > 0) response = { ...fixture('empty'), cache_hit: true, source_item_count: 25 }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: response }) })
  })
  await page.goto(base + '/rebuild.html?page=news')
  await expect(page.getByRole('heading', { name: '市场资讯', exact: true, level: 2 })).toBeVisible()
  await expect(page.getByText('当前查询还没有本地资讯。点击“联网刷新”获取。')).toBeVisible()
  expect(refreshes).toBe(0)
  await page.getByLabel('时效窗口').selectOption('24')
  await page.getByLabel('截至时间（设备时区）').fill('2026-09-26T16:00')
  await page.getByLabel('复盘开始日期（可选，上海时区）').fill('2026-09-25')
  await page.getByLabel('复盘结束日期（可选，上海时区）').fill('2026-09-26')
  await page.getByRole('button', { name: '联网刷新', exact: true }).click()
  await expect(page.getByText('受控资讯 1', { exact: true })).toBeVisible()
  expect(refreshes).toBe(1)
  expect(lastBody.age_hours).toBe(24)
  expect(lastBody.date_from).toBe('2026-09-25')
  expect(lastBody.as_of_at).toMatch(/Z$/)
  await expect(page.locator('img[src=x]')).toHaveCount(0)
  await expect(page.getByText('已排除：发布时间未知 1 条、晚于截至时间 1 条、窗口外 1 条。')).toBeVisible()
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.getByText('受控资讯 21', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '联网刷新', exact: true }).click()
  await expect(page.getByText('缓存已超过 5 分钟，页面保留原抓取时间。需要最新资讯时请联网刷新。')).toBeVisible()
  await expect(page.getByText(/东方财富快讯：来源请求超时/)).toBeVisible()
  await page.getByRole('button', { name: '读取缓存', exact: true }).click()
  await expect(page.getByText(/已获取来源样本，但所选时间/)).toBeVisible()
  expect(refreshes).toBe(2)
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
  console.log(JSON.stringify({ ok: true, explicit_refresh_count: refreshes, initial_network_calls: 0, time_window: 24,
    date_filter: true, cached_failure: true, empty_distinct: true, plain_text: true, mobile_320: true }))
} finally {
  await browser.close()
}
