import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  await page.getByRole('button', { name: '运行信号判断' }).click()
  await page.getByRole('heading', { name: '加入模拟委托草稿' }).waitFor()
  await page.getByLabel('委托限价').fill('10')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByText('观察信号已加入所选模拟账户的委托草稿').waitFor()
  await page.getByRole('button', { name: '查看模拟草稿' }).click()
  await page.getByRole('button', { name: '策略草稿', exact: true }).click()
  await page.getByRole('heading', { name: '策略信号委托草稿' }).waitFor()
  await page.getByRole('button', { name: '查看' }).first().click()
  await page.getByRole('button', { name: '预览费用与资金' }).click()
  await page.getByText('资金缺口：¥ 0.00').waitFor()
  await page.getByRole('button', { name: '提交到模拟委托' }).click()
  await page.getByText('草稿已提交为模拟限价委托').waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: research signal, simulation draft, funding preview, order submission')
} finally {
  await browser.close()
}
