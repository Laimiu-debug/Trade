import { chromium, expect } from '@playwright/test'
import { readFile, writeFile, mkdtemp, unlink, rmdir } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const base = 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
let offlineDirectory
let offlinePath
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const session = (await (await page.request.get(base + '/api/v1/session')).json()).data
  let serial = 0
  const prefix = `report-smoke-${Date.now()}`
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body,
      headers: { 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': `${prefix}-${++serial}` } })
    if (!response.ok()) throw new Error(await response.text())
    return (await response.json()).data
  }
  const offset = Date.now() % 10000
  const bars = Array.from({ length: 65 }, (_, index) => {
    const day = new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10)
    const close = 10 + index * 0.15
    return { event_date: day, open: (close - 0.03).toFixed(4), high: (close + 0.2).toFixed(4),
      low: (close - 0.2).toFixed(4), close: close.toFixed(4), volume: 1000000 + offset + index * 1000,
      available_at: `${day}T07:00:00+00:00` }
  })
  const ds = await post('/market/datasets', { symbol: '600000', bars })
  const run = await post('/backtests', { dataset_id: ds.id, strategy_id: 'relative_strength_breakout_v1',
    params: { min_ret40: '0.01' }, holding_bars: 3, initial_capital: '10000', strict: true, advanced_analysis: true, analysis_seed: 34, analysis_iterations: 100 })
  await expect.poll(async () => {
    const response = (await (await page.request.get(`${base}/api/v1/backtests/${run.id}`)).json()).data
    if (response.state === 'failed') throw new Error(response.error)
    return response.state
  }, { timeout: 30000, intervals: [300, 500] }).toBe('succeeded')
  await page.goto(base + '/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('报告库验收')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  const history = page.getByRole('heading', { name: '历史回测任务', exact: true }).locator('..')
  await history.locator('tbody tr').filter({ hasText: ds.id.slice(0, 12) }).first().getByRole('button', { name: '查看', exact: true }).click()
  await expect(page.getByRole('heading', { name: '风险与稳定性分析', exact: true })).toBeVisible()
  await page.getByText('蒙特卡洛情景分析', { exact: true }).click()
  await expect(page.getByText(/至少需要10轮完整交易/)).toBeVisible()
  await page.getByText('按日期查看资产、持仓和成交', { exact: true }).click()
  await page.getByLabel('回测观察日', { exact: true }).selectOption(bars[2].event_date)
  await expect(page.getByText('当日无成交', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '保存到单股报告库', exact: true }).click()
  const library = page.getByRole('region', { name: '研究报告库', exact: true })
  const title = `报告验收 ${Date.now()}`
  await library.getByLabel('冻结报告标题', { exact: true }).fill(title)
  const saveResponse = page.waitForResponse(response => response.url() === base + '/api/v1/research/reports' && response.request().method() === 'POST')
  await library.getByRole('button', { name: '将当前回测保存为报告', exact: true }).click()
  const savedResponse = await saveResponse
  if (!savedResponse.ok()) throw new Error(await savedResponse.text())
  const saved = (await savedResponse.json()).data
  const detail = library.getByRole('region', { name: '冻结报告详情', exact: true })
  await expect(detail.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await expect(detail.getByRole('img', { name: '报告冻结资产曲线', exact: true })).toBeVisible()
  const zipEvent = page.waitForEvent('download')
  await detail.getByRole('link', { name: '下载完整 ZIP 报告包', exact: true }).click()
  const downloaded = await zipEvent
  const bytes = await readFile(await downloaded.path())
  if (bytes.readUInt32LE(0) !== 0x04034b50) throw new Error('Expected ZIP report package')
  const htmlEvent = page.waitForEvent('download')
  await detail.getByRole('link', { name: '下载离线 HTML 报告', exact: true }).click()
  const htmlDownload = await htmlEvent
  offlineDirectory = await mkdtemp(join(tmpdir(), 'trade-report-smoke-'))
  offlinePath = join(offlineDirectory, 'report.html')
  await writeFile(offlinePath, await readFile(await htmlDownload.path()))
  const offline = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  await offline.goto(pathToFileURL(offlinePath).href)
  await expect(offline.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await expect(offline.getByRole('img', { name: '资产曲线', exact: true })).toBeVisible()
  if (await offline.locator('script').count()) throw new Error('Offline report should not require scripts')
  await offline.close()
  const excel = await page.request.get(`${base}/api/v1/research/reports/${saved.id}/export.xlsx`)
  if (!excel.ok() || (await excel.body()).readUInt32LE(0) !== 0x04034b50) throw new Error('Expected regenerated Excel workbook')

  const savedRow = library.locator('tbody tr').filter({ hasText: title }).first()
  await savedRow.getByRole('button', { name: '删除报告', exact: true }).click()
  await library.getByRole('button', { name: '确认删除报告', exact: true }).click()
  await expect(library.getByRole('status')).toContainText('报告已从列表删除')
  await library.locator('summary').filter({ hasText: '导入完整报告包' }).click()
  await library.getByLabel('研究报告 ZIP 文件', { exact: true }).setInputFiles({ name: 'report.zip', mimeType: 'application/zip', buffer: bytes })
  const importResponse = page.waitForResponse(response => response.url() === base + '/api/v1/research/reports/import' && response.request().method() === 'POST')
  await library.getByRole('button', { name: '校验并导入报告包', exact: true }).click()
  const importedResponse = await importResponse
  if (!importedResponse.ok()) throw new Error(await importedResponse.text())
  const imported = (await importedResponse.json()).data
  if (imported.id === saved.id || imported.origin !== 'import') throw new Error('Expected an independent imported report snapshot')
  await expect(detail.getByText(/导入报告，未重新计算/)).toBeVisible()
  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '报告库', exact: true }).click()
  await page.getByRole('link', { name: '单股报告', exact: true }).click()
  await library.locator('tbody tr').filter({ hasText: title }).first().getByRole('button', { name: '查看报告', exact: true }).click()
  await expect(detail.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await expect(detail.getByText(/导入报告，未重新计算/)).toBeVisible()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: actual backtest report freeze, chart, ZIP/Excel/offline HTML, delete, import and refresh')
} finally {
  await browser.close()
  if (offlinePath) await unlink(offlinePath)
  if (offlineDirectory) await rmdir(offlineDirectory)
}
