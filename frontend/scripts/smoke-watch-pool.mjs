import { chromium, expect } from '@playwright/test'

const base = process.env.TRADE_SMOKE_BASE || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
let original
let api
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const session = (await (await page.request.get(base + '/api/v1/session')).json()).data
  let csrf = session.csrf_token
  page.on('response', async response => {
    if (response.url().endsWith('/api/v1/session') && response.ok()) csrf = (await response.json()).data.csrf_token
  })
  api = async (path, method = 'GET', body) => {
    const response = await page.request.fetch(base + '/api/v1' + path, { method, data: body,
      headers: method === 'GET' ? {} : { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() } })
    if (!response.ok()) throw new Error(`${path} ${response.status()} ${await response.text()}`)
    return (await response.json()).data
  }
  original = await api('/research/watch-pool')
  const start = new Date('2025-01-01T00:00:00Z')
  const bars = Array.from({ length: 260 }, (_, i) => {
    const day = new Date(start.getTime() + i * 86400000).toISOString().slice(0, 10)
    const close = 10 + i / 100
    return { event_date: day, open: close.toFixed(4), close: close.toFixed(4), high: (close + .2).toFixed(4),
      low: (close - .2).toFixed(4), volume: 100000, amount: '2000000', available_at: day + 'T08:00:00+00:00' }
  })
  const datasets = []
  for (const symbol of ['sh000001', '000001']) datasets.push(await api('/market/datasets', 'POST', { symbol, bars }))
  const run = await api('/research/screener-runs', 'POST', { datasets: datasets.map(row => ({ dataset_id: row.id, float_shares: 20000000, float_shares_as_of_date: '2025-01-01' })),
    as_of_date: bars.at(-1).event_date, config: {} })
  const index = run.result.pools.input.find(row => row.symbol.toLowerCase() === 'sh000001')
  const legacy = JSON.stringify({ config: { ...original.state.config, enabled: false }, manual: [index], order: ['000001.SH'] })
  await page.addInitScript(({ legacy, runId }) => {
    localStorage.setItem('trade-rebuild.watch-pool.v1', legacy)
    localStorage.setItem('trade-rebuild.screener-last-run.v1', JSON.stringify(runId))
  }, { legacy, runId: run.id })
  await page.goto(base + '/rebuild.html?page=research&research=screener')
  const pool = page.locator('.watch-pool')
  await expect(pool.getByText(new RegExp(`修订 ${original.revision} ·`))).toBeVisible()
  expect((await api('/research/watch-pool')).revision).toBe(original.revision)
  await pool.getByText('迁入旧本机观察池（原记录保留）', { exact: true }).click()
  await pool.getByRole('button', { name: '预览旧本机记录' }).click()
  await expect(pool.getByText(/已验证 1 个成员/)).toBeVisible()
  expect((await api('/research/watch-pool')).revision).toBe(original.revision)
  await pool.getByRole('button', { name: '确认迁入并替换服务端观察池' }).click()
  await expect(pool.getByText('旧本机记录已迁入；本机原记录仍保留。')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('trade-rebuild.watch-pool.v1'))).toBe(legacy)
  await page.getByRole('button', { name: '输入池 2', exact: true }).click()
  await pool.getByLabel('从当前阶段人工加入').selectOption('000001')
  await pool.getByRole('button', { name: '加入观察池' }).click()
  await expect(pool.locator('fieldset').first().getByRole('row')).toHaveCount(3)
  const beforeSave = await api('/research/watch-pool')
  expect(beforeSave.state.manual).toHaveLength(1)
  // A different window writes first; UI save must preserve its local two-member draft.
  await api('/research/watch-pool', 'PUT', { expected_revision: beforeSave.revision, state: beforeSave.state })
  await pool.getByRole('button', { name: '保存观察池', exact: true }).click()
  await expect(pool.getByText(/比较服务端修订/)).toBeVisible()
  await expect(pool.getByRole('button', { name: '保存观察池', exact: true })).toBeDisabled()
  await pool.getByRole('button', { name: '保留草稿，以最新修订继续编辑' }).click()
  await pool.getByRole('button', { name: '保存观察池', exact: true }).click()
  await expect(pool.getByText('观察池已保存到服务端。')).toBeVisible()
  const saved = await api('/research/watch-pool')
  expect(new Set(saved.state.manual.map(row => row.symbol))).toEqual(new Set(['sh000001', 'sz000001']))
  await page.reload()
  await expect(pool.getByText(new RegExp(`修订 ${saved.revision} ·`))).toBeVisible()
  await expect(pool.locator('fieldset').first().getByRole('row')).toHaveCount(3)
  await pool.getByRole('button', { name: '读取保存历史' }).click()
  await expect(pool.getByText('最近 100 次保存历史', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
  console.log(JSON.stringify({ ok: true, canonical_distinct_index_and_stock: true, explicit_legacy_preview_import: true,
    legacy_preserved: true, reload_persistence: true, stale_revision_draft_retained: true, history: true, mobile320: true }))
} finally {
  if (original && api) {
    const latest = await api('/research/watch-pool')
    if (JSON.stringify(latest.state) !== JSON.stringify(original.state)) {
      await api('/research/watch-pool', 'PUT', { expected_revision: latest.revision, state: original.state })
    }
  }
  await browser.close()
}
