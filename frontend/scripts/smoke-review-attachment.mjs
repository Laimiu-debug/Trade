import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('heading', { name: '创建第一个账户' }).waitFor()
  await page.getByRole('textbox', { name: '账户名称' }).fill('截图浏览器测试')
  await page.getByRole('button', { name: '创建实盘账户' }).click()
  await page.getByRole('button', { name: '每日复盘' }).click()
  const review = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '每日复盘' }) })
  const dataUrl = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 16; canvas.height = 12
    const context = canvas.getContext('2d')
    context.fillStyle = '#2b80c0'; context.fillRect(0, 0, 16, 12)
    return canvas.toDataURL('image/png')
  })
  await review.getByLabel('选择截图').setInputFiles({
    name: '复盘截图.png', mimeType: 'image/png',
    buffer: Buffer.from(dataUrl.split(',')[1], 'base64'),
  })
  await review.getByText('图片已保存').waitFor({ timeout: 10000 })
  await review.getByRole('img', { name: '复盘截图.png' }).waitFor()
  await page.reload()
  await page.getByRole('button', { name: '每日复盘' }).click()
  await review.getByRole('img', { name: '复盘截图.png' }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: daily review image upload and reload')
} finally {
  await browser.close()
}
