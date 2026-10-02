// Only isolated local records. Real workers and downloads; never opens source HTML.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'
import XLSX from 'xlsx'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))
const legacyBackend = fileURLToPath(new URL('../../../final-trade/backend', import.meta.url))
const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-table-exports-ui-'))
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const fixture = String.raw`
import json, os
from pathlib import Path
from test_wyckoff_research_api import data, write, dataset
from fastapi.testclient import TestClient
from trade_app.main import create_app
from test_legacy_reports import original_package
from test_plateau import create_experiment
from test_portfolio import create_run
from test_strategy_scans import _bars
from trade_app.research import backtest_service, plateau_service, portfolio_service, signal_workspace_service as signals
from trade_app.research.scan_service import create_scan
from trade_app.research.legacy_report_domain import prepare_import
from trade_app.research.legacy_report_service import save_report
target=Path(os.environ['TRADE_REBUILD_DATA_DIR'])
with TestClient(create_app(target, auto_rebuild=False)) as client:
    client.headers['X-CSRF-Token'] = client.get('/api/v1/session').json()['data']['csrf_token']
    account=data(write(client,'/accounts',{'name':'研究导出隔离验证'}))
    ds=dataset(client,_bars(True))
    native=data(write(client,'/backtests',{'dataset_id':ds['id'],'strategy_id':'relative_strength_breakout_v1','params':{'min_ret40':'.01','min_vol_slope20':'0'},'holding_bars':3,'strict':True}))
    factory=client.app.state.db_factory
    assert backtest_service.process_one_backtest(factory,target)
    experiment,_,_=create_experiment(client,target,count=2)
    while plateau_service.process_one_plateau(factory,target): pass
    portfolio,_=create_run(client,target)
    while portfolio_service.process_one_portfolio(factory,target): pass
    prepared=prepare_import(original_package.__wrapped__()[0],'original.ftbt')
    with factory.begin() as session:
        old=save_report(session,prepared,prepared['preview']['preview_sha256'],'legacy_readonly',True)
        scan=create_scan(session,target,{'dataset_ids':[ds['id']],'strategies':[{'strategy_id':'wulong_cluster_v1','params':{}},{'strategy_id':'relative_strength_breakout_v1','params':{}}],'date_from':'2025-03-28','date_to':'2025-03-29','strict':True})
    with factory() as session:
        prepared=signals.prepare_report(session,target,{'scan_id':scan['id'],'as_of_date':'2025-03-29','min_overlap':2})
    with factory.begin() as session: report=signals.save_report(session,prepared)
    with factory() as session:
        native=backtest_service.get_backtest(session,native['id'])
        portfolio=portfolio_service.get_portfolio(session,portfolio['id'],full=True)
    assert native['result']['trades'] and portfolio['result']['trades']
    target.joinpath('fixtures.json').write_text(json.dumps({'account':account['id'],'backtest':native['id'],'backtest_count':len(native['result']['trades']),'plateau':experiment['id'],'portfolio':portfolio['id'],'portfolio_count':len(portfolio['result']['trades']),'legacy':old['id'],'signals':report['id'],'symbols':len(report['result']['per_symbol'])}),encoding='utf8')
import uvicorn
uvicorn.run('trade_app.main:app',host='127.0.0.1',port=int(os.environ['TRADE_REBUILD_PORT']))
`
const server = spawn(process.env.PYTHON || 'python', ['-c', fixture], { cwd: backend, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, TRADE_REBUILD_DATA_DIR: dataDir, TRADE_REBUILD_PORT: String(port), PYTHONIOENCODING: 'utf-8',
    PYTHONPATH: [backend, path.join(backend, 'tests'), legacyBackend].join(path.delimiter) } })
let logs = '', browser
for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { logs = (logs + data.toString()).slice(-18000) })
try {
  const base = `http://127.0.0.1:${port}`
  for (let index = 0; index < 250; index++) {
    try { if ((await fetch(base + '/health')).ok) break } catch { /* bounded fixture startup */ }
    if (server.exitCode !== null || index === 249) throw new Error(logs)
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  const ids = JSON.parse(await readFile(path.join(dataDir, 'fixtures.json'), 'utf8'))
  browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 }, timezoneId: 'Asia/Shanghai' })
  const errors = [], researchWrites = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (request.method() !== 'GET' && /\/(research|backtests)\//.test(request.url())) researchWrites.push(request.url()) })
  async function download(link) {
    const event = page.waitForEvent('download'); await link.click(); const item = await event
    assert.equal(await item.failure(), null)
    const stream = await item.createReadStream(), chunks = []
    for await (const chunk of stream) chunks.push(chunk)
    const bytes = Buffer.concat(chunks)
    assert.ok(bytes.length > 0)
    return bytes
  }
  const csvRows = bytes => XLSX.utils.sheet_to_json(XLSX.read(bytes.toString('utf8'), { type: 'string', raw: true }).Sheets.Sheet1)
  await page.goto(base + `/rebuild.html?page=backtest&account=${ids.account}&task=backtest:${ids.backtest}`)
  const nativeCsv = await download(page.locator(`a[href="/api/v1/backtests/${ids.backtest}/trades.csv"]`))
  assert.equal(csvRows(nativeCsv).length, ids.backtest_count)
  const nativeHtml = await download(page.locator(`a[href="/api/v1/backtests/${ids.backtest}/trades.html"]`))
  assert.ok(nativeHtml.toString('utf8').includes('executed_fill_in_source_order'))
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '参数实验', exact: true }).click()
  await page.getByRole('link', { name: '单股收益平原', exact: true }).click()
  await page.locator('tr').filter({ hasText: '检查点实验' }).getByRole('button', { name: '查看实验' }).click()
  const points = await download(page.getByRole('link', { name: '导出平原全点 Excel', exact: true }))
  const workbook = XLSX.read(points, { type: 'buffer' })
  assert.equal(XLSX.utils.sheet_to_json(workbook.Sheets.PlateauPoints).length, 2)
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '组合回测', exact: true }).click()
  await page.getByRole('tab', { name: /^任务历史/ }).click()
  await page.locator('tr').filter({ hasText: '真实组合验收' }).getByRole('button', { name: '查看组合' }).click()
  const portfolioCsv = await download(page.locator(`a[href="/api/v1/research/portfolios/${ids.portfolio}/trades.csv"]`))
  assert.equal(csvRows(portfolioCsv).length, ids.portfolio_count)
  await download(page.locator(`a[href="/api/v1/research/portfolios/${ids.portfolio}/trades.html"]`))
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '报告库', exact: true }).click()
  await page.getByRole('link', { name: '旧版报告', exact: true }).click()
  const legacy = page.getByRole('region', { name: '旧报告与平原档案', exact: true })
  await legacy.getByRole('button', { name: '查看旧档案' }).click()
  const oldCsv = await download(legacy.getByRole('link', { name: '导出原成交 CSV', exact: true }))
  assert.ok(oldCsv.toString('utf8').includes('信号日期,入场日期,离场日期'))
  await download(legacy.getByRole('link', { name: '导出原成交 HTML', exact: true }))
  const oldXlsx = XLSX.read(await download(legacy.getByRole('link', { name: '导出原平原全点 Excel' })), { type: 'buffer' })
  const oldPoints = XLSX.utils.sheet_to_json(oldXlsx.Sheets.PlateauPoints)
  assert.equal(oldPoints.length, 2); assert.equal(oldPoints[1].status, 'failed')
  await legacy.getByRole('button', { name: '查看旧参数点 1' }).click()
  await download(legacy.getByRole('region', { name: '旧参数点详情' }).getByRole('link', { name: '导出原成交 CSV' }))
  await page.goto(base + `/rebuild.html?page=signals&account=${ids.account}`)
  await page.getByRole('button', { name: '查看信号报告', exact: true }).click()
  await expect(page.getByText('· 使用这份报告保存时的过滤条件；表单未保存的改动不影响导出。', { exact: true })).toBeVisible()
  const frozenCsv = await download(page.getByRole('link', { name: '导出交叉验证 CSV' }))
  const crossing = csvRows(frozenCsv)
  assert.equal(crossing.length, ids.symbols)
  assert.equal(crossing[0]['报告ID'], ids.signals)
  assert.equal(crossing[0]['扫描结束日'], '2025-03-29')
  await page.locator('label.field').filter({ has: page.locator('span').filter({ hasText: /^交易所$/ }) }).locator('select').selectOption('bj')
  assert.deepEqual(await download(page.getByRole('link', { name: '导出交叉验证 CSV' })), frozenCsv)
  assert.deepEqual(researchWrites, []); assert.deepEqual(errors, [])
  console.log(`research table browser downloads passed: single + portfolio CSV/HTML, all-point native + legacy Excel, original trade/point CSV/HTML, frozen filtered cross-validation CSV; ${dataDir}`)
} finally {
  await browser?.close()
  if (server.exitCode === null) { const ended = new Promise(resolve => server.once('exit', resolve)); server.kill(); await ended }
}
