import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('人工评分浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '每日复盘' }).click()
  const scores = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '人工复盘评分' }) })
  await scores.getByLabel('仓位控制').selectOption('8')
  await scores.getByLabel('整体点评').fill('仓位适中')
  await scores.getByRole('button', { name: '保存人工评分' }).click()
  await scores.getByText('人工最终评分已保存').waitFor()
  await page.reload()
  await page.getByRole('button', { name: '每日复盘' }).click()
  await page.waitForFunction(() => [...document.querySelectorAll('section.card')].some(section => section.querySelector('h2')?.textContent === '人工复盘评分' && [...section.querySelectorAll('select')].some(select => select.value === '8')))
  if (await scores.getByLabel('仓位控制').inputValue() !== '8') throw new Error('人工评分未恢复')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: manual daily score save and reload')
} finally {
  await browser.close()
}
