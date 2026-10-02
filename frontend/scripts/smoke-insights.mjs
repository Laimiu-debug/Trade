import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('dialog', dialog => dialog.accept())
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('灵感浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '灵感闪记' }).click()
  await page.getByLabel('内容').fill('先控制仓位，再寻找机会')
  await page.getByLabel('标签（逗号分隔）').fill('纪律,仓位')
  await page.getByRole('button', { name: '记下（Ctrl+Enter）' }).click()
  await page.getByText('先控制仓位，再寻找机会').waitFor()
  await page.getByLabel('内容').fill('只做计划内的交易')
  await page.getByLabel('标签（逗号分隔）').fill('纪律')
  await page.getByRole('button', { name: '记下（Ctrl+Enter）' }).click()
  await page.getByText('只做计划内的交易').waitFor()
  await page.getByRole('button', { name: '仓位' }).click()
  if (await page.getByText('只做计划内的交易').count()) throw new Error('Tag filter failed')
  await page.getByRole('button', { name: '删除' }).click()
  await page.getByText('这个标签下还没有卡片').waitFor()
  await page.getByRole('button', { name: '总览' }).click()
  await page.getByRole('heading', { name: '今日旧卡温故' }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: inspiration cards, tags, soft deletion, daily panel')
} finally {
  await browser.close()
}
