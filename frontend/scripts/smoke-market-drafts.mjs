import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'

const base = process.env.TRADE_SMOKE_BASE_URL || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = [], writes = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (request.url().includes('/market/annotations/') && request.method() !== 'GET') writes.push(request.method()) })
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': crypto.randomUUID() } })
    assert(response.ok(), await response.text()); return (await response.json()).data
  }
  const account = await post('/accounts', { name: '行情导航与草稿隔离验收' })
  const bars = ['2025-01-02', '2025-01-03', '2025-01-06'].map(event_date => ({ event_date, open: '10', high: '11', low: '9', close: '10.5', volume: 1234, available_at: event_date + 'T08:00:00+00:00' }))
  const first = await post('/market/datasets', { symbol: 'sh600519', bars })
  const second = await post('/market/datasets', { symbol: 'sz000002', bars })
  await page.goto(`${base}/rebuild.html?page=market&account=${account.id}&dataset=${first.id}`)
  await expect(page.getByLabel('人工备注')).toBeEnabled()
  await page.getByLabel('人工备注').fill('切证券后应恢复的本机草稿')
  await page.getByRole('row').filter({ hasText: second.id.slice(0, 12) }).getByRole('button', { name: '查看', exact: true }).click()
  await expect(page).toHaveURL(new RegExp('dataset=' + second.id))
  await expect(page.getByLabel('人工备注')).not.toHaveValue('切证券后应恢复的本机草稿')
  await page.goBack()
  await expect(page).toHaveURL(new RegExp('dataset=' + first.id))
  await expect(page.getByLabel('人工备注')).toHaveValue('切证券后应恢复的本机草稿')
  await page.reload()
  await expect(page.getByLabel('人工备注')).toHaveValue('切证券后应恢复的本机草稿')
  await page.goForward()
  await expect(page).toHaveURL(new RegExp('dataset=' + second.id))
  await expect(page.getByLabel('人工备注')).not.toHaveValue('切证券后应恢复的本机草稿')
  assert.deepEqual(writes, []); assert.deepEqual(errors, [])
  console.log('Market draft smoke passed: dataset URL/reload/back/forward, symbol-scoped local draft, no implicit annotation writes.')
} finally { await browser.close() }
