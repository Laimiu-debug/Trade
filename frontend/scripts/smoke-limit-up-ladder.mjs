import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('梯队验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('checkbox', { name: /选择 600002/ }).check()
  await page.getByRole('tab', { name: '涨停梯队' }).click()
  const panel = page.getByRole('heading', { name: '涨停梯队 · 冻结行情扫描' }).locator('..')
  await panel.waitFor()
  await panel.getByLabel('开始日期').fill('2025-01-09')
  await panel.getByLabel('结束日期').fill('2025-01-14')
  await panel.getByLabel('近期交易日').fill('2')
  await panel.getByLabel('历史最低连板高度').fill('2')
  await panel.getByRole('button', { name: /扫描已选/ }).click()
  await panel.getByRole('heading', { name: /梯队结果 ·/ }).waitFor()
  await panel.getByRole('cell', { name: '2 板' }).waitFor()
  await panel.getByRole('button', { name: '查看 K 线' }).first().click()
  await page.getByRole('heading', { name: /600002 ·/ }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: limit-up ladder, height, history and frozen chart link')
} finally { await browser.close() }
