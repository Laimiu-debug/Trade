import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.locator('.account-select select').waitFor()
  const account = await page.evaluate(async () => {
    const response = await fetch('/api/v1/accounts')
    return (await response.json()).data.find(row => row.kind === 'real')
  })
  if (!account) throw new Error('print preview fixture requires one real account')
  const query = new URLSearchParams({ print: 'review', account: account.id,
    kind: 'daily', key: '2026-01-05' })
  await page.goto(`http://127.0.0.1:8011/rebuild.html?${query}`)
  await page.getByRole('heading', { name: '交易复盘与下周计划' }).waitFor()
  await page.getByRole('button', { name: '打开浏览器打印' }).waitFor()
  const picture = page.locator('.print-attachments img')
  await picture.waitFor()
  const loaded = await picture.evaluate(image => image.complete && image.naturalWidth > 0)
  if (!loaded) throw new Error('review attachment did not load in print preview')
  await page.emulateMedia({ media: 'print' })
  if (await page.locator('.print-toolbar').evaluate(element => getComputedStyle(element).display) !== 'none') throw new Error('print toolbar remains visible')
  const pdf = await page.pdf({ preferCSSPageSize: true, printBackground: true })
  if (!pdf.subarray(0, 5).equals(Buffer.from('%PDF-')) || pdf.length < 5000) throw new Error('browser PDF output invalid')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: saved review print preview, attachment, A4 print output')
} finally { await browser.close() }
