import { spawn } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-all-pages-ui-'))
const socket = net.createServer()
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve))
const port = socket.address().port
await new Promise(resolve => socket.close(resolve))
const base = `http://127.0.0.1:${port}`
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
  cwd: fileURLToPath(new URL('../../backend', import.meta.url)), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port) },
})
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', value => { logs = (logs + value.toString()).slice(-10000) })
const checks = []
try {
  await expect.poll(async () => { try { return (await fetch(base + '/health')).ok } catch { return false } }, { timeout: 30000 }).toBe(true)
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const context = await browser.newContext({ viewport: { width: 320, height: 900 } })
  const page = await context.newPage()
  const errors = [], external = [], mutations = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (!request.url().startsWith(base) && !request.url().startsWith('blob:') && !request.url().startsWith('data:')) external.push(request.url())
    if (request.url().startsWith(base + '/api/v1') && request.method() !== 'GET') mutations.push(request.method() + ' ' + request.url())
  })
  const csrf = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const create = async (endpoint, data) => {
    const response = await page.request.post(base + '/api/v1/' + endpoint, { data, headers: { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() } })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).data
  }
  const real = await create('accounts', { name: '所有页面空态验收' })
  const sim = await create('sim-accounts', { name: '所有页面模拟验收', initial_capital: '100000', start_date: '2025-01-01' })
  async function layout(label) {
    await expect(page.locator('.content').getByText(/^(?:正在)?(?:加载|打开|读取).*…$/).filter({ visible: true })).toHaveCount(0)
    await page.waitForLoadState('networkidle')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    expect(await page.locator('.content').innerText()).not.toBe('')
    const unstyled = await page.locator('.content input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="hidden"]):visible, .content select:visible, .content textarea:visible').evaluateAll(nodes => nodes.filter(node => {
      const css = getComputedStyle(node)
      return parseFloat(css.borderRadius) < 4 || (document.documentElement.dataset.theme === 'dark' && css.backgroundColor === 'rgb(255, 255, 255)')
    }).map(node => node.outerHTML.slice(0, 180)))
    expect(unstyled, `${label}: every visible form control should use themed styling`).toEqual([])
    checks.push(label)
  }
  for (const [account, theme] of [[real, 'light'], [sim, 'dark']]) {
    await page.goto(`${base}/rebuild.html?page=settings&account=${account.id}`)
    await page.evaluate(value => localStorage.setItem('trade-theme-mode', value), theme)
    await page.reload()
    const nav = page.getByRole('navigation', { name: '主导航' })
    await expect(nav).toBeVisible()
    const names = await nav.getByRole('button').allTextContents()
    for (const name of names) {
      const button = nav.getByRole('button', { name, exact: true })
      await button.focus(); await page.keyboard.press('Enter')
      await expect(button).toHaveAttribute('aria-current', 'page')
      // Wait for lazy modules and initial cache reads before measuring.
      await page.waitForLoadState('networkidle')
      await layout(`${account.kind}/${theme}/${name}`)
      const secondary = page.locator('.research-sidebar nav a:visible, .workspace-navigation button:visible')
      const destinations = await secondary.allTextContents()
      for (const destination of destinations) {
        const link = secondary.filter({ hasText: destination }).first()
        await link.click(); await page.waitForLoadState('networkidle')
        await layout(`${account.kind}/${name}/${destination}`)
        const modes = page.locator('.research-mode-nav a')
        for (const mode of await modes.allTextContents()) {
          await modes.filter({ hasText: mode }).click(); await page.waitForLoadState('networkidle')
          await layout(`${account.kind}/${name}/${destination}/${mode}`)
        }
      }
      const tabs = page.locator('[role="tabpanel"]:not([hidden]) [role="tab"], .content > [role="tablist"] [role="tab"], .research-tabs button, .settings-workspace [role="tablist"] [role="tab"]')
      const tabNames = await tabs.allTextContents()
      for (const title of tabNames) {
        const tab = tabs.filter({ hasText: title }).first()
        if (await tab.isVisible() && await tab.isEnabled()) {
          await tab.click(); await page.waitForLoadState('networkidle')
          await layout(`${account.kind}/${name}/${title}`)
        }
      }
      // Expand read-only details, including configuration forms and empty histories.
      for (const summary of await page.locator('.content details:not([open]) > summary').all()) {
        if (await summary.isVisible()) await summary.click()
      }
      await layout(`${account.kind}/${name}/expanded`)
    }
    await page.screenshot({ path: path.join(dataDir, `${account.kind}-${theme}-320.png`), fullPage: true })
  }
  expect(errors).toEqual([]); expect(external).toEqual([])
  // The documented daily old-card selection records today's stable choice on overview.
  expect(mutations.filter(value => value !== 'POST ' + base + '/api/v1/insights/daily')).toEqual([])
  await writeFile(path.join(dataDir, 'checks.json'), JSON.stringify(checks, null, 2))
  console.log(JSON.stringify({ status: 'passed', dataDir, checks: checks.length, assertions: ['all_real_sim_navigation', '320px', 'keyboard_navigation', 'empty_states', 'expanded_details', 'light_dark', 'only_documented_daily_card_selection', 'no_external_requests', 'no_page_errors'] }))
} catch (error) {
  console.error('Evidence:', dataDir, 'Completed:', checks, logs)
  for (const context of browser?.contexts() || []) for (const page of context.pages()) console.error((await page.locator('body').innerText()).slice(-10000))
  throw error
} finally {
  await browser?.close()
  server.kill()
  await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); setTimeout(resolve, 5000).unref() })
}
