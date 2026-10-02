import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('navigation', { name: '行情页面' }).getByRole('button', { name: '导入样本', exact: true }).click()
  await page.getByLabel('证券代码').fill('600000')
  await page.getByLabel('CSV 内容').fill('date,open,high,low,close,volume,available_at\n2025-01-01,10,11,9,10.5,1000,2025-01-02T00:00:00+00:00\n2025-01-02,10.5,12,10,11.5,1200,2025-01-03T00:00:00+00:00')
  await page.getByRole('button', { name: '保存不可变样本' }).click()
  await page.getByText('不可变行情样本已保存').waitFor()
  await page.getByText('提供了可得时间').waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: imported immutable market dataset with availability metadata')
} finally {
  await browser.close()
}
