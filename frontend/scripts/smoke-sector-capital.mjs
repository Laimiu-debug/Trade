import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('板块资金验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '板块资金' }).click()
  const panel = page.getByRole('heading', { name: '板块资金热度 · 通达信指数' }).locator('..')
  await panel.waitFor()
  await panel.getByLabel('开始日期').fill('2024-08-01')
  await panel.getByLabel('结束日期').fill('2024-09-16')
  await panel.getByLabel('每日 Top N').fill('1')
  await panel.getByLabel('每指数冻结 K 线数量').fill('260')
  await panel.getByRole('button', { name: '扫描板块资金热度' }).click()
  await panel.getByRole('button', { name: '查看结果' }).waitFor({ timeout: 30000 })
  await panel.getByRole('button', { name: '查看结果' }).click()
  await panel.getByRole('heading', { name: /结果 ·/ }).waitFor()
  await panel.getByRole('cell', { name: '石油石化' }).first().waitFor()
  await panel.getByRole('img', { name: /资金热度曲线/ }).waitFor()
  await panel.getByLabel('明细范围').selectOption('all')
  await panel.getByRole('cell', { name: '煤炭' }).first().waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: TDX sector flow job, ranked sectors, series and daily detail')
} finally { await browser.close() }
