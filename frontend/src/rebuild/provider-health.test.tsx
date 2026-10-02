import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ProviderHealth } from './provider-health'

const api = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api }))
vi.mock('./directory-picker', () => ({ DirectoryPicker: ({ onSelect }: { onSelect: (path: string) => void }) => <button onClick={() => onSelect('D:/chosen')}>选择通达信目录</button> }))
const empty = { current_path: null, selection: 'unset', persisted: false, candidates: [], limited: false, scope: '浅层扫描' }
const sources = { sources: [{ id: 'tdx', name: '本地通达信', network: false, configured: false, instruments: ['股票'], daily: true, location: null, notes: '' }] }
beforeEach(() => { api.mockReset(); api.mockImplementation((path: string) => Promise.resolve(path.endsWith('/providers') ? sources : empty)) })

it('lets the user pick and apply a directory without a save action', async () => {
  api.mockImplementation((path: string) => Promise.resolve(path.endsWith('/providers') ? sources : path.endsWith('/select') ? { ...empty, current_path: 'D:/chosen', selection: 'manual' } : empty))
  render(<ProviderHealth />)
  await screen.findByText(/当前目录：尚未选择/)
  fireEvent.click(screen.getByRole('button', { name: '选择通达信目录' }))
  expect(screen.getByLabelText('通达信安装目录或 vipdoc 目录')).toHaveValue('D:/chosen')
  expect(api.mock.calls.some(([, method]) => method === 'POST')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '使用此目录' }))
  await screen.findByText('本次运行已使用所选目录，可以检查或导入通达信日线。')
  expect(api).toHaveBeenCalledWith('/market/providers/tdx/select', 'POST', { path: 'D:/chosen' })
  expect(screen.getByText(/不保存目录配置/)).toBeVisible()
})

it('shows multiple discovered installations and requires an explicit choice', async () => {
  api.mockImplementation((path: string) => Promise.resolve(path.endsWith('/providers') ? sources : path.endsWith('/scan') ? { ...empty, candidates: [
    { path: 'D:/one', vipdoc: 'D:/one/vipdoc', markets: ['sh'] }, { path: 'F:/two', vipdoc: 'F:/two/vipdoc', markets: ['sz'] },
  ] } : empty))
  render(<ProviderHealth />)
  fireEvent.click(screen.getByRole('button', { name: '扫描本机' }))
  await screen.findByText('发现 2 个目录，请选择后点击“使用此目录”。')
  expect(api.mock.calls.some(([path]) => path.endsWith('/select'))).toBe(false)
  fireEvent.change(screen.getByLabelText('发现的通达信目录'), { target: { value: 'F:/two' } })
  expect(screen.getByLabelText('通达信安装目录或 vipdoc 目录')).toHaveValue('F:/two')
})
