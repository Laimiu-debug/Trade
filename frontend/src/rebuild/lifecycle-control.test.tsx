import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { LifecycleControl } from './lifecycle-control'

const call = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: call }))
const normal = { supported: true, instance_id: 'source-instance', data_dir: 'D:\\Source', state: 'running', operation: null, last_transition: null, capabilities: { exit: true, switch: true }, active_writes: 0, active_background: {} }
beforeEach(() => { call.mockReset(); sessionStorage.clear() })

it('requires verified target and explicit review before submitting only its frozen token', async () => {
  call.mockImplementation(async (path: string) => path.endsWith('switch-preview') ? { verification_id: 'verified', fingerprint: 'frozen-sha', source_data_dir: 'D:\\Source', destination: 'D:\\Restored', total_bytes: 1000, dataset_count: 2, attachment_count: 1, expires_at: '2099-01-01' } : path.endsWith('/switch') ? { id: 'operation-1', kind: 'switch', state: 'draining' } : normal)
  render(<LifecycleControl suggestedDirectory="D:\Restored" />)
  fireEvent.click(await screen.findByRole('button', { name: '使用刚恢复或复制的目录' }))
  fireEvent.click(screen.getByRole('button', { name: '校验切换目标' }))
  const submit = await screen.findByRole('button', { name: '确认切换并重启' }) as HTMLButtonElement
  expect(submit.disabled).toBe(true)
  expect(call.mock.calls.filter(([, method]) => method === 'POST')).toHaveLength(1)
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(submit)
  await waitFor(() => expect(call).toHaveBeenCalledWith('/system/lifecycle/switch', 'POST', { verification_id: 'verified', expected_fingerprint: 'frozen-sha' }))
  expect(JSON.parse(sessionStorage.getItem('trade-rebuild:lifecycle-pending')!).id).toBe('operation-1')
  expect((screen.getByLabelText('切换到已有数据目录') as HTMLInputElement).closest('fieldset')!.disabled).toBe(true)
})

it('external processes expose status without offering owned-process operations', async () => {
  call.mockResolvedValue({ ...normal, supported: false, capabilities: { exit: false, switch: false } })
  render(<LifecycleControl />)
  await screen.findByText(/当前为独立服务进程/)
  expect(screen.queryByRole('button', { name: '准备退出应用' })).toBeNull()
  expect(call.mock.calls.some(([, method]) => method === 'POST')).toBe(false)
})

it('reports a rolled back transition after reconnect instead of claiming a successful switch', async () => {
  sessionStorage.setItem('trade-rebuild:lifecycle-pending', JSON.stringify({ id: 'switch-1', kind: 'switch', instance: 'source-instance', reloaded_for: 'rollback-instance', started: Date.now() }))
  call.mockResolvedValue({ ...normal, instance_id: 'rollback-instance', last_transition: { id: 'switch-1', state: 'rolled_back', active_data_dir: 'D:\\Source', error: '目标启动失败' } })
  render(<LifecycleControl />)
  await screen.findByText(/切换失败，已回到原目录/)
  expect(screen.getByRole('alert').textContent).toBe('目标启动失败')
  expect(sessionStorage.getItem('trade-rebuild:lifecycle-pending')).toBeNull()
})

it('an unrelated completed transition cannot satisfy the pending operation', async () => {
  sessionStorage.setItem('trade-rebuild:lifecycle-pending', JSON.stringify({ id: 'new-switch', kind: 'switch', instance: 'source-instance', started: Date.now() }))
  call.mockResolvedValue({ ...normal, last_transition: { id: 'older-switch', state: 'completed', active_data_dir: 'D:\\Source' } })
  render(<LifecycleControl />)
  await screen.findByText(/操作 new-switch/)
  expect(JSON.parse(sessionStorage.getItem('trade-rebuild:lifecycle-pending')!).id).toBe('new-switch')
  expect(screen.queryByText(/切换完成/)).toBeNull()
})
