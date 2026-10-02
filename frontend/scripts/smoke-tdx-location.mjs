import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'
import { copyFile, mkdir, mkdtemp, readdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { spawn, execFile } from 'node:child_process'
import net from 'node:net'

if (!process.env.TRADE_BUNDLE_EXE) throw new Error('Set TRADE_BUNDLE_EXE to the executable under test')
const output = await mkdtemp(join(tmpdir(), 'trade-tdx-frozen-'))
const runtime = join(output, 'runtime'), source = join(output, 'app-data'), pointer = join(output, 'launcher.json')
const portable = join(output, '独立 EXE'), fixture = join(output, '临时通达信')
await mkdir(runtime); await mkdir(portable); await mkdir(join(fixture, 'vipdoc/sh/lday'), { recursive: true })
const executable = join(portable, 'TradeRebuild.exe')
await copyFile(resolve(process.env.TRADE_BUNDLE_EXE), executable)
const dates = [20250102, 20250103, 20250106]
const bytes = Buffer.concat(dates.map(day => {
  const row = Buffer.alloc(32)
  ;[day, 1000, 1100, 900, 1050].forEach((value, index) => row.writeUInt32LE(value, index * 4))
  row.writeFloatLE(100000, 20); row.writeUInt32LE(10000, 24)
  return row
}))
const fixtureFile = join(fixture, 'vipdoc/sh/lday/sh600000.day')
await writeFile(fixtureFile, bytes)
const binding = net.createServer(); await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port; await new Promise(resolve => binding.close(resolve))
const base = `http://127.0.0.1:${port}`
const env = { ...process.env, TEMP: runtime, TMP: runtime, TMPDIR: runtime, PYINSTALLER_STRICT_UNPACK_MODE: '1' }
delete env.TRADE_TDX_ROOT
let child, exited, browser, token, logs = ''
function launch() {
  child = spawn(executable, ['--data-dir', source, '--state-file', pointer, '--port', String(port), '--no-browser', '--no-tray'],
    { cwd: dirname(executable), windowsHide: true, env, stdio: ['ignore', 'pipe', 'pipe'] })
  child.stdout.on('data', data => { logs += data }); child.stderr.on('data', data => { logs += data })
  exited = new Promise(resolve => { child.once('exit', resolve); child.once('error', error => resolve(String(error))) })
}
launch()
try {
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  async function ready() {
    await expect.poll(async () => { try { return (await fetch(base + '/health')).ok } catch { return false } }, { timeout: 45000 }).toBe(true)
    token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  }
  async function get(path) {
    const response = await page.request.get(base + '/api/v1' + path)
    assert(response.ok(), await response.text()); return (await response.json()).data
  }
  async function write(path, body) {
    const response = await page.request.post(base + '/api/v1' + path, { data: body,
      headers: { 'X-CSRF-Token': token, 'Idempotency-Key': crypto.randomUUID() } })
    assert(response.ok(), await response.text()); return (await response.json()).data
  }
  async function stop() {
    await write('/system/lifecycle/exit', {})
    assert.equal(await Promise.race([exited, new Promise(resolve => setTimeout(() => resolve('timeout'), 20000))]), 0, logs)
    assert.deepEqual((await readdir(runtime)).filter(name => name.startsWith('_MEI')), [])
  }
  await ready()
  const initial = await get('/market/providers/tdx/locations')
  assert.equal(initial.persisted, false)
  assert.equal(initial.current_path, initial.candidates.length === 1 ? initial.candidates[0].path : null)
  await page.goto(base + '/rebuild.html?page=settings&settings_group=market_sources')
  const panel = page.getByRole('region', { name: '通达信目录选择' })
  await expect(panel.getByRole('button', { name: '选择通达信目录', exact: true })).toBeVisible()
  // Native dialog plumbing has its own platform tests; use text entry for unattended UI smoke.
  await panel.getByLabel('通达信安装目录或 vipdoc 目录').fill(fixture)
  await panel.getByRole('button', { name: '使用此目录', exact: true }).click()
  await expect(panel.getByText('本次运行已使用所选目录，可以检查或导入通达信日线。', { exact: true })).toBeVisible()
  assert.equal((await get('/market/providers/tdx/locations')).current_path, fixture)
  await page.getByRole('button', { name: '检查 本地通达信', exact: true }).click()
  await expect(page.getByText('本地样本可读', { exact: true })).toBeVisible()
  assert.deepEqual(await get('/market/datasets'), [])
  const probe = await write('/market/providers/tdx/probe', { symbol: '600000', start_date: '2025-01-01', end_date: '2025-01-07' })
  assert.equal(probe.sample_count, 3); assert.equal(probe.first_date, '2025-01-02'); assert.equal(probe.last_date, '2025-01-06')
  const imported = await write('/market/tdx-import', { symbol: '600000' })
  assert.equal(imported.bar_count, 3)
  const job = await write('/research/tdx-ladder-jobs', { markets: ['sh'], date_from: '2025-01-02', date_to: '2025-01-06', max_bars: 251 })
  await expect.poll(async () => (await get('/research/tdx-universe-jobs/' + job.id)).state, { timeout: 45000 }).toBe('succeeded')
  // Explicit scan reports actual local candidates but must preserve the manual source.
  await panel.getByRole('button', { name: '扫描本机', exact: true }).click()
  await expect(panel.getByRole('button', { name: '扫描本机', exact: true })).toBeEnabled()
  const rescanned = await get('/market/providers/tdx/locations')
  assert.equal(rescanned.current_path, fixture)
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: join(output, 'desktop.png') })
  await page.setViewportSize({ width: 375, height: 850 }); await panel.scrollIntoViewIfNeeded()
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Page must fit a narrow viewport')
  await page.screenshot({ path: join(output, 'mobile.png') })
  assert.deepEqual(await readFile(fixtureFile), bytes)
  await stop(); launch(); await ready()
  const reopened = await get('/market/providers/tdx/locations')
  assert.notEqual(reopened.current_path, fixture)
  assert.equal(reopened.selection, reopened.candidates.length === 1 ? 'auto' : 'unset')
  assert.equal(reopened.current_path, initial.current_path)
  assert((await get('/market/datasets')).some(item => item.id === imported.id), 'Imported dataset survives restart independently of source selection')
  await stop()
  assert.deepEqual(errors, [])
  await writeFile(join(output, 'result.json'), JSON.stringify({ initial, rescanned, reopened, probe, imported: imported.id, job: job.id, errors }, null, 2))
  console.log('TDX frozen smoke passed: auto discovery, UI selection and scan, read-only probe, import, real background universe job, desktop/mobile layout, restart without saved selection, owned exit. Evidence:', output)
} catch (error) {
  console.error('TDX frozen evidence:', output, logs)
  throw error
} finally {
  await browser?.close()
  if (child?.exitCode === null && child.pid) {
    await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, resolve))
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 10000))])
  }
}
