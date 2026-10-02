import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { aiDraftKey, emptyAIDraft, useAIDraft } from './ai-drafts'
import { GlobalAILauncher, sendAIHandoff, takeAIHandoff, AI_ACTIVITY_EVENT } from './ai-launcher'
import { AIWorkspace } from './ai-workspace'
const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call, apiStream: vi.fn() }))
const config = { revision: 0, text: { base_url:'', model:'',secret_ref:'' }, vision:{ base_url:'',model:'',secret_ref:'' }, temperature:.7,max_tokens:2048,timeout_seconds:60 }
const quick = {revision:0,items:[],system_items:[],pages:['generic'],updated_at:null}
beforeEach(() => {
  localStorage.clear(); takeAIHandoff(); call.mockReset()
  call.mockImplementation((path: string) => Promise.resolve(path === '/ai/config' ? config : path === '/ai/quick-prompts' ? quick : path.includes('/ai/usage') ? {by_model:[]} : []))
})

it('persists drafts across remount, isolates account and session keys', async () => {
  const first = renderHook(({scope,session}) => useAIDraft(scope,session), {initialProps:{scope:'a',session:'one'}})
  act(() => first.result.current.field('message')('account a question'))
  await waitFor(() => expect(localStorage.getItem(aiDraftKey('a','one'))).toContain('account a question'))
  first.rerender({scope:'b',session:'one'})
  expect(first.result.current.data.message).toBe('')
  act(() => first.result.current.field('message')('account b question'))
  first.rerender({scope:'a',session:'two'})
  expect(first.result.current.data.message).toBe('')
  first.rerender({scope:'a',session:'one'})
  expect(first.result.current.data.message).toBe('account a question')
  first.unmount()
  const next=renderHook(() => useAIDraft('b','one'))
  expect(next.result.current.data.message).toBe('account b question')
})

it('cross-tab conflict keeps input and requires explicit resolution', async () => {
  const draft=renderHook(() => useAIDraft('a','one'))
  act(() => draft.result.current.field('message')('mine'))
  const key=aiDraftKey('a','one'), raw=JSON.stringify({revision:9,data:{...emptyAIDraft(),message:'other'}})
  act(() => { localStorage.setItem(key,raw); window.dispatchEvent(new StorageEvent('storage',{key,newValue:raw})) })
  expect(draft.result.current.data.message).toBe('mine')
  expect(draft.result.current.conflict?.data?.message).toBe('other')
  act(() => draft.result.current.resolve(false))
  await waitFor(() => expect(JSON.parse(localStorage.getItem(key)!).data.message).toBe('mine'))
  expect(JSON.parse(localStorage.getItem(key)!).revision).toBe(10)
})

it('launcher buffers a handoff before lazy mount and never initiates API calls', () => {
  const open=vi.fn()
  render(<GlobalAILauncher page="market" datasetId="chosen" accountId="a" onOpen={open} />)
  fireEvent.click(screen.getByRole('button',{name:'询问 AI 助手'}))
  expect(takeAIHandoff()).toMatchObject({page:'market',datasetId:'chosen',accountId:'a'})
  expect(takeAIHandoff()).toBeNull()
  act(() => window.dispatchEvent(new CustomEvent(AI_ACTIVITY_EVENT,{detail:{running:true,accountId:'a',runId:'r'}})))
  fireEvent.click(screen.getByRole('button',{name:'查看运行中的 AI 助手'}))
  expect(takeAIHandoff()).toBeNull()
  expect(open).toHaveBeenCalledTimes(2)
  expect(call).not.toHaveBeenCalled()
})

it('workspace retains source until explicit adoption and preserves typed draft', async () => {
  localStorage.setItem(aiDraftKey('',null),JSON.stringify({revision:1,data:{...emptyAIDraft(),message:'existing',manual:'private existing'}}))
  sendAIHandoff({page:'market',datasetId:'chosen',accountId:'a'})
  render(<AIWorkspace accountId="a" accountKind="real" />)
  await screen.findByText('待加入的页面上下文')
  expect(JSON.parse(localStorage.getItem(aiDraftKey('',null))!).data.datasetIds).toEqual([])
  fireEvent.click(screen.getByRole('button',{name:'加入草稿，稍后预览'}))
  await waitFor(() => expect(JSON.parse(localStorage.getItem(aiDraftKey('',null))!).data.datasetIds).toEqual(['chosen']))
  expect(JSON.parse(localStorage.getItem(aiDraftKey('',null))!).data.message).toBe('existing')
  expect(call.mock.calls.every(([,method]) => method === undefined)).toBe(true)
})

it('explicit account handoff adopts target draft without copying another account input', async () => {
  localStorage.setItem(aiDraftKey('',null),JSON.stringify({revision:1,data:{...emptyAIDraft(),message:'unbound private'}}))
  localStorage.setItem(aiDraftKey('a',null),JSON.stringify({revision:2,data:{...emptyAIDraft(),message:'account a own'}}))
  sendAIHandoff({page:'market',datasetId:'chosen',accountId:'a'})
  render(<AIWorkspace accountId="a" accountKind="real" />)
  await screen.findByText('待加入的页面上下文')
  fireEvent.click(screen.getByRole('checkbox',{name:/同时绑定来源账户/}))
  fireEvent.click(screen.getByRole('button',{name:'加入草稿，稍后预览'}))
  await waitFor(() => expect(JSON.parse(localStorage.getItem(aiDraftKey('a',null))!).data.datasetIds).toEqual(['chosen']))
  expect(JSON.parse(localStorage.getItem(aiDraftKey('a',null))!).data.message).toBe('account a own')
  expect(JSON.parse(localStorage.getItem(aiDraftKey('',null))!).data.message).toBe('unbound private')
})

it('quick prompt copies a readonly item and only explicit save mutates server', async () => {
  const system = {id:'system:x',page:'generic',label:'系统证据',prompt:'内置内容',pinned:true,readonly:true}
  call.mockImplementation((path: string, method?: string, body?: {items:unknown[]}) => Promise.resolve(path === '/ai/quick-prompts' ? { ...quick, revision: method ? 1 : 0, system_items:[system], items:body?.items || [] } : []))
  const { AIQuickPrompts } = await import('./ai-quick-prompts')
  const use = vi.fn()
  render(<AIQuickPrompts page="market" disabled={false} onUse={use} />)
  fireEvent.click(await screen.findByRole('button',{name:'★ 系统证据 · 内置'}))
  expect(use).toHaveBeenCalledWith('内置内容')
  expect(call.mock.calls.every(([,method]) => method === undefined)).toBe(true)
  fireEvent.click(screen.getByText(/管理页面提问/))
  fireEvent.click(screen.getByRole('button',{name:'复制内置“系统证据”'}))
  fireEvent.change(screen.getByLabelText('快捷提问名称'),{target:{value:'自己的问题'}})
  fireEvent.click(screen.getByRole('button',{name:'保存快捷提问'}))
  await screen.findByText('快捷提问已保存，不会自动发送到模型。')
  const saved=call.mock.calls.find(([,method]) => method === 'PUT')![2]
  expect(saved.expected_revision).toBe(0)
  expect(saved.items[0]).toMatchObject({label:'自己的问题',prompt:'内置内容'})
  expect(saved.items[0].id).toMatch(/^[a-f0-9]{32}$/)
  expect(saved.items[0].readonly).toBeUndefined()
})

it('quick prompt revision conflict retains edited text and reload requires explicit action', async () => {
  let changed=false
  call.mockImplementation((_path: string,method?:string) => {
    if(method) { changed=true; return Promise.reject(Object.assign(new Error('其他页面已修改'),{code:'QUICK_PROMPTS_CONFLICT'})) }
    return Promise.resolve({...quick,revision:changed?2:1})
  })
  const { AIQuickPrompts } = await import('./ai-quick-prompts')
  render(<AIQuickPrompts page="market" disabled={false} onUse={vi.fn()} />)
  await screen.findByText(/版本 1/)
  fireEvent.click(screen.getByText(/管理页面提问/))
  fireEvent.change(screen.getByLabelText('快捷提问名称'),{target:{value:'我的未保存名称'}})
  fireEvent.change(screen.getByLabelText('快捷提问正文'),{target:{value:'我的未保存正文'}})
  fireEvent.click(screen.getByRole('button',{name:'保存快捷提问'}))
  await screen.findByText('当前编辑草稿保留，服务端已有版本 2。')
  expect((screen.getByLabelText('快捷提问正文') as HTMLTextAreaElement).value).toBe('我的未保存正文')
  fireEvent.click(screen.getByRole('button',{name:'载入最新列表，保留编辑草稿'}))
  expect((screen.getByLabelText('快捷提问正文') as HTMLTextAreaElement).value).toBe('我的未保存正文')
})
