import { spawn } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const dataDir = await mkdtemp(path.join(os.tmpdir(), 'trade-legacy-supplements-ui-'))
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

  const payload={exported_at:'2025-01-05T00:00:00',capital_flows:[{id:1,flow_date:'2025-01-01',kind:'initial',amount:10000}],
    trades:[{id:1,trade_date:'2025-01-02',code:'600000',side:'buy',qty:100,price:10},{id:2,trade_date:'2025-01-03',code:'600000',side:'sell',qty:100,price:12}],
    round_reviews:[{id:9,code:'600000',start_date:'2025-01-02',review_summary:'原回合复盘，保留来源'}],
    flash_cards:[{id:1,content:'纪律优先',tags:'纪律,复盘'},{id:2,content:'纪律优先',tags:'纪律,复盘'},{id:3,content:'另一窗口的卡片',tags:'其他'}],
    settings:[{key:'commission_min',value:'6'},{key:'wave_pct',value:'40'},{key:'node_count',value:'10'},{key:'market_priority',value:'tdx,akshare,web'},
      {key:'ai_score_base_url',value:'https://example.invalid/v1'},{key:'ai_score_text_model',value:'old-model'},{key:'ai_score_api_key',value:'never-persist-fixture-secret'}]}
  const other=await api('/accounts','POST',{name:'不应被改动的账户'})
  const original=JSON.stringify(payload),upload={filename:'supplement.json',content_base64:Buffer.from(original).toString('base64'),mode:'new_real_account',account_name:'补充映射账户'}
  const before=await api('/legacy-imports/preview','POST',upload)
  const imported=await api('/legacy-imports','POST',{...upload,expected_preview_sha256:before.preview_sha256,acknowledge_limitations:true})
  await expect.poll(async()=>(await api('/accounts/'+imported.account_id+'/analytics')).status).toBe('fresh')
  const root='/legacy-imports/'+imported.id+'/supplements'
  await page.goto(`${base}/rebuild.html?page=settings&settings_group=legacy&account=${other.id}`)
  await page.getByRole('button',{name:'查看档案',exact:true}).click()
  const editor=page.getByRole('heading',{name:'旧卡片、设置与回合 · 逐项补充映射',exact:true}).locator('..')
  await expect(editor.getByRole('checkbox',{name:'补充迁入 全局行情来源',exact:true})).toBeDisabled()
  await editor.getByText('模型配置迁入的环境变量引用',{exact:true}).click()
  await editor.getByLabel('文本迁入密钥环境引用',{exact:true}).fill('TRADE_AI_LEGACY_KEY')
  await editor.getByRole('button',{name:'核对环境引用与最新目标',exact:true}).click()
  await expect(editor.getByText('已校验当前环境变量名称与目标版本，尚未写入。')).toBeVisible()
  for(const label of ['共享灵感卡 · card:1','旧回合摘要 · round:9','账户费用','账户目标','全局模型配置'])await editor.getByRole('checkbox',{name:'补充迁入 '+label,exact:true}).check()
  expect(await api('/insights/cards')).toHaveLength(0)
  await editor.getByRole('button',{name:'预览所选 5 项补充映射',exact:true}).click()
  const confirm=editor.getByRole('button',{name:'确认应用本批补充映射',exact:true})
  await expect(confirm).toBeDisabled()
  expect((await api('/settings/groups/fees?account_id='+imported.account_id)).value.minimum_commission).toBe('5.00')
  await editor.getByRole('checkbox',{name:/我已核对本批每项来源/}).check()
  await confirm.click()
  await expect(editor.getByText('已保存所选补充映射；原档案和原文件不变。')).toBeVisible()
  expect((await api('/insights/cards')).length).toBe(1)
  expect((await api('/accounts/'+imported.account_id+'/round-notes'))[0].summary).toBe('原回合复盘，保留来源')
  expect((await api('/settings/groups/fees?account_id='+imported.account_id)).value.minimum_commission).toBe('6.00')
  expect((await api('/settings/groups/fees?account_id='+other.id)).value.minimum_commission).toBe('5.00')
  expect((await api('/ai/config')).text.secret_ref).toBe('TRADE_AI_LEGACY_KEY')
  const archive=await (await page.request.get(base+'/api/v1/legacy-imports/'+imported.id+'/export.json')).json()
  expect(JSON.stringify(archive)).not.toContain('never-persist-fixture-secret')
  expect(archive.supplement_mappings.length).toBe(1)
  await editor.getByRole('checkbox',{name:'补充迁入 共享灵感卡 · card:2',exact:true}).check()
  await editor.getByRole('button',{name:'预览所选 1 项补充映射',exact:true}).click()
  const concurrent={expected_revision:2,selected_keys:['card:3'],secret_refs:{}}
  const newPreview=await api(root+'/preview','POST',concurrent)
  await api(root+'/apply','POST',{...concurrent,expected_preview_sha256:newPreview.preview_sha256,acknowledge_limitations:true})
  await editor.getByRole('checkbox',{name:/我已核对本批每项来源/}).check()
  await editor.getByRole('button',{name:'确认应用本批补充映射',exact:true}).click()
  await expect(editor.getByText('旧档案修订已变化，请刷新后重新预览')).toBeVisible()
  await expect(editor.getByRole('checkbox',{name:'补充迁入 共享灵感卡 · card:2',exact:true})).toBeChecked()
  expect((await api('/insights/cards')).length).toBe(2)
  await editor.getByRole('button',{name:'重新读取来源与目标',exact:true}).click()
  await editor.getByRole('button',{name:'预览所选 1 项补充映射',exact:true}).click()
  await editor.getByRole('checkbox',{name:/我已核对本批每项来源/}).check()
  await editor.getByRole('button',{name:'确认应用本批补充映射',exact:true}).click()
  await expect(editor.getByText('已保存所选补充映射；原档案和原文件不变。')).toBeVisible()
  expect((await api('/insights/cards')).length).toBe(2)
  await page.reload()
  await page.getByRole('button',{name:'查看档案',exact:true}).click()
  await expect(page.getByText('补充映射历史 · 3 批', {exact:true})).toBeVisible()
  await page.setViewportSize({width:320,height:900})
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true)
  expect(errors).toEqual([]);expect(external).toEqual([]);expect(original).toBe(JSON.stringify(payload))
  console.log(JSON.stringify({ok:true,dataDir,checks:['explicit_previews','round_exact_ids','shared_cards','target_account_only','AI_reference_no_secret','no_provider_network','revision_conflict_retains_selection','duplicate_card_reuse','history_reload','logical_archive_unchanged','320px']}))
} finally {
  await browser?.close()
  server.kill()
  await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); setTimeout(resolve, 5000).unref() })
}
