import { spawn, execFileSync } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const backend=fileURLToPath(new URL('../../backend',import.meta.url))
const fixture=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',"import sys,json,io,base64;sys.path.insert(0,'tests');from test_legacy_sim_import import fixture;from test_legacy_import import sample;from PIL import Image;b=io.BytesIO();Image.new('RGB',(8,6),'blue').save(b,format='PNG');print(json.dumps({'sim':fixture(),'real':sample(),'image':base64.b64encode(b.getvalue()).decode()}))"],{cwd:backend,encoding:'utf8',windowsHide:true}))
const dataDir=await mkdtemp(path.join(os.tmpdir(),'trade-legacy-final-ui-'))
const binding=net.createServer();await new Promise(resolve=>binding.listen(0,'127.0.0.1',resolve));const port=binding.address().port;await new Promise(resolve=>binding.close(resolve))
const base=`http://127.0.0.1:${port}`
const server=spawn(process.env.PYTHON||'python',['-m','uvicorn','trade_app.main:app','--host','127.0.0.1','--port',String(port)],{cwd:backend,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,TRADE_REBUILD_DATA_DIR:dataDir,TRADE_REBUILD_PORT:String(port)}})
let browser,logs=''
for(const stream of [server.stdout,server.stderr])stream.on('data',chunk=>{logs=(logs+chunk).slice(-12000)})
try{
  for(let index=0;index<120;index++){try{if((await fetch(base+'/health')).ok)break}catch{}if(server.exitCode!==null||index===119)throw new Error(logs);await new Promise(resolve=>setTimeout(resolve,200))}
  browser=await chromium.launch({channel:'msedge',headless:true})
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],external=[]
  page.on('pageerror',error=>errors.push(error.message));page.on('request',req=>{if(!req.url().startsWith(base))external.push(req.url())})
  let csrf=(await(await page.request.get(base+'/api/v1/session')).json()).data.csrf_token
  page.on('response',async response=>{if(response.url().endsWith('/api/v1/session')&&response.ok())csrf=(await response.json()).data.csrf_token})
  const api=async(route,method='GET',data)=>{const response=await page.request.fetch(base+'/api/v1'+route,{method,data,headers:method==='GET'?{}:{'X-CSRF-Token':csrf,'Idempotency-Key':crypto.randomUUID()}});if(!response.ok())throw new Error(route+' '+response.status()+' '+await response.text());return(await response.json()).data}
  async function archive(filename,payload,mode='archive_only'){
    const body={filename,content_base64:Buffer.from(JSON.stringify(payload)).toString('base64'),mode,account_name:mode==='new_real_account'?'附件新实盘':''}
    const preview=await api('/legacy-imports/preview','POST',body)
    return await api('/legacy-imports','POST',{...body,expected_preview_sha256:preview.preview_sha256,acknowledge_limitations:true})
  }
  const independent=await api('/accounts','POST',{name:'原账户保持'})
  const sim=await archive('sim-complete.json',fixture.sim)
  const original=structuredClone(fixture.sim)
  const bad=structuredClone(fixture.sim);bad.account.cash='1.00';await archive('sim-invalid.json',bad)
  const real=await archive('real-attachments.json',fixture.real,'new_real_account')
  async function openArchive(filename){await page.getByRole('row').filter({hasText:filename}).getByRole('button',{name:'查看档案',exact:true}).click()}
  await page.goto(base+`/rebuild.html?page=settings&settings_group=legacy&account=${independent.id}`)
  await openArchive('sim-invalid.json')
  await page.getByRole('button',{name:'核验旧模拟账本并预览',exact:true}).click()
  await expect(page.getByText(/初始资金\+全部净成交金额与当前现金不一致/)).toBeVisible()
  await expect(page.getByRole('button',{name:'确认创建独立模拟账户',exact:true})).toBeDisabled()
  await openArchive('sim-complete.json')
  await page.getByRole('button',{name:'核验旧模拟账本并预览',exact:true}).click()
  await expect(page.getByText('现金 ¥ 9189.40 · 剩余成本 ¥ 1002.50',{exact:true})).toBeVisible()
  expect((await api('/accounts')).filter(item=>item.kind==='sim')).toHaveLength(0)
  await page.getByRole('checkbox',{name:/我已核对逐笔结果/}).check()
  await page.getByRole('button',{name:'确认创建独立模拟账户',exact:true}).click()
  await expect.poll(async()=>(await api('/legacy-imports/'+sim.id)).sim_promotion?.account_id).toBeTruthy()
  const id=(await api('/legacy-imports/'+sim.id)).sim_promotion.account_id
  await expect.poll(()=>new URL(page.url()).searchParams.get('account')).toBe(id)
  await page.goto(base+`/rebuild.html?page=simulation&simulation-view=settlement&account=${id}`)
  await expect(page.getByText('· 旧成交参考，原限价未知',{exact:true}).first()).toBeVisible()
  const portfolio=await api('/sim-accounts/'+id+'/portfolio');expect(portfolio.cash).toBe('9189.40');expect(portfolio.positions[0].cost_basis).toBe('1002.50')
  const order=await api('/sim-accounts/'+id+'/orders','POST',{symbol:portfolio.positions[0].symbol,side:'sell',quantity:100,limit_price:'11',signal_date:portfolio.as_of_date,submit_date:portfolio.as_of_date})
  await api('/sim-accounts/'+id+'/orders/'+order.id+'/fill','POST',{expected_revision:order.revision,fill_date:portfolio.as_of_date,fill_price:'11'})
  expect((await api('/sim-accounts/'+id+'/portfolio')).cash).toBe('10283.85')
  expect((await api('/legacy-imports/'+sim.id)).archive).toEqual(original)
  await page.goto(base+`/rebuild.html?page=settings&settings_group=legacy&account=${independent.id}`)
  await openArchive('sim-complete.json');await expect(page.getByRole('button',{name:'查看迁入的模拟账户',exact:true})).toBeVisible()
  expect((await api('/accounts')).filter(item=>item.kind==='sim')).toHaveLength(1)
  await openArchive('real-attachments.json')
  const attachments=page.getByRole('heading',{name:'旧复盘图片 · 显式文件对应',exact:true}).locator('..')
  await expect(attachments.getByText('尚未提供文件',{exact:true})).toBeVisible()
  const imagesBefore=await api('/accounts/'+real.account_id+'/daily-reviews/2025-01-02/attachments');expect(imagesBefore).toHaveLength(0)
  await attachments.getByLabel('为 attachment:1:0 选择图片',{exact:true}).setInputFiles({name:'chosen.png',mimeType:'image/png',buffer:Buffer.from(fixture.image,'base64')})
  await attachments.getByRole('button',{name:'预览所选图片映射',exact:true}).click()
  const confirm=attachments.getByRole('button',{name:'确认迁入这批图片',exact:true});await expect(confirm).toBeDisabled()
  expect(await api('/accounts/'+real.account_id+'/daily-reviews/2025-01-02/attachments')).toHaveLength(0)
  await attachments.getByRole('checkbox',{name:/我已核对文件内容与旧路径对应/}).check();await confirm.click()
  await expect(attachments.getByText('所选图片已迁入，原文件和复盘正文保持。',{exact:true})).toBeVisible()
  const added=await api('/accounts/'+real.account_id+'/daily-reviews/2025-01-02/attachments');expect(added).toHaveLength(1)
  expect(Buffer.from(await(await page.request.get(base+added[0].url)).body()).toString('base64')).toBe(fixture.image)
  expect((await api('/accounts/'+real.account_id+'/daily-reviews/2025-01-02')).market_observation).toBe('保留人工文字')
  expect(await api('/accounts/'+independent.id+'/daily-reviews/2025-01-02/attachments')).toHaveLength(0)
  await page.reload();await openArchive('real-attachments.json');await expect(page.getByText('已映射 · '+added[0].id,{exact:true})).toBeVisible()
  await page.setViewportSize({width:320,height:900});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true)
  expect(errors).toEqual([]);expect(external).toEqual([])
  console.log(JSON.stringify({ok:true,dataDir,checks:['invalid_cash_blocked','explicit_sim_preview','new_sim_scope','unknown_historical_metadata','continued_sell_FIFO_cash','one_time_restart','source_unchanged','explicit_image_file_mapping','no_legacy_path_read','content_hash_bytes','review_preserved','account_isolation','320px']}))
}finally{await browser?.close();server.kill();await new Promise(resolve=>{if(server.exitCode!==null)resolve();else server.once('exit',resolve);setTimeout(resolve,5000).unref()})}
