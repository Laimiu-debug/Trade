import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('计划对照浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '每日复盘' }).click()
  const review = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '每日复盘' }) })
  await review.getByLabel('复盘日期').fill('2025-01-03')
  await review.getByLabel('大盘预判').fill('震荡')
  if (await review.getByLabel('计划执行日（默认顺延周末；节假日请手动调整）').inputValue() !== '2025-01-06') throw new Error('周末顺延错误')
  await review.getByRole('button', { name: '添加关注股' }).click()
  await review.getByLabel('代码').fill('600000')
  await review.getByLabel('触发条件').fill('十日新高')
  await review.getByRole('button', { name: '立即保存' }).click()
  await review.getByText('已保存', { exact: true }).waitFor()
  await review.getByLabel('复盘日期').fill('2025-01-06')
  const compare = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '昨日计划与今日实际' }) })
  await compare.getByText('2025-01-03 编写 · 2025-01-06 执行').waitFor()
  await compare.getByRole('cell', { name: '十日新高' }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: weekend plan target and next-day comparison')
} finally {
  await browser.close()
}
