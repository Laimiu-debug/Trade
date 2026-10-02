import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('通达信全市场验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '四步漏斗', exact: true }).click()
  await page.getByRole('heading', { name: '四步选股漏斗' }).waitFor()
  await page.getByLabel('截至日期').fill('2025-10-27')
  await page.getByRole('button', { name: '启动全市场筛选任务' }).click()
  await page.getByText('1/1', { exact: false }).waitFor({ timeout: 30000 })
  await page.getByRole('button', { name: '查看结果' }).click()
  await page.getByRole('heading', { name: /筛选结果/ }).waitFor()
  await page.getByRole('button', { name: /输入池 1/ }).click()
  await page.getByRole('cell', { name: '600000' }).last().waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: TDX universe job progress and frozen funnel result')
} finally { await browser.close() }
