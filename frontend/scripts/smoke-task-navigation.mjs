import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'

const base = 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const session = (await (await page.request.get(base + '/api/v1/session')).json()).data
  const headers = { 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': crypto.randomUUID() }
  const accountResponse = await page.request.post(base + '/api/v1/accounts', { data: { name: '导航任务验收 ' + Date.now() }, headers })
  assert(accountResponse.ok(), await accountResponse.text())
  const account = (await accountResponse.json()).data
  const queuedResponse = await page.request.post(base + '/api/v1/ai/test-connection?account_id=' + account.id,
    { data: { config_revision: (await (await page.request.get(base + '/api/v1/ai/config')).json()).data.revision },
      headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() } })
  assert(queuedResponse.ok(), await queuedResponse.text())
  const queued = (await queuedResponse.json()).data
  await page.goto(base + '/rebuild.html?page=tasks&account=' + account.id)
  await expect(page.getByRole('heading', { name: '任务中心', exact: true, level: 2 })).toBeVisible()
  const row = page.getByRole('row').filter({ hasText: queued.id.slice(0, 12) })
  await expect(row).toContainText('等待执行')
  await row.getByRole('button', { name: '取消', exact: true }).click()
  await expect(row).toContainText('已取消')
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  await expect(page).toHaveURL(/page=research/)
  await page.reload()
  await expect(page.getByRole('heading', { name: '固定样本信号', exact: true })).toBeVisible()
  assert.equal(new URL(page.url()).searchParams.get('account'), account.id)
  await page.getByRole('button', { name: '任务中心', exact: true }).click()
  await expect(page).toHaveURL(/page=tasks/)
  await page.goBack()
  await expect(page.getByRole('heading', { name: '固定样本信号', exact: true })).toBeVisible()
  await page.goForward()
  await expect(page.getByRole('heading', { name: '任务中心', exact: true, level: 2 })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 900 })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
  assert.deepEqual(errors, [])
  console.log('Task center and page/account navigation smoke passed')
} finally { await browser.close() }
