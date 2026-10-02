import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('收益统计浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '资金流水' }).click()
  await page.getByLabel('日期').fill('2025-01-01')
  await page.getByLabel('类型').selectOption('initial')
  await page.getByLabel('金额').fill('10000')
  await page.getByRole('button', { name: '保存流水' }).click()
  await page.getByRole('button', { name: '资产快照' }).click()
  await page.getByLabel('日期').fill('2025-01-01')
  await page.getByLabel('总资产').fill('10000')
  await page.getByRole('button', { name: '保存快照' }).click()
  await page.getByLabel('日期').fill('2025-01-02')
  await page.getByLabel('总资产').fill('11000')
  await page.getByRole('button', { name: '保存快照' }).click()
  await page.getByRole('button', { name: '统计分析', exact: true }).click()
  const summary = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '收益与回合统计' }) })
  await summary.getByRole('cell', { name: '10.0000%' }).waitFor({ timeout: 10000 })
  await summary.getByRole('button', { name: '2025-01-02' }).click()
  await summary.getByText('基线快照 2025-01-01').waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: confirmed snapshot daily return and detail')
} finally {
  await browser.close()
}
