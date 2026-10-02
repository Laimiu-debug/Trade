import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('TDX 浏览器测试')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.locator('.account-select select').waitFor()
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.evaluate(async () => {
    const session = (await (await fetch('/api/v1/session')).json()).data
    const accountId = document.querySelector('.account-select select')?.value
    if (!accountId) throw new Error('missing account for chart marker')
    const response = await fetch(`/api/v1/accounts/${accountId}/trades`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token,
        'Idempotency-Key': 'tdx-smoke-trade-2025-01-02' },
      body: JSON.stringify({ trade_date: '2025-01-02', symbol: '600000', name: '浦发银行',
        side: 'buy', quantity: 100, price: '10.50', fee_mode: 'auto' }),
    })
    if (!response.ok) throw new Error(`trade fixture failed: ${response.status}`)
  })
  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('navigation', { name: '行情页面' }).getByRole('button', { name: '导入样本', exact: true }).click()
  await page.getByLabel('本地证券库搜索（代码 / 名称 / 拼音）').fill('600000')
  await page.getByRole('button', { name: '选用' }).first().click()
  await page.getByLabel('通达信证券代码').inputValue().then(value => { if (value !== 'sh600000') throw new Error('market symbol not normalized') })
  await page.getByRole('button', { name: '导入通达信日线' }).click()
  await page.getByText('通达信日线已复制为不可变样本').waitFor()
  await page.getByText('sh600000.day').first().waitFor()
  await page.getByRole('cell', { name: '通达信本地' }).waitFor()
  await page.getByRole('button', { name: '校验全部文件哈希' }).click()
  await page.getByText('已校验 1 个文件内容').waitFor()
  await page.getByLabel('收盘价查询日期').fill('2025-01-02')
  await page.getByRole('button', { name: '查询收盘价' }).click()
  await page.getByText('收盘价：¥ 10.5000').waitFor()
  const candles = page.locator('.market-chart svg g.candle')
  await page.locator('.market-chart svg g.trade-marker.buy').first().waitFor()
  await candles.first().click()
  await candles.last().click()
  await page.getByText('成交额合计：¥ 35801.00').waitFor()
  await candles.first().dblclick()
  await page.getByRole('heading', { name: '600000 · 2025-01-02 原始一分钟分时' }).waitFor()
  await page.getByText('成交额合计：¥ 3579.00').waitFor()
  const note = `人工标注浏览器验证 ${Date.now()}`
  await page.getByLabel('人工备注').fill(note)
  await page.getByRole('button', { name: '保存人工标注' }).click()
  await page.getByText('人工标注已保存').waitFor()
  await page.locator('.market-chart svg text', { hasText: '人工启动日' }).waitFor()
  await page.getByRole('button', { name: '情绪估值' }).click()
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('button', { name: '查看' }).first().click()
  await page.getByLabel('人工备注').inputValue().then(value => { if (value !== note) throw new Error('annotation did not persist') })
  await page.locator('.market-chart svg text', { hasText: '人工启动日' }).waitFor()
  const simId = await page.evaluate(async () => {
    const session = (await (await fetch('/api/v1/session')).json()).data
    const post = async (path, body, key) => {
      const response = await fetch(`/api/v1${path}`, { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token,
          'Idempotency-Key': key }, body: JSON.stringify(body) })
      const result = await response.json()
      if (!response.ok) throw new Error(`${path}: ${JSON.stringify(result)}`)
      return result.data
    }
    const sim = await post('/sim-accounts', { name: '行情图模拟标记', initial_capital: '100000',
      start_date: '2025-01-02' }, 'tdx-smoke-sim')
    const order = await post(`/sim-accounts/${sim.id}/orders`, { symbol: '600000', side: 'buy',
      quantity: 100, limit_price: '11', signal_date: '2025-01-02', submit_date: '2025-01-02' }, 'tdx-smoke-order')
    await post(`/sim-accounts/${sim.id}/orders/${order.id}/fill`, { expected_revision: 1,
      fill_date: '2025-01-02', fill_price: '10.50' }, 'tdx-smoke-fill')
    return sim.id
  })
  await page.reload()
  await page.locator('.account-select select').selectOption(simId)
  await page.getByRole('button', { name: '行情样本' }).click()
  await page.getByRole('button', { name: '查看' }).first().click()
  await page.locator('.market-chart svg g.trade-marker.buy[aria-label^="模拟"]').waitFor()
  if (await page.locator('.market-chart svg g.trade-marker[aria-label^="实盘"]').count()) throw new Error('real account marker leaked into sim chart')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: TDX daily/minute chart, real/sim account execution markers')
} finally { await browser.close() }
