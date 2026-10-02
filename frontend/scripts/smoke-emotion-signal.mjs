import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = 'http://127.0.0.1:8011'
  const session = await (await page.request.get(`${base}/api/v1/session`)).json()
  const closes = [...Array(12).fill(10), 10, 10.2, 10.4, 10.6, 10.4, 10.2, 10, 9.8, 11]
  const bars = closes.map((close, index) => ({
    event_date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10),
    open: close.toFixed(4), high: (close * 1.01).toFixed(4),
    low: (close * 0.99).toFixed(4), close: close.toFixed(4), volume: 1000,
    available_at: new Date(Date.UTC(2025, 0, index + 2)).toISOString(),
  }))
  const imported = await page.request.post(`${base}/api/v1/market/datasets`, {
    data: { symbol: '600000', adjustment: 'none', bars },
    headers: { 'X-CSRF-Token': session.data.csrf_token, 'Idempotency-Key': 'browser-emotion-market' },
  })
  if (!imported.ok()) throw new Error(await imported.text())
  await page.goto(`${base}/rebuild.html`)
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('情绪涨停验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  const panel = page.getByRole('heading', { name: '固定样本信号' }).locator('..')
  await panel.getByLabel('策略').selectOption('emotion_limit_up_v1')
  await panel.getByRole('button', { name: '运行信号判断' }).click()
  await page.getByText('研究输入和结果已按版本保存').waitFor()
  await page.getByText(/涨停共振：是/).waitFor()
  await page.getByText(/紫转黄：是/).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: emotion limit-up frozen signal and indicator detail')
} finally { await browser.close() }
