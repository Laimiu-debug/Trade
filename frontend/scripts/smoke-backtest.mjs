import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('回测 Excel 浏览器测试')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('button', { name: '导入样本', exact: true }).click()
  const lines = ['date,open,high,low,close,volume,available_at']
  for (let index = 0; index < 60; index++) {
    const day = new Date(Date.UTC(2025, 0, 1 + index))
    const iso = day.toISOString().slice(0, 10)
    const next = new Date(Date.UTC(2025, 0, 2 + index)).toISOString().slice(0, 10)
    const close = 10 + index * 0.15
    lines.push(`${iso},${(close - 0.1).toFixed(4)},${(close + 0.5).toFixed(4)},${(close - 0.5).toFixed(4)},${close.toFixed(4)},${1000 + index * 30},${next}T00:00:00+00:00`)
  }
  await page.getByLabel('证券代码', { exact: true }).fill('600001')
  await page.getByLabel('CSV 内容').fill(lines.join('\n'))
  await page.getByRole('button', { name: '保存不可变样本' }).click()
  await page.getByText('不可变行情样本已保存').waitFor()
  await page.getByRole('button', { name: '历史回测' }).click()
  await page.getByRole('heading', { name: '创建单股回测' }).waitFor()
  await page.getByRole('button', { name: '提交后台回测' }).click()
  await page.getByRole('heading', { name: /回测报告/ }).waitFor()
  await page.getByText('期末资产').waitFor({ timeout: 30000 })
  const [workbook] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: '导出本次回测 Excel' }).click(),
  ])
  if (!workbook.suggestedFilename().endsWith('.xlsx')) throw new Error('backtest workbook download failed')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: imported 60 bars, completed backtest and Excel export')
} finally {
  await browser.close()
}
