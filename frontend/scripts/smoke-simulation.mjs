import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('dialog', dialog => {
    const message = dialog.message()
    const answer = message.includes('名称') ? '浏览器模拟测试' : message.includes('初始模拟资金') ? '10000' : '9'
    dialog.accept(answer)
  })
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('button', { name: '新增模拟' }).click()
  await page.getByRole('heading', { name: '模拟交易' }).waitFor()
  await page.waitForFunction(() => document.querySelector('#sim-order-form input[type=date]')?.value)
  await page.getByLabel('代码').fill('600000')
  await page.getByLabel('限价').fill('10')
  await page.getByRole('button', { name: '提交模拟委托' }).click()
  await page.getByRole('navigation', { name: '模拟交易页面' }).getByRole('button', { name: '委托与结算', exact: true }).click()
  await page.getByRole('button', { name: '确认成交' }).click()
  await page.getByText('已成交').waitFor()
  await page.getByText('暂无模拟持仓').waitFor({ state: 'detached' })
  const currentDay = await page.getByLabel('结算到').inputValue()
  const nextDay = new Date(`${currentDay}T00:00:00Z`)
  nextDay.setUTCDate(nextDay.getUTCDate() + 1)
  await page.getByLabel('结算到').fill(nextDay.toISOString().slice(0, 10))
  await page.getByRole('button', { name: '仅推进日期' }).click()
  await page.getByRole('navigation', { name: '模拟交易页面' }).getByRole('button', { name: '持仓与下单', exact: true }).click()
  await page.getByRole('button', { name: '快捷卖出' }).click()
  const order = page.locator('#sim-order-form')
  if (await order.getByLabel('方向').inputValue() !== 'sell' || await order.getByLabel('代码').inputValue() !== '600000' || await order.getByLabel('数量').inputValue() !== '100') throw new Error('快捷卖出预填错误')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: simulation account, order, fill, position and quick sell draft')
} finally {
  await browser.close()
}
