import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('B1 偏好验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: 'B1 多周期', exact: true }).click()
  await page.getByRole('heading', { name: 'B1 多周期扫描' }).waitFor()
  await page.getByLabel('B1 截至日期').fill('2025-10-27')
  await page.getByLabel('当日量 / 20 日均量上限').fill('0.7')
  await page.reload()
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: 'B1 多周期', exact: true }).click()
  await page.getByRole('heading', { name: 'B1 多周期扫描' }).waitFor()
  if (await page.getByLabel('B1 截至日期').inputValue() !== '2025-10-27') throw new Error('as-of date not restored')
  if (await page.getByLabel('当日量 / 20 日均量上限').inputValue() !== '0.7') throw new Error('B1 preference not restored')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: B1 controls and screener preferences restore after reload')
} finally { await browser.close() }
