import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SignalWorkspace } from './signal-workspace'

const mockApi = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: mockApi }))
vi.mock('./scan-jobs', () => ({ ScanJobs: () => <div>后台任务</div> }))
const contextStrategies = [
  { id: 'wulong_cluster_v1', name: '五龙聚首', signal_params: { min_score: 60, rank_weight_health: 1 }, params_schema: { min_score: { type: 'number' }, rank_weight_health: { type: 'number' } }, capabilities: {} },
  { id: 'b1_mtf_v1', name: 'B1 多周期', signal_params: { min_score: 60 }, params_schema: { min_score: { type: 'number' } }, capabilities: {} },
]
const report = { id: 'report-hash', request: {}, result: { as_of_date: '2025-03-29', signal_count: 1,
  rows: [{ run_id: 'run-a', dataset_id: 'dataset-a', symbol: 'sh600000', strategy_id: 'wulong_cluster_v1', decision_date: '2025-03-29', trigger_date: '2025-03-29', confirmation_date: '2025-03-29', confirmation_status: 'confirmed_on_observation', signal_age_bars: 0, timeliness: 'active', rank: 1, rank_score: 81, effective_entry_delay_bars: 1, executable_date: null, execution_status: 'pending_future_bar', excluded_reasons: [], draft_eligible: true, events: ['signal'], risk_events: [], event_dates: {}, quality_flags: [] }],
  per_symbol: [], same_day_intersection: [], notes: ['报告按冻结截至日显示'], source: { kind: 'fixed' }, version: 'v2', code_sha256: 'abc' } }

beforeEach(() => {
  mockApi.mockReset()
  mockApi.mockImplementation((path: string, method?: string) => {
    if (path === '/market/datasets') return Promise.resolve([{ id: 'dataset-a', symbol: '600000', first_date: '2025-01-01', last_date: '2025-03-29', availability_quality: 'known' }])
    if (path === '/research/strategies') return Promise.resolve([{ id: 'wulong_cluster_v1', name: '五龙聚首', signal_params: {}, params_schema: {}, capabilities: { supports_signal_age_filter: true, supports_entry_delay: true } }])
    if (path === '/research/scans') return Promise.resolve([{ id: 'scan-a', as_of_date: '2025-03-29', signal_count: 1 }])
    if (path.endsWith('/sources')) return Promise.resolve({ trend_pools: [], tdx_jobs: [], notes: [], signal_context_strategies: contextStrategies })
    if (path === '/research/event-profiles') return Promise.resolve({ active_profile_id: 'classic', profiles: [{ profile_id: 'classic', name: '经典规则', revision: 3 }] })
    if (path.endsWith('/reports') && !method) return Promise.resolve([])
    if (path.endsWith('/preview')) return Promise.resolve(report)
    if (path.endsWith('/reports') && method === 'POST') return Promise.resolve({ ...report, created_at: '2025-03-29', signal_count: 1 })
    if (path.endsWith('/scan-preview')) return Promise.resolve({ input_sha256: 'scan-hash', evaluation_count: 1, source: { is_subset: false } })
    if (path.endsWith('/scan-jobs')) return Promise.resolve({ id: 'job-a' })
    throw new Error(`Unexpected ${method || 'GET'} ${path}`)
  })
})

it('opens with read-only requests and no automatic signal recomputation', async () => {
  render(<SignalWorkspace onOpenMarket={vi.fn()} onOpenResearch={vi.fn()} />)
  await screen.findByRole('option', { name: '五龙聚首' })
  expect(mockApi.mock.calls.every(call => call[1] === undefined)).toBe(true)
})

it('requires preview before saving and invalidates the preview when cutoff changes', async () => {
  render(<SignalWorkspace onOpenMarket={vi.fn()} onOpenResearch={vi.fn()} />)
  await screen.findByRole('option', { name: /2025-03-29 · 1 次信号/ })
  fireEvent.change(screen.getByLabelText('已完成扫描'), { target: { value: 'scan-a' } })
  fireEvent.click(screen.getByRole('button', { name: '预览信号报告' }))
  await screen.findByText(/等待后续行情/)
  expect(mockApi.mock.calls.some(call => call[0].endsWith('/reports') && call[1] === 'POST')).toBe(false)
  fireEvent.change(screen.getByLabelText('报告截至日'), { target: { value: '2025-03-30' } })
  expect(screen.queryByRole('button', { name: '保存这份报告' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '预览信号报告' }))
  fireEvent.click(await screen.findByRole('button', { name: '保存这份报告' }))
  await screen.findByText('已保存不可变信号报告。')
  expect(screen.getByRole('link', { name: '导出交叉验证 CSV' }).getAttribute('href')).toBe('/api/v1/research/signal-workspace/reports/report-hash/cross-validation.csv')
  expect(mockApi.mock.calls.find(call => call[0].endsWith('/reports') && call[1] === 'POST')?.[2].expected_preview_sha256).toBe('report-hash')
})

it('freezes complete context, profile and independent multi-strategy parameters, then clears them when switching mode', async () => {
  render(<SignalWorkspace onOpenMarket={vi.fn()} onOpenResearch={vi.fn()} />)
  fireEvent.click(await screen.findByLabelText('选择信号样本 600000'))
  fireEvent.change(screen.getByLabelText('信号计算口径'), { target: { value: 'full' } })
  fireEvent.change(screen.getByLabelText('信号策略'), { target: { value: 'wulong_cluster_v1' } })
  fireEvent.change(screen.getByLabelText('候选指标口径'), { target: { value: 'legacy_tdx' } })
  fireEvent.change(screen.getByLabelText('事件窗口（K线）'), { target: { value: '80' } })
  fireEvent.change(screen.getByLabelText('排名权重：健康'), { target: { value: '0.5' } })
  fireEvent.click(screen.getByRole('button', { name: '加入多策略组合' }))
  fireEvent.change(screen.getByLabelText('信号策略'), { target: { value: 'b1_mtf_v1' } })
  fireEvent.change(screen.getByLabelText('综合分下限'), { target: { value: '70' } })
  fireEvent.click(screen.getByRole('button', { name: '加入多策略组合' }))
  fireEvent.click(screen.getByRole('button', { name: '预览冻结扫描' }))
  await screen.findByRole('button', { name: '确认启动后台扫描' })
  const request = mockApi.mock.calls.find(call => call[0].endsWith('/scan-preview'))?.[2]
  expect(request.scan).toMatchObject({ signal_context: { candidate_path: 'legacy_tdx', window_days: 80 }, event_profile_id: 'classic', event_profile_revision: 3,
    strategies: [{ strategy_id: 'wulong_cluster_v1', params: { rank_weight_health: '0.5' } }, { strategy_id: 'b1_mtf_v1', params: { min_score: '70' } }] })
  fireEvent.change(screen.getByLabelText('事件窗口（K线）'), { target: { value: '81' } })
  expect(screen.queryByRole('button', { name: '确认启动后台扫描' })).toBeNull()
  fireEvent.change(screen.getByLabelText('信号计算口径'), { target: { value: 'observation' } })
  expect(screen.queryByRole('option', { name: 'B1 多周期' })).toBeNull()
  expect(screen.queryByRole('button', { name: '清空组合，使用当前单策略' })).toBeNull()
  fireEvent.change(screen.getByLabelText('信号策略'), { target: { value: 'wulong_cluster_v1' } })
  fireEvent.click(screen.getByRole('button', { name: '预览冻结扫描' }))
  await screen.findByRole('button', { name: '确认启动后台扫描' })
  const single = mockApi.mock.calls.filter(call => call[0].endsWith('/scan-preview')).at(-1)?.[2]
  expect(single.scan.signal_context).toBeUndefined()
  expect(single.scan.event_profile_id).toBeUndefined()
  expect(single.scan.strategies).toEqual([{ strategy_id: 'wulong_cluster_v1', params: {} }])
})

it('keeps research promotion explicit and never posts a draft from the signal table', async () => {
  const open = vi.fn()
  render(<SignalWorkspace onOpenMarket={vi.fn()} onOpenResearch={open} />)
  await screen.findByRole('option', { name: /2025-03-29 · 1 次信号/ })
  fireEvent.change(screen.getByLabelText('已完成扫描'), { target: { value: 'scan-a' } })
  fireEvent.click(screen.getByRole('button', { name: '预览信号报告' }))
  fireEvent.click(await screen.findByRole('button', { name: '原研究 / 待买草稿' }))
  expect(open).toHaveBeenCalledWith('run-a')
  expect(mockApi.mock.calls.some(call => call[0].includes('/drafts'))).toBe(false)
})

it('explicitly freezes the candidate source and submits only after scan confirmation', async () => {
  render(<SignalWorkspace onOpenMarket={vi.fn()} onOpenResearch={vi.fn()} />)
  fireEvent.click(await screen.findByLabelText('选择信号样本 600000'))
  fireEvent.change(screen.getByLabelText('信号策略'), { target: { value: 'wulong_cluster_v1' } })
  fireEvent.click(screen.getByRole('button', { name: '预览冻结扫描' }))
  await screen.findByRole('button', { name: '确认启动后台扫描' })
  expect(mockApi.mock.calls.some(call => call[0].endsWith('/scan-jobs'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认启动后台扫描' }))
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith('/research/signal-workspace/scan-jobs', 'POST', expect.objectContaining({ expected_preview_sha256: 'scan-hash', source: { kind: 'fixed', dataset_ids: ['dataset-a'] } })))
})
