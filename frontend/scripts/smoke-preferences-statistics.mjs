import { spawn } from 'node:child_process'
import { mkdtemp, readFile } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-preferences-statistics-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const base = `http://127.0.0.1:${port}`
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: fileURLToPath(new URL('../../backend', import.meta.url)), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port) },
})
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', value => { logs = (logs + value.toString()).slice(-12000) })
try {
  for (let count = 0; count < 100; count++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded owned server startup */ }
    if (server.exitCode !== null || count === 99) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
  const page = await context.newPage()
  const errors = [], external = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (!request.url().startsWith(base) && !request.url().startsWith('blob:')) external.push(request.url()) })
  let csrf = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  page.on('response', async response => { if (response.url().endsWith('/api/v1/session') && response.ok()) csrf = (await response.json()).data.csrf_token })
  const api = async (url, method = 'GET', data) => {
    const response = await page.request.fetch(base + '/api/v1' + url, { method, data,
      headers: method === 'GET' ? {} : { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() } })
    if (!response.ok()) throw new Error(`${url} ${response.status()} ${await response.text()}`)
    return (await response.json()).data
  }
  const account = await api('/accounts', 'POST', { name: '偏好与统计验收' })
  const root = '/accounts/' + account.id
  await api(root + '/cash-flows', 'POST', { flow_date: '2025-01-01', kind: 'initial', amount: '10000' })
  for (const [day, total_assets] of [['2025-01-01', '10000'], ['2025-01-02', '11000']]) {
    await api(root + '/snapshots/' + day, 'PUT', { snap_date: day, total_assets, expected_revision: 0 })
  }
  await page.goto(`${base}/rebuild.html?page=settings&settings_group=display&account=${account.id}`)
  const display = page.getByRole('tabpanel', { name: '显示与密度' })
  await display.getByLabel('主题模式').selectOption('dark')
  await display.getByLabel('列表密度').selectOption('compact')
  await page.evaluate(() => {
    localStorage.setItem('trade-rebuild:sentiment-valuation-v1', JSON.stringify({ symbol: 'sh600000', name: '浦发银行', earningsYi: '10', growthRatePct: '20', basePe: '20', indexPoints: '3000', sentimentCoef: '1', actualCapYi: '' }))
    localStorage.setItem('trade-rebuild.ai-draft.unrelated', 'PRIVATE DRAFT')
  })
  let downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出本机偏好 JSON' }).click()
  const archive = await readFile(await (await downloadPromise).path(), 'utf8')
  expect(archive).not.toContain('PRIVATE DRAFT')
  expect(JSON.parse(archive).entries['trade-theme-mode']).toBe('dark')
  await display.getByLabel('主题模式').selectOption('light')
  await display.getByLabel('列表密度').selectOption('comfortable')
  const file = { name: 'preferences.json', mimeType: 'application/json', buffer: Buffer.from(archive) }
  await page.getByLabel('读取偏好文件并预览').setInputFiles(file)
  await expect(page.getByRole('heading', { name: '恢复差异 · 2 组' })).toBeVisible()
  await expect(display.getByLabel('主题模式')).toHaveValue('light')
  // Another tab changes a key between preview and explicit confirmation.
  const other = await page.context().newPage()
  await other.goto(base + '/health')
  await other.evaluate(() => localStorage.setItem('trade-theme-mode', 'system'))
  await page.getByRole('button', { name: '确认恢复这些偏好' }).click()
  await expect(page.getByRole('alert')).toContainText('其他页面修改')
  await other.close()
  await page.getByLabel('读取偏好文件并预览').setInputFiles(file)
  await page.getByRole('button', { name: '确认恢复这些偏好' }).click()
  await expect(display.getByLabel('主题模式')).toHaveValue('dark')
  await expect(display.getByLabel('列表密度')).toHaveValue('compact')
  expect(await page.evaluate(() => localStorage.getItem('trade-rebuild.ai-draft.unrelated'))).toBe('PRIVATE DRAFT')
  await page.reload()
  await expect(display.getByLabel('主题模式')).toHaveValue('dark')
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: path.join(dataDir, 'preferences-320.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('tab', { name: '打印与署名', exact: true }).click()
  const printing = page.getByRole('tabpanel', { name: '打印与署名' })
  await printing.getByLabel('导出署名').fill('中文打印验收 <记录>')
  await printing.getByLabel('本机导出目录（绝对路径）').fill(path.join(dataDir, 'explicit-exports'))
  await printing.getByRole('button', { name: '保存本组设置' }).click()
  await expect(printing.getByText('此设置组已保存。', { exact: true })).toBeVisible()
  const printSettings = await api('/settings/groups/print')
  expect(printSettings.value.author).toBe('中文打印验收 <记录>')
  await page.reload()
  await expect(printing.getByLabel('导出署名')).toHaveValue('中文打印验收 <记录>')
  await printing.getByRole('button', { name: '预览本组默认值' }).click()
  expect((await api('/settings/groups/print')).revision).toBe(printSettings.revision)
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: '统计分析', exact: true }).click()
  const summary = page.locator('section').filter({ has: page.getByRole('heading', { name: '收益与回合统计', exact: true }) })
  await expect(summary).toBeVisible()
  for (const name of ['导出统计 PDF', '导出统计 Excel', '导出汇总 CSV']) {
    downloadPromise = page.waitForEvent('download')
    await summary.getByRole('link', { name, exact: true }).click()
    const download = await downloadPromise
    const raw = await readFile(await download.path())
    expect(raw.length).toBeGreaterThan(200)
    if (name.endsWith('PDF')) expect(raw.subarray(0, 5).toString()).toBe('%PDF-')
    if (name.endsWith('CSV')) expect(raw.toString('utf8')).toContain('return_pct')
  }
  const sim = await api('/sim-accounts', 'POST', { name: '模拟统计下载', initial_capital: '100000', start_date: '2025-01-30' })
  const simPath = '/sim-accounts/' + sim.id
  async function fill(day, side, price) {
    const order = await api(simPath + '/orders', 'POST', { symbol: '600000', side, quantity: 100, limit_price: price, signal_date: day, submit_date: day })
    return api(simPath + '/orders/' + order.id + '/fill', 'POST', { expected_revision: 1, fill_date: day, fill_price: price })
  }
  await fill('2025-01-30', 'buy', '10')
  await api(simPath + '/settle', 'POST', { to_date: '2025-02-03' })
  await fill('2025-02-03', 'sell', '12')
  await page.goto(`${base}/rebuild.html?page=simulation&simulation-view=review&account=${sim.id}`)
  await page.getByLabel('月份归属日期').selectOption('buy')
  downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: '导出月份 CSV' }).click()
  const content = await readFile(await (await downloadPromise).path(), 'utf8')
  expect(content).toContain('2025-01'); expect(content).not.toContain('2025-02')
  await page.getByLabel('月份归属日期').selectOption('sell')
  downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: '导出月份 CSV' }).click()
  expect(await readFile(await (await downloadPromise).path(), 'utf8')).toContain('2025-02')
  await page.getByLabel('统计开始日期', { exact: true }).fill('2025-01-01')
  await page.getByLabel('统计结束日期', { exact: true }).fill('2025-01-31')
  await page.getByRole('button', { name: '应用统计区间', exact: true }).click()
  await expect(page.getByText('卖出成交 0', { exact: true })).toBeVisible()
  await page.getByLabel('月份归属日期').selectOption('buy')
  await expect(page.getByText('卖出成交 1', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '导出当前归属口径 PDF' })).toHaveAttribute('href', /date_from=2025-01-01&date_to=2025-01-31/)
  downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: '导出当前归属口径 PDF' }).click()
  expect((await readFile(await (await downloadPromise).path())).includes(Buffer.from('/FontFile2'))).toBe(true)
  const sample = await api('/market/datasets', 'POST', { symbol: '600000', bars: ['2025-01-30', '2025-01-31', '2025-02-03'].map((event_date, index) => ({ event_date, open: String(10 + index), close: String(10 + index), high: String(10 + index), low: String(10 + index), volume: 100000, available_at: event_date + 'T08:00:00Z' })) })
  const equityInput = { date_from: '2025-01-30', date_to: '2025-02-03', dataset_ids: [sample.id], strict: true }
  const preview = await api(simPath + '/equity-reports/preview', 'POST', equityInput)
  await api(simPath + '/equity-reports', 'POST', { ...equityInput, expected_input_sha256: preview.input_sha256 })
  await page.reload()
  const equity = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '模拟资产与回撤曲线', exact: true }) })
  await equity.getByRole('button', { name: '查看资产报告', exact: true }).click()
  downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: '导出权益曲线 PDF' }).click()
  expect((await readFile(await (await downloadPromise).path())).includes(Buffer.from('/FontFile2'))).toBe(true)
  expect(errors).toEqual([]); expect(external).toEqual([])
  console.log(JSON.stringify({ status: 'passed', dataDir, assertions: ['preference_download', 'preview_no_write', 'other_tab_conflict', 'confirmed_restore', 'reload', 'draft_exclusion', 'print_author_directory_reload_default_preview', 'real_statistics_pdf_excel_csv', 'sim_buy_sell_month_csv', 'sim_exact_date_range_export', 'frozen_equity_charts_pdf_download', '320px', 'no_external_network'] }))
} finally {
  await browser?.close()
  server.kill()
  await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); setTimeout(resolve, 5000).unref() })
}
