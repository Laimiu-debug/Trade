import { chromium, expect } from '@playwright/test'
import { readFile, stat } from 'node:fs/promises'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 }, acceptDownloads: true })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = process.env.TRADE_SMOKE_BASE_URL || 'http://127.0.0.1:8011'
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  async function post(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': crypto.randomUUID() } })
    if (!response.ok()) throw new Error(await response.text())
    return (await response.json()).data
  }
  const account = await post('/accounts', { name: '分享卡固定样本验收' })
  const start = new Date('2024-01-01T00:00:00Z')
  const bars = Array.from({ length: 280 }, (_, i) => {
    while ([0, 6].includes(start.getUTCDay())) start.setUTCDate(start.getUTCDate() + 1)
    const day = start.toISOString().slice(0, 10); start.setUTCDate(start.getUTCDate() + 1)
    return { event_date: day, open: '11', close: '11.5', high: '12', low: '10', volume: 1000000 + i, available_at: day + 'T08:00:00+00:00' }
  })
  const sample = await post('/market/datasets', { symbol: 'sh600000', bars })
  await post('/research/screener-runs', { datasets: [{ dataset_id: sample.id }], as_of_date: bars.at(-1).event_date })
  await post('/research/b1-runs', { dataset_ids: [sample.id], as_of_date: bars.at(-1).event_date })
  await page.goto(base + '/rebuild.html?account=' + account.id)
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '分享卡' }).click()
  await page.getByRole('heading', { name: '复盘分享卡' }).waitFor()
  await page.getByLabel('本地证券库搜索（代码 / 名称 / 拼音）').fill('600000')
  await page.getByRole('button', { name: '选用' }).first().click()
  await page.locator('textarea[rows="6"]').fill('测试人工复盘思考')
  await page.getByText('价格走势缺失').first().waitFor()
  const firstDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出分享卡 PNG' }).click()
  const noDataPng = await firstDownload
  if (!noDataPng.suggestedFilename().endsWith('.png') || (await stat(await noDataPng.path())).size < 1000) throw new Error('missing-data PNG invalid')
  await page.getByLabel('冻结行情样本').selectOption(sample.id)
  await expect(page.getByRole('table', { name: '证券研究历史' })).toContainText('四步漏斗')
  await expect(page.getByRole('table', { name: '证券研究历史' })).toContainText('B1 筛选')
  await page.getByText('末日收盘 ¥ 11.5000').waitFor()
  const secondDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出分享卡 PNG' }).click()
  const pricedPng = await secondDownload
  if ((await stat(await pricedPng.path())).size < 1000) throw new Error('priced PNG invalid')
  await page.locator('textarea[rows="6"]').fill('完整长文验证'.repeat(90))
  const longDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出分享卡 PNG' }).click()
  const longPng = await longDownload
  const header = await readFile(await longPng.path())
  if (header.readUInt32BE(20) <= 675) throw new Error('long note PNG was truncated')
  const alpha = await page.evaluate(async encoded => {
    const image = new Image(); image.src = 'data:image/png;base64,' + encoded; await image.decode()
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0)
    return context.getImageData(10, image.height - 10, 1, 1).data[3]
  }, header.toString('base64'))
  if (alpha !== 255) throw new Error('long PNG background is transparent below the original card height')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: share card with and without frozen market data, PNG export')
} finally { await browser.close() }
