import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('趋势龙头验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('checkbox', { name: /选择 600000/ }).check()
  await page.getByRole('checkbox', { name: /选择 000001/ }).check()
  await page.getByRole('tab', { name: '趋势龙头' }).click()
  await page.getByRole('heading', { name: '趋势龙头 · 冻结行情扫描' }).waitFor()
  const panel = page.getByRole('heading', { name: '趋势龙头 · 冻结行情扫描' }).locator('..')
  await panel.getByLabel('开始日期').fill('2025-01-10')
  await panel.getByLabel('结束日期').fill('2025-02-09')
  await panel.getByLabel('涨幅窗口（交易日）').fill('5')
  await panel.getByLabel('每日 Top N').fill('1')
  await panel.getByRole('button', { name: '扫描已选 2 只' }).click()
  await panel.getByRole('heading', { name: /结果 ·/ }).waitFor()
  await panel.getByRole('cell', { name: /000001/ }).first().waitFor()
  await panel.getByText('区间涨幅曲线与每日 Top N').click()
  await panel.getByRole('img', { name: /000001 区间涨幅曲线/ }).waitFor()
  await panel.getByRole('button', { name: '启动全市场趋势任务' }).waitFor()
  await panel.getByRole('button', { name: '查看 K 线' }).click()
  await page.getByRole('heading', { name: /000001 ·/ }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: trend leader scan, history result and frozen chart link')
} finally { await browser.close() }
