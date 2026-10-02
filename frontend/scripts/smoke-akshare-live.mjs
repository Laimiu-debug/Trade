// Manual connectivity check; requires network access to AKShare's upstream.
import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('在线行情验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('navigation', { name: '行情页面' }).getByRole('button', { name: '在线同步', exact: true }).click()
  const end = new Date()
  const start = new Date(end.getTime() - 45 * 86400_000)
  await page.getByLabel('在线来源').selectOption('akshare')
  await page.getByLabel('在线同步证券代码').fill('sh600000')
  await page.getByLabel('起始日期').fill(start.toISOString().slice(0, 10))
  await page.getByLabel('截止日期').fill(end.toISOString().slice(0, 10))
  await page.getByRole('button', { name: '同步在线日线' }).click()
  await Promise.race([
    page.getByText(/已从 AKShare 获取 .*根日线/).waitFor({ timeout: 60000 }),
    page.getByRole('alert').waitFor({ timeout: 60000 }).then(async () => {
      throw new Error(await page.getByRole('alert').innerText())
    }),
  ])
  await page.getByRole('cell', { name: 'AKShare 在线' }).waitFor()
  await page.getByText(/行情内容 SHA-256/).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: live AKShare daily sync and frozen source manifest')
} finally { await browser.close() }
