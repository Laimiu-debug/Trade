import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import net from 'node:net'
import { chromium, expect } from '@playwright/test'

const evidence = await mkdtemp(join(tmpdir(), 'trade-layout-redesign-'))
const socket = net.createServer()
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve))
const port = socket.address().port
await new Promise(resolve => socket.close(resolve))
const base = `http://127.0.0.1:${port}`
const server = spawn('python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
  cwd: fileURLToPath(new URL('../../backend', import.meta.url)), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: evidence, TRADE_REBUILD_PORT: String(port) },
})
let browser, page, logs = ''
server.stdout.on('data', value => { logs += value }); server.stderr.on('data', value => { logs += value })
const checks = []
try {
  await expect.poll(async () => { try { return (await fetch(base + '/health')).ok } catch { return false } }, { timeout: 30000 }).toBe(true)
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(() => localStorage.setItem('trade-theme-mode', 'dark'))
  page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base + '/rebuild.html')
  await page.getByLabel('账户名称', { exact: true }).fill('布局验收账户')
  for (const width of [1440, 768, 375]) {
    await page.setViewportSize({ width, height: 1000 })
    const field = await page.getByLabel('账户名称', { exact: true }).boundingBox()
    const primary = await page.getByRole('button', { name: '创建实盘账户', exact: true }).boundingBox()
    const secondary = await page.getByRole('button', { name: '创建模拟账户', exact: true }).boundingBox()
    assert(primary.y >= field.y + field.height, 'Account actions must be below the input')
    assert(secondary.y >= primary.y + primary.height || secondary.x >= primary.x + primary.width, 'Account actions must not overlap')
    await page.locator('.onboarding').screenshot({ path: join(evidence, `onboarding-dark-${width}.png`) })
    checks.push(`onboarding spacing ${width}`)
  }
  await page.getByRole('button', { name: '创建实盘账户', exact: true }).click()
  await expect(page.locator('.onboarding')).toHaveCount(0)
  const account = new URL(page.url()).searchParams.get('account')
  assert(account)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${base}/rebuild.html?page=research&research=portfolio&account=${account}`)
  await expect(page.getByRole('heading', { name: '固定样本组合回测', exact: true })).toBeVisible()
  await expect(page.locator('.research-page:visible')).toHaveCount(1)
  const routeBefore = page.url()
  await page.getByLabel('组合实验名称', { exact: true }).fill('保留中的组合配置')
  const research = page.getByRole('navigation', { name: '研究子页面' })
  await research.getByRole('link', { name: '策略目录', exact: false }).click()
  await expect(page.getByLabel('组合实验名称', { exact: true })).not.toBeVisible()
  await page.goBack()
  await expect(page).toHaveURL(routeBefore)
  await expect(page.getByLabel('组合实验名称', { exact: true })).toHaveValue('保留中的组合配置')
  checks.push('research deep link / one visible workspace / back navigation retains form')
  for (const width of [1440, 768, 375]) {
    await page.setViewportSize({ width, height: 1000 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    const bad = await page.locator('.research-page:visible input:not([type="checkbox"]):visible, .research-page:visible select:visible').evaluateAll(nodes => nodes.filter(node => getComputedStyle(node).backgroundColor === 'rgb(255, 255, 255)').map(node => node.outerHTML))
    assert.deepEqual(bad, [])
    await page.screenshot({ path: join(evidence, `portfolio-dark-${width}.png`), fullPage: true })
    checks.push(`portfolio themed controls ${width}`)
  }
  for (const [step, title] of [[2, '资金与执行'], [3, '退出与费用']]) {
    await page.locator('.research-steps button').nth(step).click()
    const controls = page.locator('.research-step-panel:visible input:not([type="checkbox"]):visible, .research-step-panel:visible select:visible')
    assert(await controls.count() > 0)
    assert.deepEqual(await controls.evaluateAll(nodes => nodes.filter(node => getComputedStyle(node).backgroundColor === 'rgb(255, 255, 255)').map(node => node.outerHTML)), [])
    assert.deepEqual(await page.locator('.research-step-panel:visible input[type="checkbox"]:visible').evaluateAll(nodes => nodes.filter(node => node.getBoundingClientRect().width > 24).map(node => node.outerHTML)), [])
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: join(evidence, `portfolio-step-${step}-375.png`), fullPage: true })
    checks.push(`portfolio ${title} / themed fields and compact checkboxes`)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await research.getByRole('link', { name: '策略目录', exact: false }).click()
  await page.screenshot({ path: join(evidence, 'strategy-directory-dark.png'), fullPage: true })
  // Controlled news fixture isolates typography and duplicate headline handling;
  // it does not claim validation of an external news provider.
  await page.route('**/api/v1/market/news', route => route.fulfill({ json: { data: {
    request: { age_hours: 72, as_of_at: '2026-09-26T08:00:00Z', window_start: '2026-09-23T08:00:00Z', window_end: '2026-09-26T08:00:00Z', query: '布局样本' },
    items: [{ id: 'layout-fixture', title: '市场观察：板块变化与成交量 - 研究样本', snippet: '市场观察：板块变化与成交量 研究样本', url: 'https://example.com/fixture', published_at: '2026-09-26T07:00:00Z', source_name: '布局测试来源', provider: 'eastmoney' }],
    count: 1, source_item_count: 1, snapshot_id: 'layout-fixture', actual_provider: 'eastmoney', fetched_at: '2026-09-26T08:00:00Z', cache_hit: true, cache_age_seconds: 0, cache_stale: false, cache_ttl_seconds: 300,
    errors: [], notes: [], attempted_providers: ['eastmoney'], excluded: { unknown_publication_time: 0, after_as_of: 0, outside_window: 0 }, status: 'ready',
  } } }))
  await page.goto(`${base}/rebuild.html?page=news&account=${account}`)
  await expect(page.locator('.news-article')).toHaveCount(1)
  await expect(page.locator('.news-article p')).toHaveCount(0)
  assert.notEqual(await page.locator('.news-article a').evaluate(node => getComputedStyle(node).color), 'rgb(0, 0, 238)')
  await page.screenshot({ path: join(evidence, 'news-dark.png'), fullPage: true })
  checks.push('readable news links / deduplicated headline')
  await page.goto(`${base}/rebuild.html?page=market&market-view=import&account=${account}`)
  await expect(page.getByRole('heading', { name: '导入固定样本' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '在线日线同步' })).not.toBeVisible()
  await page.getByLabel('CSV 内容', { exact: true }).fill('未提交的本地草稿')
  await page.getByRole('navigation', { name: '行情页面' }).getByRole('button', { name: '在线同步', exact: true }).click()
  await expect(page).toHaveURL(/market-view=sync/)
  await expect(page.getByLabel('CSV 内容', { exact: true })).not.toBeVisible()
  await page.goBack()
  await expect(page.locator('textarea').filter({ visible: true }).first()).toHaveValue('未提交的本地草稿')
  checks.push('market separate pages / URL / retained form')
  const csrf = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  async function write(endpoint, body, method = 'POST') {
    const response = await page.request.fetch(base + '/api/v1' + endpoint, { method, data: body, headers: { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() } })
    assert(response.ok(), await response.text())
    return (await response.json()).data
  }
  const other = await write('/accounts', { name: '账户隔离验收 B' })
  for (const [id, total] of [[account, '5000'], [other.id, '8000']]) {
    await write(`/accounts/${id}/cash-flows`, { flow_date: '2025-01-01', kind: 'initial', amount: total })
    await write(`/accounts/${id}/snapshots/2025-01-02`, { expected_revision: 0, snap_date: '2025-01-02', total_assets: total }, 'PUT')
    await expect.poll(async () => (await (await page.request.get(base + `/api/v1/accounts/${id}/analytics`)).json()).data.status, { timeout: 30000 }).toBe('fresh')
  }
  await page.goto(`${base}/rebuild.html?page=snapshots&account=${account}`)
  await page.getByLabel('总资产', { exact: true }).fill('123456')
  await page.locator('.account-select select').selectOption(other.id)
  await expect(page.getByLabel('总资产', { exact: true })).toHaveValue('')
  await expect(page.getByRole('status').filter({ hasText: '上一账户未提交' })).toBeVisible()
  checks.push('account switch clears owner-bound unsaved snapshot')
  let releaseLate, intercepted = false
  const late = new Promise(resolve => { releaseLate = resolve })
  await page.route(`**/api/v1/accounts/${account}/analytics`, async route => {
    const response = await route.fetch()
    intercepted = true
    await late
    await route.fulfill({ response })
  })
  await page.goto(`${base}/rebuild.html?page=overview&account=${account}`)
  await expect.poll(() => intercepted).toBe(true)
  await page.locator('.account-select select').selectOption(other.id)
  await expect(page.locator('.metric').filter({ hasText: '最新资产' }).locator('strong')).toHaveText('¥ 8000.00')
  releaseLate()
  await page.waitForLoadState('networkidle')
  await expect(page.locator('.metric').filter({ hasText: '最新资产' }).locator('strong')).toHaveText('¥ 8000.00')
  await expect(page.locator('.account-select select')).toHaveValue(other.id)
  checks.push('late previous-account analytics cannot replace current-account facts')
  await page.unroute(`**/api/v1/accounts/${account}/analytics`)
  let releaseTrade, tradeIntercepted = false
  const heldTrade = new Promise(resolve => { releaseTrade = resolve })
  await page.route(`**/api/v1/accounts/${account}/trades`, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    const response = await route.fetch()
    assert(response.ok(), await response.text())
    tradeIntercepted = true
    await heldTrade
    await route.fulfill({ response })
  })
  await page.goto(`${base}/rebuild.html?page=trades&account=${account}`)
  const tradeForm = page.locator('form').filter({ has: page.getByLabel('交易日期', { exact: true }) })
  await tradeForm.getByLabel('证券代码', { exact: true }).fill('600000')
  await tradeForm.getByLabel('价格', { exact: true }).fill('10')
  await tradeForm.getByRole('button', { name: '新增交易', exact: true }).click()
  await expect.poll(() => tradeIntercepted).toBe(true)
  await page.locator('.account-select select').selectOption(other.id)
  await tradeForm.getByLabel('证券代码', { exact: true }).fill('600001')
  await tradeForm.getByLabel('价格', { exact: true }).fill('12')
  releaseTrade()
  await page.waitForLoadState('networkidle')
  await expect(tradeForm.getByLabel('证券代码', { exact: true })).toHaveValue('600001')
  await expect(tradeForm.getByLabel('价格', { exact: true })).toHaveValue('12')
  await expect(page.getByRole('status').filter({ hasText: '交易已保存' })).toHaveCount(0)
  const savedA = (await (await page.request.get(base + `/api/v1/accounts/${account}/trades`)).json()).data
  const savedB = (await (await page.request.get(base + `/api/v1/accounts/${other.id}/trades`)).json()).data
  assert.equal(savedA.length, 1); assert.equal(savedA[0].symbol, '600000'); assert.equal(savedB.length, 0)
  checks.push('late trade completion preserves new account draft and writes only the original account')
  assert.deepEqual(errors, [])
  await writeFile(join(evidence, 'checks.json'), JSON.stringify(checks, null, 2))
  console.log(JSON.stringify({ status: 'passed', evidence, checks }))
} catch (error) { console.error('Evidence:', evidence, 'URL:', page?.url(), logs); if (page) console.error((await page.locator('.content').innerText()).slice(0, 1600)); throw error }
finally {
  await browser?.close()
  server.kill()
  await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); setTimeout(resolve, 5000).unref() })
}
