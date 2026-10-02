import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.locator('.account-select select option').first().waitFor({ state: 'attached' })
  const realValue = await page.locator('.account-select select option').evaluateAll(options =>
    options.find(option => !option.textContent.includes('（模拟）'))?.value || '')
  if (!realValue) throw new Error('No real account available for chart smoke')
  await page.locator('.account-select select').selectOption(realValue)
  await page.getByRole('button', { name: '总览' }).click()
  await page.locator('.card canvas').first().waitFor({ timeout: 15000 })
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: on-demand net-value chart rendered')
} finally {
  await browser.close()
}
