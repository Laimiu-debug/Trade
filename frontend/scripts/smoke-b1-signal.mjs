import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: 'B1 多周期', exact: true }).click()
  await page.getByRole('heading', { name: 'B1 历史记录' }).waitFor()
  await page.getByRole('heading', { name: 'B1 历史记录' }).locator('..').getByRole('button', { name: '查看' }).first().click()
  await page.getByRole('button', { name: '查看 K 线' }).first().click()
  await page.getByRole('heading', { name: /600000 ·/ }).waitFor()
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: 'B1 多周期', exact: true }).click()
  await page.getByRole('heading', { name: 'B1 结果', exact: false }).waitFor()
  await page.getByRole('button', { name: '转观察信号' }).first().click()
  await page.getByRole('heading', { name: /运行结果/ }).waitFor()
  await page.getByRole('heading', { name: '加入模拟委托草稿' }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: B1 hit opens frozen chart and promotes to draft eligible signal')
} finally { await browser.close() }
