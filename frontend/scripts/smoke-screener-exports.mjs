import { chromium, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

if (!process.env.TRADE_SMOKE_URL) throw new Error('Set TRADE_SMOKE_URL to a server you started with an isolated temporary data directory; never use your daily application for smoke fixtures.')
const base = process.env.TRADE_SMOKE_URL
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1550, height: 1080 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  const token = (await (await page.request.get(base + '/api/v1/session')).json()).data.csrf_token
  const prefix = `screen-export-${Date.now()}`; let serial = 0
  async function post(path, body) { const response = await page.request.post(base + '/api/v1' + path, { data: body, headers: { 'X-CSRF-Token': token, 'Idempotency-Key': `${prefix}-${++serial}` } }); if (!response.ok()) throw new Error(await response.text()); return (await response.json()).data }
  const dates = []; const current = new Date(Date.UTC(2020, 0, 1))
  while (dates.length < 940) { if (current.getUTCDay() !== 0 && current.getUTCDay() !== 6) dates.push(current.toISOString().slice(0,10)); current.setUTCDate(current.getUTCDate()+1) }
  const tail = [16.17, 15.51, 15.94, 15.64, 16.28, 15.74, 15.53, 15.52, 15.91]
  const datasets = []
  for (const symbol of ['sh600091','sh600092']) {
    const bars = dates.map((day, index) => { const close = index >= 931 ? tail[index-931] : Math.round((10+index*.005+Math.max(0,index-700)**2*.00002)*100)/100; return { event_date:day, open:String(close),high:(close+.12).toFixed(2),low:(close-.12).toFixed(2),close:String(close),volume:index===939?300:1000,amount:'1000000000',available_at:day+'T07:00:00+00:00' } })
    datasets.push(await post('/market/datasets',{symbol,bars}))
  }
  const asOf = dates.at(-1)
  const funnel = await post('/research/screener-runs',{datasets:datasets.map(row=>({dataset_id:row.id,float_shares:10000,float_shares_as_of_date:'2019-01-01'})),as_of_date:asOf,return_window_days:40,
    config:{mode:'strict',step1:{turnover_threshold:.01,amount_threshold:50000000,amplitude_threshold:.01}}})
  const b1 = await post('/research/b1-runs',{dataset_ids:datasets.map(row=>row.id),as_of_date:asOf})
  if (funnel.summary.step1 !== 2 || b1.hit_count !== 2) throw new Error('Actual source fixture did not pass expected frozen stages')
  await page.goto(base+'/rebuild.html'); await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading',{name:'创建第一个账户'}).count()) { await page.getByRole('textbox',{name:'账户名称'}).fill('筛选导出验收'); await page.getByRole('button',{name:'创建实盘账户'}).click() }
  await page.waitForFunction(()=>Boolean(document.querySelector('.account-select select')?.value))
  await page.evaluate(({funnelId,b1Id})=>{ localStorage.setItem('trade-rebuild.screener-last-run.v1',JSON.stringify(funnelId));localStorage.setItem('trade-rebuild.b1-last-run.v1',JSON.stringify(b1Id));localStorage.setItem('trade-rebuild.research-tab.v1',JSON.stringify('funnel')) },{funnelId:funnel.id,b1Id:b1.id})
  await page.reload(); await page.getByRole('button',{name:'策略研究',exact:true}).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '选股筛选', exact: true }).click()
  await page.getByRole('tab', { name: '四步漏斗', exact: true }).click()
  await page.getByRole('heading',{name:`筛选结果 · ${funnel.id.slice(0,16)}`,exact:true}).waitFor()
  await page.getByRole('button',{name:'流动性 2',exact:true}).click()
  async function verify(panel, kind) {
    const frozen = kind === 'funnel' ? funnel.result.pools.step1 : b1.result.hits
    const included = frozen.find(row => row.dataset_id === datasets[1].id).symbol
    const excluded = frozen.find(row => row.dataset_id === datasets[0].id).symbol
    await panel.getByLabel('手动选择导出证券',{exact:true}).check()
    await panel.getByLabel(`导出 ${included}`,{exact:true}).check()
    const hashes = new Set()
    for (const [label,extension] of [['CSV','csv'],['Excel','xlsx'],['PDF','pdf']]) {
      const waiting = page.waitForResponse(reply=>reply.url().includes(`/exports/${extension}`)&&reply.request().method()==='POST')
      const download = page.waitForEvent('download')
      await panel.getByRole('button',{name:`导出 ${label}`,exact:true}).click()
      const response = await waiting; if (!response.ok()) throw new Error(await response.text())
      expect(response.headers()['x-export-row-count']).toBe('1'); hashes.add(response.headers()['x-export-selection-sha256'])
      const file = await download, bytes=await readFile(await file.path())
      expect(file.suggestedFilename()).toContain(kind)
      if (extension==='csv') {const text=bytes.toString('utf8');expect(text).toContain(included);expect(text).not.toContain(excluded);expect(text).toContain('source_json');expect(text).toContain(kind==='funnel'?'step1':'hits')}
      if (extension==='xlsx') expect(bytes.subarray(0,2).toString()).toBe('PK')
      if (extension==='pdf') expect(bytes.subarray(0,5).toString()).toBe('%PDF-')
    }
    expect(hashes.size).toBe(1)
  }
  await verify(page.getByRole('region',{name:'漏斗结果导出',exact:true}),'funnel')
  await page.getByRole('tab', { name: 'B1 多周期', exact: true }).click()
  await verify(page.getByRole('region',{name:'B1结果导出',exact:true}),'b1')
  await page.getByRole('tab', { name: '四步漏斗', exact: true }).click()
  await page.getByRole('button',{name:'输入池 2',exact:true}).click()
  await expect(page.getByRole('region',{name:'漏斗结果导出',exact:true}).getByLabel('手动选择导出证券',{exact:true})).not.toBeChecked()
  if(errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: actual two-stock 940bar funnel+B1 frozen runs -> current stage explicit one-stock subset -> CSV/XLSX/PDF same selection SHA -> stage change resets selection')
} finally { await browser.close() }
