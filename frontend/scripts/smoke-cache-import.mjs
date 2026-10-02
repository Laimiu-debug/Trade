import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('缓存导入浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByLabel('缓存来源').selectOption('akshare')
  await page.getByLabel('缓存证券代码').fill('sh600000')
  await page.getByRole('button', { name: '导入本地缓存' }).click()
  await page.getByText('本地缓存已复制为不可变样本').waitFor()
  await page.getByRole('cell', { name: 'AkShare 缓存' }).waitFor()
  await page.getByRole('cell', { name: '12345.00' }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: legacy AkShare cache import and amount display')
} finally { await browser.close() }
