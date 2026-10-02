import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('节点浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '资金流水' }).click()
  await page.getByLabel('日期').fill('2025-01-01')
  await page.getByLabel('类型').selectOption('initial')
  await page.getByLabel('金额').fill('100')
  await page.getByRole('button', { name: '保存流水' }).click()
  await page.getByRole('button', { name: '资产快照' }).click()
  await page.getByLabel('日期').fill('2025-01-02')
  await page.getByLabel('总资产').fill('130')
  await page.getByRole('button', { name: '保存快照' }).click()
  await page.getByRole('button', { name: '总览' }).click()
  const nodes = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '目标节点' }) })
  await nodes.getByText('已点亮 1 / 50').waitFor({ timeout: 10000 })
  await nodes.getByRole('button', { name: '配置节点' }).click()
  await nodes.getByLabel('每级倍率').fill('2.0')
  await nodes.getByLabel('节点数量').fill('2')
  await nodes.getByRole('button', { name: '保存节点配置' }).click()
  await nodes.getByText('已点亮 0 / 2').waitFor({ timeout: 10000 })
  await nodes.getByText('还差 ¥ 70.00').waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: target node settings, recomputation, next gap')
} finally {
  await browser.close()
}
