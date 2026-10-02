// Real Edge + API + SQLite, actual legacy exporter fixture, isolated data directory.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const legacyBackend = fileURLToPath(new URL('../../../final-trade/backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-legacy-report-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const fixture = String.raw`
import os
from pathlib import Path
from test_legacy_reports import original_package
Path(os.environ['TRADE_REBUILD_DATA_DIR'], 'original.ftbt').write_bytes(original_package.__wrapped__()[0])
import uvicorn
uvicorn.run('trade_app.main:app', host='127.0.0.1', port=int(os.environ['TRADE_REBUILD_PORT']))
`
const server = spawn(process.env.PYTHON || 'python', ['-c', fixture], { cwd: backend,
  windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port), PYTHONIOENCODING: 'utf-8',
    PYTHONPATH: [backend, path.join(backend, 'tests'), legacyBackend].join(path.delimiter) } })
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { logs = (logs + data.toString()).slice(-16000) })
try {
  const base = `http://127.0.0.1:${port}`
  for (let index = 0; index < 150; index++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded startup */ }
    if (server.exitCode !== null || index === 149) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1400, height: 950 }, timezoneId: 'Asia/Shanghai' })
  const errors = [], external = [], mutations = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (!request.url().startsWith(base)) external.push(request.url())
    if (request.url().startsWith(base + '/api/v1/') && request.method() !== 'GET' && /\/(research|backtests|portfolio-backtests)(\/|$)/.test(request.url())) mutations.push(request.url())
  })
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const created = await page.request.post(base + '/api/v1/accounts', { data: { name: '旧档案隔离验证' }, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': 'legacy-ui-account' } })
  assert.equal(created.status(), 200, await created.text())
  await page.goto(base + '/rebuild.html')
  await page.getByRole('button', { name: '历史回测', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '报告库', exact: true }).click()
  await page.getByRole('link', { name: '旧版报告', exact: true }).click()
  const library = page.getByRole('region', { name: '旧报告与平原档案', exact: true })
  await expect(library.getByRole('heading', { name: '旧报告与平原档案' })).toBeVisible()
  const file = library.getByLabel('旧 FTBT / 平原 JSON / HTML 文件')
  const root = base + '/api/v1/research/legacy-reports'
  const getRows = async () => (await (await page.request.get(root)).json()).data
  const uploadPath = path.join(dataDir, 'original.ftbt')
  await file.setInputFiles(uploadPath)
  await library.getByRole('button', { name: '预览旧报告' }).click()
  const preview = library.getByRole('region', { name: '旧报告导入预览' })
  await expect(preview.getByText(/可转为只读结构报告/)).toBeVisible()
  assert.equal((await getRows()).length, 0)
  await expect(preview.getByRole('button', { name: '确认保存只读旧报告' })).toBeDisabled()
  await preview.getByRole('checkbox').check()
  await preview.getByRole('button', { name: '确认保存只读旧报告' }).click()
  const detail = library.getByRole('region', { name: '旧档案详情' })
  await expect(detail.getByText('源收益 10.00%', { exact: true })).toBeVisible()
  await expect(detail.getByText('<script>untrusted()</script>', { exact: true })).toBeVisible()
  assert.equal(await detail.locator('iframe, script, img').count(), 0)
  await detail.getByRole('button', { name: '查看旧参数点 1' }).click()
  const point = detail.getByRole('region', { name: '旧参数点详情' })
  await expect(point.getByText('window_days', { exact: true })).toBeVisible()
  await expect(point.getByRole('img', { name: '旧报告原资产曲线，未重新计算' })).toBeVisible()
  await detail.getByRole('button', { name: '查看旧参数点 2' }).click()
  await expect(point.getByText('原包未提供此点完整回测详情；未生成替代结果。')).toBeVisible()
  const saved = (await getRows())[0]
  const rawHtml = await page.request.get(root + '/' + saved.id + '/original.bin?name=report.html')
  assert.equal(rawHtml.headers()['content-type'], 'application/octet-stream')
  assert.equal(rawHtml.headers()['x-content-type-options'], 'nosniff')
  assert.ok(rawHtml.headers()['content-disposition'].startsWith('attachment;'))
  assert.ok((await rawHtml.text()).includes('<script>'))
  const safeHtml = await page.request.get(root + '/' + saved.id + '/report.html')
  assert.ok(!(await safeHtml.text()).includes('<script>'))
  assert.ok(safeHtml.headers()['content-security-policy'].includes('sandbox'))
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
  await page.setViewportSize({ width: 1400, height: 950 })
  // Same raw bytes deduplicate even if uploaded again through the full UI.
  await file.setInputFiles(uploadPath)
  await library.getByRole('button', { name: '预览旧报告' }).click()
  await preview.getByRole('checkbox').check()
  await preview.getByRole('button', { name: '确认保存只读旧报告' }).click()
  await expect(library.getByRole('status')).toContainText('同一原件复用已有记录及首次保存方式')
  assert.equal((await getRows()).length, 1)
  await page.reload()
  await library.getByRole('button', { name: '查看旧档案' }).click()
  await expect(detail.getByText('源收益 10.00%', { exact: true })).toBeVisible()
  // An opaque old HTML is archived only. No script is loaded or executed.
  await file.setInputFiles({ name: 'untrusted.html', mimeType: 'text/html', buffer: Buffer.from('<script>fetch("https://invalid.example")</script>') })
  await library.getByRole('button', { name: '预览旧报告' }).click()
  await expect(preview.getByText('run_request.json', { exact: true })).toBeVisible()
  await expect(preview.getByRole('button', { name: '确认保存只读旧报告' })).toHaveCount(0)
  await preview.getByRole('checkbox').check()
  await preview.getByRole('button', { name: '确认仅保存原件' }).click()
  await expect(detail.getByText(/archive-only/)).toBeVisible()
  await expect(detail.getByRole('region', { name: '旧平原实验' })).toHaveCount(0)
  assert.equal((await getRows()).length, 2)
  const targetRow = library.locator('tbody tr').filter({ hasText: '旧报告 · untrusted.html' })
  await targetRow.getByRole('button', { name: '删除旧档案', exact: true }).click()
  assert.equal((await getRows()).length, 2)
  await library.getByRole('button', { name: '确认删除旧档案' }).click()
  await expect(library.getByRole('status')).toContainText('旧档案已从列表删除')
  assert.equal((await getRows()).length, 1)
  assert.ok(mutations.every(url => url.includes('/research/legacy-reports/')), JSON.stringify(mutations))
  assert.equal((await (await page.request.get(base + '/api/v1/backtests')).json()).data.length, 0)
  assert.deepEqual(external, []); assert.deepEqual(errors, [])
  assert.ok((await readFile(uploadPath)).length > 0)
  console.log(`legacy reports browser passed: actual exporter -> preview/confirm -> original plateau/point details -> safe downloads -> 375px -> dedup/reload -> archive-only/delete; ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) { const ended = new Promise(resolve => server.once('exit', resolve)); server.kill(); await ended }
}
