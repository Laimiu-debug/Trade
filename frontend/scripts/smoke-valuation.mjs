import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('估值浏览器测试')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.getByRole('button', { name: '情绪估值' }).click()
  await page.getByRole('heading', { name: 'A 股五因子情绪估值' }).waitFor()
  await page.getByText('327.2 亿').first().waitFor()
  await page.getByLabel('本地证券库搜索（代码 / 名称 / 拼音）').fill('600000')
  await page.getByRole('button', { name: '选用' }).first().click()
  await page.getByLabel('代码', { exact: true }).inputValue().then(value => { if (value !== 'sh600000') throw new Error('stock code not normalized') })
  await page.getByLabel('当期盈利（亿元，手工）').fill('-1')
  await page.getByText('亏损或缺失盈利请人工核实').waitFor()
  await page.getByRole('button', { name: '载入' }).first().click()
  await page.getByLabel('名称', { exact: true }).waitFor()
  await page.getByLabel('名称', { exact: true }).inputValue().then(value => { if (value !== '长江电力') throw new Error('legacy preset not loaded') })
  await page.reload()
  await page.getByRole('button', { name: '情绪估值' }).click()
  await page.getByLabel('名称', { exact: true }).inputValue().then(value => { if (value !== '长江电力') throw new Error('valuation form not persisted') })
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: five-factor valuation, loss guard, legacy preset and local form persistence')
} finally { await browser.close() }
