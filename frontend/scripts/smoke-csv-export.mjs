import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('CSV 浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.locator('.export-menu summary').click()
  const [realWorkbook] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: '账户 Excel 工作簿' }).click(),
  ])
  if (!realWorkbook.suggestedFilename().endsWith('.xlsx')) throw new Error('实盘 Excel 下载失败')
  const [realFile] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: '实盘交易' }).click(),
  ])
  if (!realFile.suggestedFilename().endsWith('.csv')) throw new Error('实盘 CSV 下载失败')
  page.on('dialog', dialog => dialog.accept(dialog.message().includes('名称') ? 'CSV 模拟' : '10000'))
  await page.getByRole('button', { name: '新增模拟' }).click()
  await page.getByRole('heading', { name: '模拟交易' }).waitFor()
  if (!await page.locator('.export-menu').evaluate(item => item.open)) await page.locator('.export-menu summary').click()
  const [simWorkbook] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: '账户 Excel 工作簿' }).click(),
  ])
  if (!simWorkbook.suggestedFilename().endsWith('.xlsx')) throw new Error('模拟 Excel 下载失败')
  const [simFile] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: '模拟成交' }).click(),
  ])
  if (!simFile.suggestedFilename().endsWith('.csv')) throw new Error('模拟 CSV 下载失败')
  console.log('browser smoke: real and simulation CSV/Excel downloads')
} finally { await browser.close() }
