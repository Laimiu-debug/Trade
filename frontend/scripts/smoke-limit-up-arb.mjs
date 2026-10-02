import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const base = 'http://127.0.0.1:8011'
  const session = await (await page.request.get(`${base}/api/v1/session`)).json()
  const closes = [10, 10, 10, 10, 11, 11.44]
  const bars = closes.map((close, index) => ({
    event_date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10),
    open: String(close), high: String(Math.round(close * 10100) / 10000),
    low: String(Math.round(close * 9900) / 10000), close: String(close),
    volume: index === 5 ? 1600 : 1000,
    available_at: new Date(Date.UTC(2025, 0, index + 2)).toISOString(),
  }))
  const imported = await page.request.post(`${base}/api/v1/market/datasets`, {
    data: { symbol: '600000', adjustment: 'none', bars },
    headers: { 'X-CSRF-Token': session.data.csrf_token, 'Idempotency-Key': 'browser-arb-market' },
  })
  if (!imported.ok()) throw new Error(await imported.text())
  await page.goto(`${base}/rebuild.html`)
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('涨停套利验证')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究' }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  const panel = page.getByRole('heading', { name: '固定样本信号' }).locator('..')
  await panel.getByLabel('策略').selectOption('limit_up_arb_v1')
  await panel.getByRole('button', { name: '运行信号判断' }).click()
  await page.getByText('研究输入和结果已按版本保存').waitFor()
  await page.getByText(/前日涨停：是/).waitFor()
  await page.getByText(/旧策略信号价格：11.44/).waitFor()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: limit-up arbitrage frozen signal and indicator detail')
} finally { await browser.close() }
