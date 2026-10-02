import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'
import { copyFile, mkdir, mkdtemp, readdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { spawn, execFile } from 'node:child_process'
import net from 'node:net'

if (!process.env.TRADE_BUNDLE_EXE) throw new Error('Set TRADE_BUNDLE_EXE to the newly built executable')
let executable = resolve(process.env.TRADE_BUNDLE_EXE)
const output = await mkdtemp(join(tmpdir(), 'trade-frozen-smoke-'))
const singleFile = process.env.TRADE_BUNDLE_SINGLE_FILE === '1'
const runtime = join(output, 'runtime')
await mkdir(runtime)
const childEnvironment = { ...process.env, TEMP: runtime, TMP: runtime, TMPDIR: runtime, PYINSTALLER_STRICT_UNPACK_MODE: '1' }
if (singleFile) {
  const portable = join(output, '单文件 独立启动')
  await mkdir(portable)
  const filename = process.platform === 'win32' ? 'TradeRebuild.exe' : 'TradeRebuild'
  await copyFile(executable, join(portable, filename))
  assert.deepEqual(await readdir(portable), [filename])
  executable = join(portable, filename)
}
const source = join(output, 'source#history%25'), target = join(output, '恢复 数据#history%25'), pointer = join(output, 'launcher.json')
const binding = net.createServer(); await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port; await new Promise(resolve => binding.close(resolve))
const base = `http://127.0.0.1:${port}`
let child = spawn(executable, ['--data-dir', source, '--state-file', pointer, '--port', String(port), '--no-browser', '--no-tray'], { cwd: dirname(executable), windowsHide: true, env: childEnvironment, stdio: ['ignore', 'pipe', 'pipe'] })
let logs = ''; child.stdout.on('data', value => { logs += value }); child.stderr.on('data', value => { logs += value })
let exited = new Promise(resolve => { child.once('exit', resolve); child.once('error', error => resolve(String(error))) })
let browser
try {
  browser = await chromium.launch({ headless: true, ...(process.env.TRADE_BROWSER_CHANNEL ? { channel: process.env.TRADE_BROWSER_CHANNEL } : process.platform === 'win32' ? { channel: 'msedge' } : {}) })
  await expect.poll(async () => { try { return (await fetch(base + '/health')).ok } catch { return false } }, { timeout: 45000 }).toBe(true)
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  async function get(path) { const response = await page.request.get(base + '/api/v1' + path); assert(response.ok(), await response.text()); return (await response.json()).data }
  async function write(path, body, method = 'POST') {
    const response = await page.request.fetch(base + '/api/v1' + path, { method, data: body, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': crypto.randomUUID() } })
    assert(response.ok(), await response.text()); return (await response.json()).data
  }
  assert.equal((await get('/system/storage')).data_dir, source)
  assert.equal((await readdir(output)).includes('source'), false, 'SQLite URI must not create a file before the # fragment')
  const account = await write('/accounts', { name: '打包版隔离验收' })
  await write(`/accounts/${account.id}/cash-flows`, { flow_date: '2025-01-01', kind: 'initial', amount: '10000' })
  for (const [snap_date, total_assets] of [['2025-01-01', '10000'], ['2025-01-02', '11000']]) {
    await write(`/accounts/${account.id}/snapshots/${snap_date}`, { snap_date, total_assets, expected_revision: 0 }, 'PUT')
  }
  const day = new Date('2024-01-01T00:00:00Z')
  const bars = Array.from({ length: 96 }, (_, index) => {
    while ([0, 6].includes(day.getUTCDay())) day.setUTCDate(day.getUTCDate() + 1)
    const date = day.toISOString().slice(0, 10); day.setUTCDate(day.getUTCDate() + 1)
    const price = 10 + index / 10
    return { event_date: date, open: price.toFixed(2), close: (price + .04).toFixed(2), high: (price + .1).toFixed(2), low: (price - .1).toFixed(2), volume: 100000 + index * 2000, amount: '10000000', available_at: date + 'T08:00:00+00:00' }
  })
  const sample = await write('/market/datasets', { symbol: '600000', bars })
  const strategyCatalog = await get('/research/strategies')
  const classicIds = ['classic_donchian_breakout_v1', 'classic_sma_trend_v1', 'classic_bollinger_reentry_v1']
  assert.equal(strategyCatalog.length, 19)
  assert.equal(new Set(strategyCatalog.map(row => row.id)).size, 19)
  assert.deepEqual(strategyCatalog.filter(row => row.origin === 'classic_reference').map(row => row.id).sort(), [...classicIds].sort())
  for (const id of classicIds) {
    const row = strategyCatalog.find(item => item.id === id)
    assert.equal(row.enabled_in_rebuild, true)
    assert.equal(row.capabilities.supports_exit_signal, true)
    assert.notEqual(row.signal_params, null)
  }
  // Exercise on-disk source hashing inside the unpacked executable as well as
  // real persistence. Short history is allowed to produce explicit exclusions.
  const screenerBody = { datasets: [{ dataset_id: sample.id }], as_of_date: bars.at(-1).event_date,
    return_window_days: 40, config: {} }
  const screenerRun = await write('/research/screener-runs', screenerBody)
  assert.match(screenerRun.id, /^[a-f0-9]{64}$/)
  assert.match(screenerRun.code_sha256, /^[a-f0-9]{64}$/)
  assert.equal(screenerRun.result.as_of_date, bars.at(-1).event_date)
  assert.equal(screenerRun.result.summary.input + screenerRun.result.excluded.length, 1)
  assert.equal(screenerRun.request.datasets[0].dataset_id, sample.id)
  assert.equal((await write('/research/screener-runs', screenerBody)).id, screenerRun.id)
  assert.deepEqual((await get('/research/screener-runs/' + screenerRun.id)).result, screenerRun.result)
  const b1Body = { dataset_ids: [sample.id], as_of_date: bars.at(-1).event_date }
  const b1Run = await write('/research/b1-runs', b1Body)
  // B1's public DTO exposes its content-derived run ID, not the private code hash.
  assert.match(b1Run.id, /^[a-f0-9]{64}$/)
  assert.equal(b1Run.result.total_scanned, 1)
  assert.deepEqual(b1Run.request.dataset_ids, [sample.id])
  assert.equal(b1Run.result.excluded.length, 1)
  assert.equal(b1Run.result.excluded[0].dataset_id, sample.id)
  assert.equal(b1Run.result.excluded[0].reason, 'INSUFFICIENT_BARS_AS_OF_DATE')
  assert.equal((await write('/research/b1-runs', b1Body)).id, b1Run.id)
  assert.deepEqual((await get('/research/b1-runs/' + b1Run.id)).result, b1Run.result)
  await writeFile(join(output, 'screener-b1.json'), JSON.stringify({ dataset_id: sample.id,
    screener: { id: screenerRun.id, code_sha256: screenerRun.code_sha256, summary: screenerRun.result.summary, excluded: screenerRun.result.excluded },
    b1: { id: b1Run.id, total_scanned: b1Run.result.total_scanned, excluded: b1Run.result.excluded } }, null, 2))
  async function cliText(args) {
    return new Promise((resolve, reject) => execFile(executable, args,
      { cwd: dirname(executable), windowsHide: true, timeout: 60000, encoding: 'utf8', env: childEnvironment },
      (error, stdout, stderr) => error ? reject(new Error(`${error.message}: ${stderr}`)) : resolve(stdout)))
  }
  async function cliJson(args) { return JSON.parse(await cliText(args)) }
  const presets = await cliJson(['lab', 'presets'])
  assert.equal(presets.presets.length, 4)
  assert(presets.presets[0].differences.some(value => /[\u4e00-\u9fff]/.test(value)))
  const labInput = join(output, '实验输入.json'), labOutput = join(output, '实验结果')
  await writeFile(labInput, JSON.stringify({ format: 'trade-lab-input-v1', strategy_id: 'hybrid_band_v1', datasets: [{ symbol: '600000.SH', bars }] }))
  const lab = await cliJson(['lab', 'scan', '--input', labInput, '--output', labOutput])
  assert.equal(lab.state, 'succeeded')
  assert.equal(lab.output, labOutput)
  assert(JSON.parse(await readFile(join(labOutput, 'result.json'), 'utf8')).code_sha256)
  assert.match(await cliText(['sync', '--help']), /本机行情同步CLI/)
  const run = await write('/backtests', { dataset_id: sample.id, strategy_id: 'relative_strength_breakout_v1', params: { min_ret40: '.01', min_vol_slope20: '0' }, advanced_analysis: true, holding_bars: 3 })
  await expect.poll(async () => (await get('/backtests/' + run.id)).state, { timeout: 45000 }).toBe('succeeded')
  const result = await get('/backtests/' + run.id)
  assert(result.result.trade_count > 0); assert(result.result_sha256)
  // The short SMA period uses this existing 96-bar fixture. It verifies that the
  // new bundled calculation module actually loads in an isolated worker; no
  // assumed signal score or strategy-exit outcome is imposed on the rising series.
  const classicParams = { period: '10', buffer_pct: '0' }
  const classicRun = await write('/backtests', { dataset_id: sample.id, strategy_id: 'classic_sma_trend_v1',
    params: classicParams, strict: true, holding_bars: 3 })
  assert.deepEqual(classicRun.params, classicParams)
  await expect.poll(async () => (await get('/backtests/' + classicRun.id)).state, { timeout: 45000 }).toBe('succeeded')
  const classicResult = await get('/backtests/' + classicRun.id)
  assert.equal(classicResult.strategy_id, 'classic_sma_trend_v1')
  assert.equal(classicResult.result.strategy_id, classicResult.strategy_id)
  assert.deepEqual(classicResult.params, classicParams)
  assert.equal(classicResult.code_sha256, classicRun.code_sha256)
  assert.equal(classicResult.result_sha256.length, 64)
  assert(classicResult.result.trade_count > 0)
  assert(classicResult.result.trades.some(trade => trade.side === 'buy'))
  assert(classicResult.result.trades.some(trade => trade.side === 'sell'))
  await writeFile(join(output, 'classic-worker.json'), JSON.stringify({ catalog_ids: strategyCatalog.map(row => row.id),
    classic_ids: classicIds, run_id: classicResult.id, params: classicResult.params, code_sha256: classicResult.code_sha256,
    result_sha256: classicResult.result_sha256, trade_count: classicResult.result.trade_count }, null, 2))
  const portfolioBody = { dataset_ids: [sample.id], mode: 'traditional_runtime14', strategy_id: 'relative_strength_breakout_v1', params: { min_ret40: '.01', min_vol_slope20: '0' }, start_date: bars[50].event_date, end_date: bars.at(-1).event_date, config: { max_holding_bars: 3 } }
  const preview = await write('/research/portfolios/preview', portfolioBody)
  const portfolio = await write('/research/portfolios', { ...portfolioBody, name: '打包组合子进程验收', expected_preview_sha256: preview.preview_sha256 })
  await expect.poll(async () => (await get('/research/portfolios/' + portfolio.id)).state, { timeout: 60000 }).toBe('succeeded')
  const analysis = await write('/research/portfolio-analyses', { source_run_id: portfolio.id, name: '打包分析子进程', iterations: 100, block_size: 3 })
  await expect.poll(async () => (await get('/research/portfolio-analyses/' + analysis.id)).state, { timeout: 60000 }).toBe('succeeded')
  assert((await get('/research/portfolio-analyses/' + analysis.id)).result_sha256)
  await write(`/accounts/${account.id}/daily-reviews/2025-01-02`, { expected_revision: 0, title: '中文离线复盘', reflection: '核对打包版本的中文字体和复盘导出。', next_target_date: null }, 'PUT')
  const pdf = await page.request.get(base + `/api/v1/accounts/${account.id}/exports/review/daily/2025-01-02.pdf`)
  assert(pdf.ok(), await pdf.text()); const pdfBytes = await pdf.body()
  assert(pdfBytes.subarray(0, 5).equals(Buffer.from('%PDF-'))); assert(pdfBytes.includes(Buffer.from('/FontFile2')))
  await writeFile(join(output, 'review.pdf'), pdfBytes)
  await expect.poll(async () => (await get(`/accounts/${account.id}/analytics`)).status, { timeout: 45000 }).toBe('fresh')
  const statistics = await page.request.get(base + `/api/v1/accounts/${account.id}/exports/performance.pdf?kind=daily`)
  assert(statistics.ok()); assert((await statistics.body()).includes(Buffer.from('/FontFile2')))
  await writeFile(join(output, 'statistics.pdf'), await statistics.body())
  const cliOutput = join(output, '独立复盘导出.pdf')
  const cli = await cliJson(['export', '--url', base, 'review', '--account', account.id, '--kind', 'daily', '--key', '2025-01-02', '--output', cliOutput])
  assert.equal(cli.state, 'exported'); assert((await readFile(cliOutput)).includes(Buffer.from('/FontFile2')))
  const archive = await page.request.get(base + '/api/v1/backups/export'); assert(archive.ok())
  await writeFile(join(output, 'backup.zip'), await archive.body())
  const stocks = await page.request.get(base + '/data/stock-database.slim.json'); assert(stocks.ok()); assert((await stocks.json()).length > 1000)
  await page.goto(`${base}/rebuild.html?page=research&research=catalog&account=${account.id}`)
  await expect(page.getByRole('heading', { name: '策略目录', exact: true })).toBeVisible()
  await expect(page.locator('.research-strategy-row')).toHaveCount(19)
  for (const id of classicIds) await expect(page.locator('.research-strategy-row').filter({ hasText: id })).toBeVisible()
  await page.locator('.research-strategy-row').filter({ hasText: 'classic_sma_trend_v1' })
    .getByRole('button', { name: '打开单股研究', exact: true }).click()
  await expect(page.getByRole('heading', { name: '单股研究', exact: true })).toBeVisible()
  await expect(page.getByLabel('策略', { exact: true })).toHaveValue('classic_sma_trend_v1')
  await expect(page.getByLabel('趋势均线周期', { exact: true })).toHaveValue('200')
  const researchNavigation = page.getByRole('navigation', { name: '研究子页面', exact: true })
  await researchNavigation.getByRole('link', { name: /^组合回测/ }).click()
  await expect(page.getByRole('heading', { name: '组合回测', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '固定样本组合回测', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '组合配置步骤', exact: true }).getByRole('button')).toHaveCount(5)
  await researchNavigation.getByRole('link', { name: /^策略目录/ }).click()
  await expect(page.getByRole('heading', { name: '策略目录', exact: true })).toBeVisible()
  await page.goto(`${base}/rebuild.html?page=settings&account=${account.id}`)
  await page.getByRole('tab', { name: '存储与备份', exact: true }).click()
  await page.getByLabel('复制到空目录', { exact: true }).fill(target)
  await page.getByRole('button', { name: '预览复制范围' }).click()
  await page.getByRole('button', { name: '确认复制到空目录' }).click()
  await page.getByRole('button', { name: '使用刚恢复或复制的目录' }).click()
  const lifecycle = page.getByRole('region', { name: '服务与目录切换' })
  await lifecycle.getByRole('button', { name: '校验切换目标' }).click()
  await lifecycle.getByRole('checkbox').check()
  await lifecycle.getByRole('button', { name: '确认切换并重启' }).click()
  await expect(lifecycle.getByText('切换完成：' + target, { exact: true })).toBeVisible({ timeout: 45000 })
  assert.equal(JSON.parse(await readFile(pointer, 'utf8')).active_data_dir, target)
  await lifecycle.getByRole('button', { name: '准备退出应用' }).click()
  await lifecycle.getByRole('button', { name: '确认退出应用' }).click()
  assert.equal(await Promise.race([exited, new Promise(resolve => setTimeout(() => resolve('timeout'), 20000))]), 0, logs)
  assert.deepEqual((await readdir(runtime)).filter(name => name.startsWith('_MEI')), [], 'One-file extraction directories must be released on normal exit')
  if (singleFile) {
    // A fresh extraction must reconnect to the persisted selected data directory.
    child = spawn(executable, ['--state-file', pointer, '--port', String(port), '--no-browser', '--no-tray'], { cwd: dirname(executable), windowsHide: true, env: childEnvironment, stdio: ['ignore', 'pipe', 'pipe'] })
    child.stdout.on('data', value => { logs += value }); child.stderr.on('data', value => { logs += value })
    exited = new Promise(resolve => { child.once('exit', resolve); child.once('error', error => resolve(String(error))) })
    await expect.poll(async () => { try { return (await fetch(base + '/health')).ok } catch { return false } }, { timeout: 45000 }).toBe(true)
    await page.reload()
    await page.getByRole('tab', { name: '存储与备份', exact: true }).click()
    assert.equal((await get(`/accounts/${account.id}/daily-reviews/2025-01-02`)).title, '中文离线复盘')
    assert.equal((await get('/backtests/' + run.id)).result_sha256, result.result_sha256)
    const restartedClassic = await get('/backtests/' + classicRun.id)
    assert.equal(restartedClassic.result_sha256, classicResult.result_sha256)
    assert.deepEqual(restartedClassic.params, classicParams)
    const restartedScreener = await get('/research/screener-runs/' + screenerRun.id)
    assert.equal(restartedScreener.code_sha256, screenerRun.code_sha256)
    assert.deepEqual(restartedScreener.result, screenerRun.result)
    assert.deepEqual((await get('/research/b1-runs/' + b1Run.id)).result, b1Run.result)
    assert.equal(JSON.parse(await readFile(pointer, 'utf8')).active_data_dir, target)
    await lifecycle.getByRole('button', { name: '准备退出应用' }).click()
    await lifecycle.getByRole('button', { name: '确认退出应用' }).click()
    assert.equal(await Promise.race([exited, new Promise(resolve => setTimeout(() => resolve('timeout'), 20000))]), 0, logs)
    assert.deepEqual((await readdir(runtime)).filter(name => name.startsWith('_MEI')), [])
  }
  assert.deepEqual(errors, [])
  console.log('Frozen executable smoke passed:', singleFile ? 'single copied executable / Chinese path / temporary extraction cleanup / fresh restart and persisted data;' : 'folder bundle;', 'served UI/resources, 19-strategy catalog with 3 classic variants, real classic worker/frozen parameters, frozen screener and B1 persistence/exclusions, research catalog/single/portfolio navigation, isolated backtest, portfolio and analysis workers, lab CLI/worker and sync CLI, Chinese CLI paths, embedded Chinese review/statistics chart PDF, backup, reviewed directory switch, reconnect and owned exit. Evidence:', output)
} catch (error) {
  console.error('Frozen executable evidence:', output, 'launcher output:', logs)
  try { console.error(await readFile(join(source, 'launch.log'), 'utf8')) } catch { /* Startup may have failed before logging. */ }
  throw error
} finally {
  await browser?.close()
  if (child.exitCode === null && child.pid) {
    // The PID belongs to the executable started above and has not exited.
    if (process.platform === 'win32') await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, resolve))
    else child.kill('SIGINT')
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 10000))])
  }
}
