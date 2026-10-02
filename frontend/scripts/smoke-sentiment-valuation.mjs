import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('情绪估值验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '情绪估值' }).click()
  const panel = page.getByRole('heading', { name: '五因子情绪估值' }).locator('..')
  await panel.getByLabel('证券代码').fill('600000')
  await panel.getByLabel('当期盈利（亿元）').first().fill('12.5')
  await panel.getByLabel('年增长率（%）').first().fill('30')
  await panel.getByLabel('基准 PE', { exact: true }).first().fill('22.5')
  await panel.getByLabel('大盘点位').first().fill('3300')
  await panel.getByLabel('情绪溢价系数').first().fill('1.8')
  await panel.getByLabel('实际市值（亿元，可选）').first().fill('400')
  await panel.getByRole('button', { name: '添加对照情景' }).click()
  await panel.getByLabel('当期盈利（亿元）').last().fill('-1')
  await panel.getByRole('button', { name: '计算并保存情景' }).click()
  await panel.getByRole('heading', { name: /估值结果 ·/ }).waitFor()
  await panel.getByText('亏损 / 零盈利不适用').waitFor()
  await panel.getByRole('button', { name: '复制此记录的参数到表单' }).click()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: five-factor scenarios, loss handling and saved parameters')
} finally { await browser.close() }
