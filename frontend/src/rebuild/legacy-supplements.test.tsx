import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { LegacySupplementEditor } from './legacy-supplements'
const call=vi.hoisted(()=>vi.fn())
vi.mock('./api',()=>({api:call}))
const item={key:'card:1',label:'共享灵感卡',source:{content:'old'},status:'ready',reason:null,action:{kind:'card',before:null,after:{content:'old',tags:[]}}}
const catalog={import_id:'import',expected_revision:2,account_id:null,source_sha256:'source',logical_sha256:'logical',items:[item],unmapped:[{source:'images',reason:'未提供二进制'}],notes:['共享库']}
const frozen={...catalog,selected_keys:['card:1'],selected:[item],preview_sha256:'hash',can_apply:true}
beforeEach(()=>{call.mockReset();call.mockImplementation((path:string)=>Promise.resolve(path.endsWith('/history')?[]:path.endsWith('/preview')?frozen:catalog))})

it('selection and preview do not apply; acknowledgement binds exact source revision and hash',async()=>{
  render(<LegacySupplementEditor importId="import" />)
  fireEvent.click(await screen.findByRole('checkbox',{name:'补充迁入 共享灵感卡 · card:1'}))
  expect(call.mock.calls.every(([,method])=>method===undefined)).toBe(true)
  fireEvent.click(screen.getByRole('button',{name:'预览所选 1 项补充映射'}))
  const confirm=await screen.findByRole('button',{name:'确认应用本批补充映射'})
  expect((confirm as HTMLButtonElement).disabled).toBe(true)
  expect(call.mock.calls.some(([path])=>String(path).endsWith('/apply'))).toBe(false)
  fireEvent.click(screen.getByRole('checkbox',{name:/我已核对本批每项来源/}))
  fireEvent.click(confirm)
  await screen.findByText('已保存所选补充映射；原档案和原文件不变。')
  expect(call).toHaveBeenCalledWith('/legacy-imports/import/supplements/apply','POST',{expected_revision:2,selected_keys:['card:1'],secret_refs:{text:'TRADE_AI_TEXT_KEY',vision:'TRADE_AI_VISION_KEY'},expected_preview_sha256:'hash',acknowledge_limitations:true})
})

it('edited refs invalidate preview and raw keys are never represented as password fields',async()=>{
  render(<LegacySupplementEditor importId="import" />)
  fireEvent.click(await screen.findByRole('checkbox',{name:'补充迁入 共享灵感卡 · card:1'}))
  fireEvent.click(screen.getByRole('button',{name:'预览所选 1 项补充映射'}))
  await screen.findByRole('button',{name:'确认应用本批补充映射'})
  fireEvent.click(screen.getByText('模型配置迁入的环境变量引用'))
  fireEvent.change(screen.getByLabelText('文本迁入密钥环境引用'),{target:{value:'TRADE_AI_OTHER_KEY'}})
  expect(screen.queryByRole('button',{name:'确认应用本批补充映射'})).toBeNull()
  expect((screen.getByLabelText('文本迁入密钥环境引用') as HTMLInputElement).type).toBe('text')
})

it('a changed target rejects apply and retains selection with visible refresh action',async()=>{
  call.mockImplementation((path:string)=>path.endsWith('/apply')?Promise.reject(new Error('来源或目标版本已变化')):Promise.resolve(path.endsWith('/history')?[]:path.endsWith('/preview')?frozen:catalog))
  render(<LegacySupplementEditor importId="import" />)
  fireEvent.click(await screen.findByRole('checkbox',{name:'补充迁入 共享灵感卡 · card:1'}))
  fireEvent.click(screen.getByRole('button',{name:'预览所选 1 项补充映射'}))
  await screen.findByRole('button',{name:'确认应用本批补充映射'})
  fireEvent.click(screen.getByRole('checkbox',{name:/我已核对本批每项来源/}))
  fireEvent.click(screen.getByRole('button',{name:'确认应用本批补充映射'}))
  await screen.findByText('来源或目标版本已变化')
  expect((screen.getByRole('checkbox',{name:'补充迁入 共享灵感卡 · card:1'}) as HTMLInputElement).checked).toBe(true)
  expect(screen.getByRole('button',{name:'重新读取来源与目标'})).toBeDefined()
})
