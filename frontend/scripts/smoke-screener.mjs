import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('漏斗浏览器验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('button', { name: '导入样本', exact: true }).click()
  const lines = ['date,open,high,low,close,volume,available_at,amount']
  for (let index = 0; index < 300; index++) {
    const day = new Date(Date.UTC(2025, 0, 1 + index)).toISOString().slice(0, 10)
    const close = 10 + index * 0.04
    const volume = 10_000_000 + index * 20_000
    lines.push(`${day},${(close - 0.1).toFixed(4)},${(close + 0.35).toFixed(4)},${(close - 0.4).toFixed(4)},${close.toFixed(4)},${volume},,${(close * volume).toFixed(4)}`)
  }
  await page.getByLabel('证券代码', { exact: true }).fill('600000')
  await page.getByLabel('CSV 内容').fill(lines.join('\n'))
  await page.getByRole('button', { name: '保存不可变样本' }).click()
  await page.getByText(/不可变行情样本已保存/).waitFor()
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '四步漏斗', exact: true }).click()
  await page.getByRole('heading', { name: '四步选股漏斗' }).waitFor()
  await page.getByLabel(/选择 600000/).first().check()
  await page.getByLabel('600000 流通股本').fill('100000000')
  await page.getByLabel('截至日期').fill('2025-10-27')
  await page.getByRole('button', { name: /运行漏斗/ }).click()
  await page.getByRole('heading', { name: /筛选结果/ }).waitFor()
  await page.getByRole('button', { name: /输入池 1/ }).click()
  await page.getByRole('cell', { name: '600000' }).last().waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: frozen dataset to four-stage screener history')
} finally { await browser.close() }
