import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('异动验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '严重异动' }).click()
  const panel = page.getByRole('heading', { name: '严重异动扫描 · 冻结行情' }).locator('..')
  await panel.getByLabel('开始日期').fill('2025-01-01')
  await panel.getByLabel('结束日期').fill('2025-04-30')
  await panel.getByLabel('扫描模式').selectOption('full')
  await panel.getByRole('button', { name: '启动全市场异动扫描' }).click()
  await panel.getByRole('button', { name: '查看结果' }).waitFor({ timeout: 30000 })
  await panel.getByRole('button', { name: '查看结果' }).click()
  await panel.getByRole('heading', { name: /异动结果 ·/ }).waitFor()
  await panel.getByRole('cell', { name: /sh000002/ }).first().waitFor()
  await panel.getByRole('button', { name: '日度诊断' }).first().click()
  await panel.getByRole('columnheader', { name: '10 日累计偏离' }).waitFor()
  await panel.getByRole('button', { name: '查看 K 线' }).first().click()
  await page.getByRole('heading', { name: /600000 ·/ }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: abnormal scan, frozen benchmark, daily diagnostics and chart link')
} finally { await browser.close() }
