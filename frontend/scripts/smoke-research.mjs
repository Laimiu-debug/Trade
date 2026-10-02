import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  await page.getByRole('heading', { name: '固定样本信号' }).waitFor()
  await page.getByRole('button', { name: '运行信号判断' }).click()
  await page.getByText('研究输入和结果已按版本保存').waitFor()
  await page.getByRole('heading', { name: /运行结果/ }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: strategy research page persisted a frozen-data run')
} finally {
  await browser.close()
}
