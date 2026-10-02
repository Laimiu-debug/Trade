import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-review-drafts-ui-'))
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
  const browserContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await browserContext.newPage()
  page.on('dialog', dialog => dialog.accept())
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

  const account=await api('/accounts','POST',{name:'草稿恢复专用账户'}),other=await api('/accounts','POST',{name:'另一个账户'})
  const day='2026-01-02',dailyPath='/accounts/'+account.id+'/daily-reviews/'+day
  const blank={expected_revision:0,title:'',overall_summary:'正式基线',market_observation:'',decision_review:'',reflection:'',mistakes:'',tomorrow_plan:'',tags:[],next_market_forecast:'',next_watchlist:[],next_position_plan:'',next_risk_plan:'',next_position_rehearsal:[],next_target_date:null}
  await api(dailyPath,'PUT',blank)
  await api('/accounts/'+account.id+'/trades','POST',{trade_date:day,symbol:'600000',name:'测试证券',side:'buy',quantity:100,price:'10',fee:'0'})
  await expect.poll(async()=>(await api('/accounts/'+account.id+'/analytics')).status).toBe('fresh')
  const route=base+'/api/v1'+dailyPath
  const blockWrites=async r=>r.request().method()==='PUT'?r.abort('failed'):r.continue()
  await page.route(route,blockWrites)
  await page.goto(`${base}/rebuild.html?page=review&account=${account.id}`)
  await page.getByLabel('复盘日期').fill(day)
  await expect(page.getByLabel('当日总述')).toHaveValue('正式基线')
  await page.getByLabel('当日总述').fill('快速切页之前的本机长文')
  await page.getByRole('button',{name:'周期复盘',exact:true}).click()
  await page.getByRole('button',{name:'每日复盘',exact:true}).click()
  await page.getByLabel('复盘日期').fill(day)
  await expect(page.getByLabel('当日总述')).toHaveValue('快速切页之前的本机长文')
  // Only the review HTTP endpoint is unavailable; the shell and unrelated local account endpoints still load.
  await page.unroute(route,blockWrites)
  const unavailable=async r=>r.abort('failed')
  await page.route(route,unavailable)
  await page.getByRole('button',{name:'周期复盘',exact:true}).click()
  await page.getByRole('button',{name:'每日复盘',exact:true}).click()
  await page.getByLabel('复盘日期').fill(day)
  await expect(page.getByLabel('当日总述')).toHaveValue('快速切页之前的本机长文')
  await expect(page.getByRole('button',{name:'立即保存',exact:true})).toBeDisabled()
  await page.getByLabel('当日总述').fill('网络恢复后仍需保存的长文')
  await page.unroute(route,unavailable);await page.route(route,blockWrites)
  await page.getByRole('button',{name:'重新读取正式日复盘',exact:true}).click()
  await expect(page.getByRole('button',{name:'立即保存',exact:true})).toBeEnabled()
  const context=page.context(),tabs=[]
  for(let i=0;i<2;i++){
    const tab=await context.newPage();tabs.push(tab);tab.on('dialog',d=>d.accept());tab.on('pageerror',e=>errors.push(e.message))
    await tab.route(route,blockWrites)
    await tab.goto(`${base}/rebuild.html?page=review&account=${account.id}`)
    await tab.getByLabel('复盘日期').fill(day)
    await expect(tab.getByLabel('当日总述')).toHaveValue('网络恢复后仍需保存的长文')
  }
  await page.getByLabel('当日总述').fill('第一标签新草稿')
  for(const tab of tabs)await expect(tab.getByRole('button',{name:'采用另一页面日草稿',exact:true})).toBeVisible()
  await tabs[0].getByLabel('当日总述').fill('第二标签独立修改')
  await tabs[0].getByRole('button',{name:'保留本页日草稿',exact:true}).click()
  await expect(page.getByRole('button',{name:'采用另一页面日草稿',exact:true})).toBeVisible()
  await tabs[1].getByRole('button',{name:'采用另一页面日草稿',exact:true}).click()
  await expect(tabs[1].getByLabel('当日总述')).toHaveValue('第二标签独立修改')
  await expect(page.getByLabel('当日总述')).toHaveValue('第一标签新草稿')
  for(const tab of tabs)await tab.close()
  await page.getByRole('button',{name:'保留本页日草稿',exact:true}).click()
  await page.unroute(route,blockWrites)
  await page.getByRole('button',{name:'立即保存',exact:true}).click()
  await expect.poll(async()=>(await api(dailyPath)).overall_summary).toBe('第一标签新草稿')
  expect(await api('/accounts/'+other.id+'/daily-reviews/'+day)).toBeNull()
  const scoring=page.locator('section.card').filter({has:page.getByRole('heading',{name:'人工复盘评分',exact:true})})
  await scoring.getByLabel('计划执行').selectOption('0')
  await scoring.getByLabel('整体点评').fill('评分解释切页保留')
  await page.getByRole('button',{name:'周期复盘',exact:true}).click()
  await page.getByRole('button',{name:'每日复盘',exact:true}).click()
  await page.getByLabel('复盘日期').fill(day)
  await expect(scoring.getByLabel('计划执行')).toHaveValue('0')
  await expect(scoring.getByLabel('整体点评')).toHaveValue('评分解释切页保留')
  await scoring.getByRole('button',{name:'保存人工评分',exact:true}).click()
  await expect(scoring.getByText('人工最终评分已保存',{exact:true})).toBeVisible()
  const finalScores=await api(dailyPath+'/scores')
  expect(finalScores[0].scores.discipline.final).toBe(0)
  expect(finalScores[0].comment).toBe('评分解释切页保留')

  await page.getByRole('button',{name:'周期复盘',exact:true}).click()
  await page.getByLabel('选择日期').fill(day)
  await page.getByLabel('核心目标').fill('离线周长文')
  await page.getByRole('button',{name:'每日复盘',exact:true}).click()
  const periodRoute=base+'/api/v1/accounts/'+account.id+'/period-reviews/weekly/2026-W01'
  await page.route(periodRoute,unavailable)
  await page.getByRole('button',{name:'周期复盘',exact:true}).click()
  await page.getByLabel('选择日期').fill(day)
  await expect(page.getByLabel('核心目标')).toHaveValue('离线周长文')
  await expect(page.getByRole('button',{name:'保存周期复盘',exact:true})).toBeDisabled()
  await page.unroute(periodRoute,unavailable)
  await page.getByRole('button',{name:'重新读取周期正文',exact:true}).click()
  await page.getByRole('button',{name:'保存周期复盘',exact:true}).click()
  await expect(page.getByText('周期复盘已保存',{exact:true})).toBeVisible()
  await page.getByLabel('复盘周期').selectOption('monthly')
  await page.getByLabel('月度总结').fill('月度保留草稿')
  await page.getByRole('button',{name:'总览',exact:true}).click()
  const rounds=page.locator('section.card').filter({has:page.getByRole('heading',{name:'交易回合',exact:true})})
  await rounds.getByRole('button',{name:/600000/}).click()
  await rounds.getByLabel('人工回合摘要').fill('回合切页长文')
  await page.getByRole('button',{name:'周期复盘',exact:true}).click()
  await page.getByLabel('选择日期').fill(day)
  await page.getByLabel('复盘周期').selectOption('monthly')
  await expect(page.getByLabel('月度总结')).toHaveValue('月度保留草稿')
  await page.getByRole('button',{name:'总览',exact:true}).click()
  await rounds.getByRole('button',{name:/600000/}).click()
  await expect(rounds.getByLabel('人工回合摘要')).toHaveValue('回合切页长文')
  await rounds.getByRole('button',{name:'保存回合摘要',exact:true}).click()
  await expect(rounds.getByText('回合摘要已保存',{exact:true})).toBeVisible()
  await page.setViewportSize({width:320,height:900})
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true)
  expect(errors).toEqual([]);expect(external).toEqual([])
  console.log(JSON.stringify({ok:true,dataDir,checks:['daily_immediate_navigation','review_endpoint_offline_recovery','three_tab_conflicts','explicit_resolution','account_isolation','manual_score_zero_and_comment_navigation','weekly_offline','monthly_navigation','round_navigation','320px']}))
} catch(error) { console.error(logs); for(const context of browser?.contexts()||[])for(const page of context.pages())console.error(await page.locator('body').innerText()); throw error
} finally {
  await browser?.close()
  server.kill()
  await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); setTimeout(resolve, 5000).unref() })
}
