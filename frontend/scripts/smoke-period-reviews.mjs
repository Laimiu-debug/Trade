import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = process.env.TRADE_SMOKE_BASE_URL || 'http://127.0.0.1:8011'
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const created = await page.request.post(base + '/api/v1/accounts', { data: { name: '周期复盘浏览器测试' }, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': crypto.randomUUID() } })
  if (!created.ok()) throw new Error(await created.text())
  const account = (await created.json()).data
  await page.goto(base + '/rebuild.html?account=' + account.id)
  await page.getByRole('button', { name: '周期复盘' }).click()
  const section = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '周复盘' }) })
  await section.getByLabel('选择日期').fill('2026-01-01')
  await section.getByText('2026-W01', { exact: true }).waitFor()
  await section.getByLabel('核心目标').fill('控制仓位')
  await section.getByLabel('选择日期').fill('2026-01-15')
  await section.getByText('2026-W03', { exact: true }).waitFor()
  await section.getByLabel('选择日期').fill('2026-01-01')
  await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some(item => item.value === '控制仓位'))
  await section.getByRole('button', { name: '保存周期复盘' }).click()
  await section.getByText('周期复盘已保存').waitFor()
  await section.getByRole('button', { name: '2026-W01 · 第 1 版' }).waitFor()
  await section.getByLabel('选择日期').fill('2026-01-15')
  await section.getByRole('button', { name: '2026-W01 · 第 1 版' }).click()
  await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some(item => item.value === '控制仓位'))
  if (await section.getByLabel('核心目标').inputValue() !== '控制仓位') throw new Error('周复盘历史加载失败')
  page.once('dialog', dialog => dialog.accept())
  await section.getByRole('button', { name: '删除本周期正文' }).click()
  await section.getByText('周期复盘已删除').waitFor()
  if (await section.getByLabel('核心目标').inputValue() !== '') throw new Error('周复盘删除后正文仍存在')
  await section.getByLabel('复盘周期').selectOption('monthly')
  await page.getByRole('heading', { name: '月复盘' }).waitFor()
  const monthly = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '月复盘' }) })
  await monthly.getByLabel('月度总结').fill('观察市场节奏')
  await monthly.getByRole('button', { name: '保存周期复盘' }).click()
  await monthly.getByText('周期复盘已保存').waitFor()
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    monthly.getByRole('link', { name: '导出已保存的 Markdown' }).click(),
  ])
  if (!download.suggestedFilename().endsWith('.md')) throw new Error('Markdown 下载文件名无效')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: weekly/monthly history, metrics, delete and Markdown download')
} finally {
  await browser.close()
}
