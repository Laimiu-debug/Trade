// Manual browser integration check against the local rebuild service.
import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('批量行情验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByLabel('在线来源').selectOption('baostock')
  const end = new Date()
  const start = new Date(end.getTime() - 30 * 86400_000)
  await page.getByLabel('起始日期').fill(start.toISOString().slice(0, 10))
  await page.getByLabel('截止日期').fill(end.toISOString().slice(0, 10))
  await page.getByLabel('证券代码（逗号、空格或换行分隔）').fill('sh600000')
  await page.getByRole('button', { name: '提交批量任务' }).click()
  await page.getByText(/已提交 1 只证券的同步任务/).waitFor()
  await page.getByRole('cell', { name: '1/1' }).waitFor({ timeout: 65000 })
  const finalState = await page.locator('table').last().locator('tbody tr').first().locator('td').nth(2).innerText()
  if (!['已完成', '部分失败'].includes(finalState)) throw new Error(`Unexpected state: ${finalState}`)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(`browser smoke: batch market sync progress visible (${finalState})`)
} finally { await browser.close() }
