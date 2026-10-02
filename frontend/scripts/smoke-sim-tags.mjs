import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('dialog', dialog => {
    const message = dialog.message()
    dialog.accept(message.includes('名称') ? '标签浏览器测试' :
      message.includes('初始模拟资金') ? '10000' : '9')
  })
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('button', { name: '新增模拟' }).click()
  await page.getByRole('heading', { name: '模拟交易' }).waitFor()
  await page.waitForFunction(() => document.querySelector('.sim-metrics strong')?.textContent?.trim() !== '—')
  await page.getByLabel('代码').fill('600000')
  await page.getByLabel('限价').fill('10')
  await page.getByLabel('信号日期').fill(new Date().toLocaleDateString('sv-SE'))
  await page.getByRole('button', { name: '提交模拟委托' }).click()
  await page.getByText('模拟委托已提交').waitFor({ timeout: 10000 })
  await page.getByRole('navigation', { name: '模拟交易页面' }).getByRole('button', { name: '委托与结算', exact: true }).click()
  await page.getByRole('button', { name: '确认成交' }).click()
  await page.getByRole('navigation', { name: '模拟交易页面' }).getByRole('button', { name: '成交与复盘', exact: true }).click()
  const tags = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '模拟成交复盘标签' }) })
  await tags.getByLabel('新标签名称').fill('突破')
  await tags.getByRole('button', { name: '添加标签' }).click()
  await tags.getByText('原因 · 突破').first().waitFor()
  await tags.getByLabel('选择成交记录').selectOption({ index: 1 })
  await tags.getByRole('checkbox', { name: '突破' }).check()
  await tags.getByRole('button', { name: '保存成交标签' }).click()
  await tags.getByText('成交标签已保存').waitFor()
  await tags.getByRole('cell', { name: '1', exact: true }).first().waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: simulated fill review tag and stats')
} finally {
  await browser.close()
}
