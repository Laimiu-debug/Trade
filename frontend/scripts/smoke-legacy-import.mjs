import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-legacy-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const base = `http://127.0.0.1:${port}`
const server = spawn(process.env.PYTHON || 'python', ['-m', 'uvicorn', 'trade_app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: fileURLToPath(new URL('../../backend', import.meta.url)), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port) },
})
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', value => { logs = (logs + value.toString()).slice(-12000) })
try {
  for (let count = 0; count < 100; count++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded owned server startup */ }
    if (server.exitCode !== null || count === 99) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = [], external = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (!request.url().startsWith(base)) external.push(request.url()) })
  let csrf = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  page.on('response', async response => { if (response.url().endsWith('/api/v1/session') && response.ok()) csrf = (await response.json()).data.csrf_token })
  const api = async (url, method = 'GET', data) => {
    const response = await page.request.fetch(base + '/api/v1' + url, { method, data,
      headers: method === 'GET' ? {} : { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() } })
    if (!response.ok()) throw new Error(`${url} ${response.status()} ${await response.text()}`)
    return (await response.json()).data
  }
  const payload = { exported_at: '2025-01-05T10:00:00',
    capital_flows: [{ id: 1, flow_date: '2025-01-01', kind: 'initial', amount: 10000 }],
    trades: [{ id: 1, trade_date: '2025-01-02', code: '600000.SH', side: 'buy', qty: 100, price: '10.1234', fee_commission: 5, fee_transfer: '0.01' }],
    daily_reviews: [{ id: 1, review_date: '2025-01-02', market_observation: '旧人工观察', scores: '{"discipline":{"ai":6,"final":8,"comment":"自己核对"}}', trade_scores: '{"1":{"timing":{"final":7}}}' }],
    snapshots: [{ id: 1, snap_date: '2025-01-02', total_assets: 10000, available_cash: null, position_value: null, positions: '[]' }],
    settings: [{ key: 'ai_score_api_key', value: 'fixture-key-never-persist' }],
    round_reviews: [{ id: 1, review_summary: '只读旧回合', code: '600000', start_date: '2025-01-02' }],
  }
  const original = Buffer.from(JSON.stringify(payload))
  const open = async () => {
    await page.goto(`${base}/rebuild.html?page=settings&settings_group=legacy`)
    await expect(page.getByRole('heading', { name: '旧资料导入', exact: true })).toBeVisible()
  }
  await open()
  await page.getByLabel('旧资料文件').setInputFiles({ name: 'fixture-laimiu.json', mimeType: 'application/json', buffer: original })
  await expect(page.getByText(/已选择：fixture-laimiu.json/)).toBeVisible()
  expect((await api('/accounts')).length).toBe(0)
  await page.getByLabel('导入方式').selectOption('new_real_account')
  await page.getByLabel('新账户名称').fill('导入验收独立账户')
  await page.getByRole('button', { name: '预览字段与来源映射' }).click()
  await expect(page.getByRole('heading', { name: /导入预览/ })).toBeVisible()
  expect((await api('/accounts')).length).toBe(0)
  await expect(page.getByRole('button', { name: '确认导入此预览' })).toBeDisabled()
  await expect(page.getByText('检测并脱敏 1 处 credential 字段。')).toBeVisible()
  await page.getByRole('checkbox', { name: /我已核对映射/ }).check()
  await page.getByRole('button', { name: '确认导入此预览' }).click()
  await expect.poll(async () => (await api('/accounts')).length).toBe(1)
  const account = (await api('/accounts'))[0]
  const trades = await api(`/accounts/${account.id}/trades`)
  expect(trades[0].symbol).toBe('sh600000'); expect(trades[0].price).toBe('10.1234'); expect(trades[0].fee).toBe('5.01')
  const scores = await api(`/accounts/${account.id}/daily-reviews/2025-01-02/scores`)
  expect(scores.some(row => row.scope === 'trade' && row.trade_ids[0] === trades[0].id)).toBe(true)
  await open()
  await expect(page.getByRole('cell', { name: /^fixture-laimiu.json/ })).toBeVisible()
  await page.getByRole('button', { name: '查看档案' }).first().click()
  await expect(page.getByRole('heading', { name: 'fixture-laimiu.json · 只读导入凭证' })).toBeVisible()
  const exported = await page.request.get(base + await page.getByRole('link', { name: '下载脱敏逻辑档案 JSON' }).getAttribute('href'))
  const archive = await exported.json()
  expect(archive.original_bytes_included).toBe(false)
  expect(JSON.stringify(archive)).not.toContain('fixture-key-never-persist')
  expect(archive.logical_data.round_reviews[0].review_summary).toBe('只读旧回合')
  await page.getByLabel('档案资料分组').selectOption('settings')
  await page.getByText('settings / 1', { exact: true }).click()
  await expect(page.getByText(/REDACTED: legacy credential/)).toBeVisible()
  const invalid = Buffer.from(JSON.stringify({ ...payload, trades: [{ ...payload.trades[0], price: '1.00001' }] }))
  await page.getByLabel('旧资料文件').setInputFiles({ name: 'invalid.json', mimeType: 'application/json', buffer: invalid })
  await page.getByLabel('导入方式').selectOption('new_real_account')
  await page.getByRole('button', { name: '预览字段与来源映射' }).click()
  await expect(page.getByText(/价格最多保留四位小数/)).toBeVisible()
  await expect(page.getByRole('button', { name: '确认导入此预览' })).toBeDisabled()
  expect((await api('/accounts')).length).toBe(1)
  const final = { schema_version: 1, account: { cash: 12345, as_of_date: '2025-01-03' }, orders: [], fills: [{ symbol: 'sh600000', side: 'buy', fill_price: 10 }], lots: [] }
  await page.getByLabel('旧资料文件').setInputFiles({ name: 'sim_state.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(final)) })
  await page.getByLabel('导入方式').selectOption('archive_only')
  await page.getByRole('button', { name: '预览字段与来源映射' }).click()
  await expect(page.getByRole('heading', { name: /导入预览 · final-trade 模拟资料/ })).toBeVisible()
  await page.getByRole('checkbox', { name: /我已核对映射/ }).check()
  await page.getByRole('button', { name: '确认导入此预览' }).click()
  await expect.poll(async () => (await api('/legacy-imports')).length).toBe(2)
  expect((await api('/accounts')).length).toBe(1)
  await open()
  await expect(page.getByRole('cell', { name: /^sim_state.json/ })).toBeVisible()
  const archiveFirst = { ...payload, exported_at: '2025-01-06T10:00:00' }
  await page.getByLabel('旧资料文件').setInputFiles({ name: 'archive-first.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(archiveFirst)) })
  await page.getByRole('button', { name: '预览字段与来源映射' }).click()
  await page.getByRole('checkbox', { name: /我已核对映射/ }).check()
  await page.getByRole('button', { name: '确认导入此预览' }).click()
  await expect(page.getByRole('heading', { name: 'archive-first.json · 只读导入凭证' })).toBeVisible()
  expect((await api('/accounts')).length).toBe(1)
  await page.getByLabel('档案转换的新账户名称').fill('明确从档案转换')
  await page.getByRole('button', { name: '预览此档案转换' }).click()
  await expect(page.getByRole('heading', { name: /档案转换预览/ })).toBeVisible()
  expect((await api('/accounts')).length).toBe(1)
  await expect(page.getByRole('button', { name: '确认从档案建立新账户' })).toBeDisabled()
  await page.getByRole('checkbox', { name: /我已核对映射/ }).check()
  await page.getByRole('button', { name: '确认从档案建立新账户' }).click()
  await expect.poll(async () => (await api('/accounts')).length).toBe(2)
  await expect.poll(() => new URL(page.url()).searchParams.get('page')).toBe('overview')
  const promoted = (await api('/legacy-imports')).find(item => item.filename === 'archive-first.json')
  expect(promoted.revision).toBe(2); expect(promoted.promoted).toBe(true)
  await open()
  const archiveRow = page.getByRole('row').filter({ hasText: 'archive-first.json' })
  await archiveRow.getByRole('button', { name: '查看档案' }).click()
  await expect(page.getByText(/档案版本 2 · 已显式转换/)).toBeVisible()
  await expect(page.getByRole('button', { name: '预览此档案转换' })).toHaveCount(0)
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  expect(errors).toEqual([]); expect(external).toEqual([])
  expect(original.toString()).toBe(JSON.stringify(payload))
  console.log(JSON.stringify({ status: 'passed', dataDir, assertions: ['preview_no_writes', 'explicit_hash_bound_import', 'decimal_reference_parity', 'readonly_sim_archive', 'redacted_export', 'invalid_precision_blocked', 'reload', 'archive_promotion_revision', 'account_navigation', '320px', 'no_external_network'] }))
} finally {
  await browser?.close()
  server.kill()
  await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); setTimeout(resolve, 5000).unref() })
}
