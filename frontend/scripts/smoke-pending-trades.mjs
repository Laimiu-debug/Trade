import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('待确认浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '交易流水' }).click()
  await page.getByRole('heading', { name: '待确认交易' }).waitFor()
  const form = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '新增交易' }) })
  await form.getByLabel('交易日期').fill('2025-01-03')
  await form.getByLabel('证券代码').fill('600000')
  await form.getByLabel('数量').fill('100')
  await form.getByLabel('价格').fill('10')
  await form.getByLabel('先加入待确认交易，核对后再计入账本').check()
  await form.getByRole('button', { name: '加入待确认' }).click()
  const pending = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '待确认交易' }) })
  await pending.getByText('600000').waitFor()
  await pending.getByRole('button', { name: '确认', exact: true }).click()
  await page.getByText('已确认并写入正式交易').waitFor()
  const formal = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '交易记录' }) })
  await formal.getByText('600000').waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: staged trade, confirmed trade, formal ledger')
} finally {
  await browser.close()
}
