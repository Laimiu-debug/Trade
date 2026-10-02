import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.locator('.account-select select option').first().waitFor({ state: 'attached' })
  await page.getByRole('button', { name: '交易流水' }).click()
  const form = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '新增交易' }) })
  await form.getByLabel('方向').selectOption('sell')
  await form.getByLabel('价格').fill('10')
  await page.getByText('当前输入预计费用：¥ 6.01').waitFor()
  const settings = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '交易费用规则' }) })
  await settings.getByRole('button', { name: '设置费率' }).click()
  await settings.getByLabel('最低佣金（元）').fill('1.00')
  await settings.getByLabel('卖出印花税率').fill('0.0005')
  await settings.getByRole('button', { name: '保存费用规则' }).click()
  await page.getByText('当前输入预计费用：¥ 1.51').waitFor()
  await form.getByLabel('费用方式').selectOption('manual')
  await form.getByLabel('实付费用').fill('3.25')
  await page.getByText('实付覆盖 ¥ 3.25').waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: real fee preview, versioned settings, manual override')
} finally {
  await browser.close()
}
