import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('浏览器验证账户')
  await page.getByRole('button', { name: '创建账户' }).click()
  await page.getByRole('button', { name: '资金流水' }).click()
  await page.getByLabel('日期').fill('2025-01-02')
  await page.getByLabel('类型').selectOption('initial')
  await page.getByLabel('金额').fill('10000')
  await page.getByRole('button', { name: '保存流水' }).click()
  await page.getByText('¥ 10000.00').waitFor()
  await page.getByRole('button', { name: '资产快照' }).click()
  await page.getByLabel('日期').fill('2025-01-02')
  await page.getByLabel('总资产').fill('11000')
  await page.getByRole('button', { name: '保存快照' }).click()
  await page.getByRole('button', { name: '总览' }).click()
  await page.getByText('1.1000').waitFor({ timeout: 10000 })
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: account, initial cash flow, snapshot, NAV = 1.1000')
} finally {
  await browser.close()
}
