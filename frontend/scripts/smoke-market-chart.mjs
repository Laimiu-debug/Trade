import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('row', { name: /600001/ }).getByRole('button', { name: '查看' }).click()
  await page.getByRole('img', { name: /600001 日 K 线/ }).waitFor()
  const candles = page.locator('.market-chart g.candle')
  if (await candles.count() !== 60) throw new Error('Expected 60 visible candles')
  await candles.first().click()
  await candles.last().click()
  await page.getByText('交易日：60').waitFor()
  await page.getByRole('button', { name: '深色' }).click()
  await page.getByRole('img', { name: /600001 日 K 线/ }).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: 60-bar candlestick chart, interval statistics, theme switch')
} finally {
  await browser.close()
}
