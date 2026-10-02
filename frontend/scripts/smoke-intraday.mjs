// Real browser/API/SQLite with an isolated deterministic upstream fixture; no public network.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-intraday-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const fixture = String.raw`
import json, os
from datetime import datetime, timezone
from trade_app.market import intraday_online as source
from trade_app.platform.types import TradeError
source.now_utc = lambda: datetime(2026,9,25,2,0,tzinfo=timezone.utc)
calls = {}
def fetch(secid):
    calls[secid] = calls.get(secid, 0) + 1
    if secid == '1.000001' and calls[secid] > 1:
        raise TradeError('INTRADAY_TIMEOUT', '隔离来源超时；旧缓存保持不变', 504)
    price = 3000 if secid.startswith('1.') else 10
    rows = [f'2026-09-25 09:30,0,{price},0,0,200,-,0',
            f'2026-09-25 09:31,{price},{price + 1},{price + 1},{price},100,1000,{price}',
            '2026-09-25 10:01,90000,99999,99999,90000,1,1,99999']
    return json.dumps({'rc':0,'data':{'code':secid[2:],'market':int(secid[0]),'name':'隔离来源','trends':rows}}).encode()
source.fetch_bytes = fetch
import uvicorn
uvicorn.run('trade_app.main:app',host='127.0.0.1',port=int(os.environ['TRADE_REBUILD_PORT']))
`
const server = spawn(process.env.PYTHON || 'python', ['-c', fixture], { cwd: backend,
  windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port), PYTHONIOENCODING: 'utf-8' } })
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { logs = (logs + data.toString()).slice(-12000) })
try {
  const base = `http://127.0.0.1:${port}`
  for (let index = 0; index < 100; index++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded startup */ }
    if (server.exitCode !== null || index === 99) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 }, timezoneId: 'Asia/Shanghai' })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  let networkPosts = 0
  page.on('request', request => { if (request.url().endsWith('/market/intraday/fetch') && request.method() === 'POST') networkPosts++ })
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  let serial = 0
  async function post(url, body) {
    const response = await page.request.post(base + '/api/v1' + url, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `intraday-ui-${++serial}` } })
    assert.equal(response.status(), 200, await response.text())
    return (await response.json()).data
  }
  async function get(url) { const response = await page.request.get(base + '/api/v1' + url); assert.equal(response.status(), 200, await response.text()); return (await response.json()).data }
  const account = await post('/accounts', { name: '分时隔离验证' })
  const bars = ['2026-09-24', '2026-09-25'].map(event_date => ({ event_date, open: '10', high: '11', low: '9', close: '10', volume: 1000, available_at: event_date + 'T08:00:00Z' }))
  const index = await post('/market/datasets', { symbol: 'sh000001', adjustment: 'none', bars })
  const stock = await post('/market/datasets', { symbol: '000001.SZ', adjustment: 'none', bars })
  const pageUrl = id => base + `/rebuild.html?page=market&account=${account.id}&dataset=${id}`
  await page.goto(pageUrl(index.id))
  const panel = page.getByRole('region', { name: '在线分时与缓存' })
  await expect(panel.getByLabel('在线分时日期')).toHaveValue('2026-09-25')
  await expect(panel.getByRole('button', { name: '主动联网获取分时' })).toBeEnabled()
  assert.equal(networkPosts, 0)
  await panel.getByRole('button', { name: '主动联网获取分时' }).click()
  await expect(panel.getByRole('img', { name: 'sh000001 2026-09-25 一分钟收盘价曲线' })).toBeVisible()
  await expect(panel.getByText('记录：2 分钟')).toBeVisible()
  await expect(panel.getByText('成交量合计：300 手')).toBeVisible()
  await expect(panel.getByText('成交额合计：未知 元')).toBeVisible()
  await expect(panel.getByText('最高收盘：3001.0000 点')).toBeVisible()
  await expect(panel.getByText('已排除观察截止之后的分时记录。')).toBeVisible()
  const savedIndex = await get('/market/intraday/snapshots?symbol=sh000001')
  assert.equal(savedIndex.length, 1)
  await panel.getByRole('button', { name: '主动联网获取分时' }).click()
  await expect(panel.getByRole('alert')).toContainText('隔离来源超时')
  await expect(panel.getByRole('img')).toBeVisible()
  assert.equal((await get('/market/intraday/snapshots?symbol=sh000001')).length, 1)
  await page.reload()
  await expect(panel.getByRole('button', { name: /^查看缓存/ })).toBeVisible()
  assert.equal(networkPosts, 2)
  await panel.getByRole('button', { name: /^查看缓存/ }).click()
  await expect(panel.getByRole('img')).toBeVisible()
  assert.equal(networkPosts, 2)
  await page.goto(pageUrl(stock.id))
  await expect(panel.getByText(/本日缓存.*0 条/)).toBeVisible()
  await panel.getByRole('button', { name: '主动联网获取分时' }).click()
  await expect(panel.getByRole('img', { name: 'sz000001 2026-09-25 一分钟收盘价曲线' })).toBeVisible()
  await expect(panel.getByText('最高收盘：11.0000 元')).toBeVisible()
  const savedStock = await get('/market/intraday/snapshots?symbol=000001.SZ')
  assert.equal(savedStock.length, 1)
  assert.notEqual(savedStock[0].id, savedIndex[0].id)
  await panel.getByRole('button', { name: '删除缓存', exact: true }).click()
  await panel.getByRole('button', { name: '确认删除此缓存' }).click()
  await expect(panel.getByText('已删除选定的分时缓存。')).toBeVisible()
  assert.equal((await get('/market/intraday/snapshots?symbol=sz000001')).length, 0)
  assert.equal((await get('/market/intraday/snapshots?symbol=sh000001')).length, 1)
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true)
  assert.deepEqual(errors, [])
  console.log(`intraday browser passed (isolated provider fixture): explicit fetch -> PIT/null/units -> failure keeps cache -> reload no fetch -> same-code index/stock isolated -> delete; ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) { const ended = new Promise(resolve => server.once('exit', resolve)); server.kill(); await ended }
}
