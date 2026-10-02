import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('dialog', dialog => dialog.accept())
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.locator('.account-select select option').first().waitFor({ state: 'attached' })
  const simId = await page.locator('.account-select select option').evaluateAll(options =>
    options.find(option => option.textContent.includes('（模拟）'))?.value || '')
  if (!simId) throw new Error('No active simulation account available')
  await page.locator('.account-select select').selectOption(simId)
  await page.getByRole('navigation', { name: '模拟交易页面' }).getByRole('button', { name: '账户恢复', exact: true }).click()
  await page.getByRole('heading', { name: '重置模拟账户' }).waitFor()
  await page.getByRole('button', { name: '创建新账户并保留恢复点' }).click()
  await page.waitForFunction(oldId => document.querySelector('.account-select select')?.value !== oldId, simId)
  const replacementId = await page.locator('.account-select select').inputValue()
  await page.locator('.account-select select').selectOption(simId)
  await page.getByRole('heading', { name: /只读模拟恢复点/ }).waitFor()
  await page.getByRole('button', { name: '恢复为可操作账户' }).click()
  await page.getByRole('navigation', { name: '模拟交易页面' }).getByRole('button', { name: '账户恢复', exact: true }).click()
  await page.getByRole('heading', { name: '重置模拟账户' }).waitFor()
  if (await page.locator('.account-select select').inputValue() !== simId)
    throw new Error('Recovery did not reactivate the original account')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(`browser smoke: reset preserved ${simId} and activated recovery; successor ${replacementId} remains`)
} finally {
  await browser.close()
}
