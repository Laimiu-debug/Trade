import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { LegacyReportLibrary } from './legacy-report-library'

const mocks = vi.hoisted(() => ({ api: vi.fn(), apiUpload: vi.fn() }))
vi.mock('./api', () => mocks)
const ROOT = '/research/legacy-reports'
const summary = { strategy_id: 'wyckoff', date_from: '2025-01-01', date_to: '2025-02-01', initial_capital: 1000, total_return: .1, max_drawdown: .03, win_rate: 0, reported_trade_count: 0, trade_rows: 0, equity_points: 2, point_count: 2, point_detail_count: 1 }
const params = { window_days: 60, min_score: 55, stop_loss: .08 }
const run = { stats: { total_return: .1 }, trades: [], equity_curve: [{ date: '2025-01-01', equity: 1000 }, { date: '2025-02-01', equity: 1100 }] }
const preview = { source_format: 'final_trade_ftbt_1.0', source_sha256: 'a'.repeat(64), source_bytes: 1000, filename: 'old.ftbt', title: '旧报告 · 原平原', can_convert: true, summary, attachments: [{ name: 'report.html', bytes: 100, sha256: 'b'.repeat(64), untrusted: true }], limitations: ['来源结果未重新计算，不能续跑。'], quality_flags: ['reexecution_unavailable'], missing_fields: [], preview_sha256: 'c'.repeat(64) }
const report = { ...preview, id: 'saved-old', mode: 'legacy_readonly', created_at: '2025-02-02T00:00:00Z', content_sha256: 'd'.repeat(64), preview, payload: { limitations: preview.limitations, run_request: { strategy_id: 'wyckoff' }, run_result: run, plateau_result: { evaluated_combinations: 2, total_combinations: 2, points: [{ params, stats: { total_return: .1, max_drawdown: .03 }, score: 80, plateau_score: 70, detail_key: 'point_detail' }, { params, stats: {}, error: '<img src=x onerror=bad()>', passes_hard_filters: false }] }, point_details: { point_detail: { params, run_request: params, run_result: run } } } }
beforeEach(() => {
  mocks.api.mockReset(); mocks.apiUpload.mockReset()
  mocks.api.mockImplementation(async (path: string, method?: string) => path === ROOT ? [] : method === 'DELETE' ? { deleted: true } : report)
  mocks.apiUpload.mockImplementation(async (path: string) => path.endsWith('/preview') ? preview : report)
})

it('previews without saving and requires explicit acknowledgement of the frozen preview', async () => {
  render(<LegacyReportLibrary />)
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(ROOT))
  expect(mocks.apiUpload).not.toHaveBeenCalled()
  const file = new File(['old'], 'old.ftbt')
  fireEvent.change(screen.getByLabelText('旧 FTBT / 平原 JSON / HTML 文件'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: '预览旧报告' }))
  const panel = await screen.findByRole('region', { name: '旧报告导入预览' })
  const confirm = within(panel).getByRole('button', { name: '确认保存只读旧报告' }) as HTMLButtonElement
  expect(confirm.disabled).toBe(true)
  expect(mocks.apiUpload).toHaveBeenCalledTimes(1)
  fireEvent.click(within(panel).getByRole('checkbox'))
  fireEvent.click(confirm)
  await screen.findByRole('region', { name: '旧档案详情' })
  expect(mocks.apiUpload).toHaveBeenLastCalledWith(ROOT + '/import', file, { expected_preview_sha256: preview.preview_sha256, mode: 'legacy_readonly', acknowledge_limitations: 'true' })
  expect(screen.queryByRole('region', { name: '旧报告导入预览' })).toBeNull()
})

it('changing an input file invalidates the prior preview and never imports it', async () => {
  render(<LegacyReportLibrary />)
  const field = screen.getByLabelText('旧 FTBT / 平原 JSON / HTML 文件')
  fireEvent.change(field, { target: { files: [new File(['one'], 'one.ftbt')] } })
  fireEvent.click(screen.getByRole('button', { name: '预览旧报告' }))
  fireEvent.click(within(await screen.findByRole('region', { name: '旧报告导入预览' })).getByRole('checkbox'))
  fireEvent.change(field, { target: { files: [new File(['two'], 'two.ftbt')] } })
  expect(screen.queryByRole('button', { name: '确认保存只读旧报告' })).toBeNull()
  expect(mocks.apiUpload).toHaveBeenCalledTimes(1)
})

it('renders hostile source text safely, links binary originals, and preserves exact point details without run actions', async () => {
  mocks.api.mockResolvedValueOnce([report])
  render(<LegacyReportLibrary />)
  fireEvent.click(await screen.findByRole('button', { name: '查看旧档案' }))
  const detail = await screen.findByRole('region', { name: '旧档案详情' })
  expect(within(detail).getByText('<img src=x onerror=bad()>')).not.toBeNull()
  expect(detail.querySelectorAll('script, iframe, img')).toHaveLength(0)
  const download = within(detail).getByRole('link', { name: '下载原件 report.html' })
  expect(download.getAttribute('href')).toBe('/api/v1' + ROOT + '/saved-old/original.bin?name=report.html')
  expect(download.hasAttribute('download')).toBe(true)
  fireEvent.click(within(detail).getByRole('button', { name: '查看旧参数点 1' }))
  const point = await screen.findByRole('region', { name: '旧参数点详情' })
  expect(within(point).getByText('window_days')).not.toBeNull()
  expect(within(point).getByText('60')).not.toBeNull()
  expect(within(point).queryByRole('img', { name: '旧报告原资产曲线，未重新计算' })).not.toBeNull()
  fireEvent.click(within(detail).getByRole('button', { name: '查看旧参数点 2' }))
  expect(screen.getByText('原包未提供此点完整回测详情；未生成替代结果。')).not.toBeNull()
  expect(mocks.api.mock.calls.every(call => !call[1])).toBe(true)
  expect(screen.queryByRole('button', { name: /运行|续跑|交易/ })).toBeNull()
})

it('shows missing paths and only permits an explicit archive save', async () => {
  const incomplete = { ...preview, source_format: 'opaque_html', can_convert: false, missing_fields: ['run_request.json', 'run_result.json'] }
  mocks.apiUpload.mockResolvedValueOnce(incomplete).mockResolvedValueOnce({ ...report, mode: 'archive_only', can_convert: false, preview: incomplete, payload: { ...report.payload, plateau_result: { points: 'unvalidated' } } })
  render(<LegacyReportLibrary />)
  const file = new File(['<script>bad()</script>'], 'original.html')
  fireEvent.change(screen.getByLabelText('旧 FTBT / 平原 JSON / HTML 文件'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: '预览旧报告' }))
  const panel = await screen.findByRole('region', { name: '旧报告导入预览' })
  expect(within(panel).getByText('run_request.json')).not.toBeNull()
  expect(within(panel).queryByRole('button', { name: '确认保存只读旧报告' })).toBeNull()
  fireEvent.click(within(panel).getByRole('checkbox')); fireEvent.click(within(panel).getByRole('button', { name: '确认仅保存原件' }))
  await screen.findByRole('region', { name: '旧档案详情' })
  expect(mocks.apiUpload).toHaveBeenLastCalledWith(ROOT + '/import', file, expect.objectContaining({ mode: 'archive_only' }))
  expect(screen.queryByRole('region', { name: '旧平原实验' })).toBeNull()
})

it('deletion requires confirmation and removing a viewed record clears it', async () => {
  mocks.api.mockResolvedValueOnce([report])
  render(<LegacyReportLibrary />)
  fireEvent.click(await screen.findByRole('button', { name: '查看旧档案' }))
  await screen.findByRole('region', { name: '旧档案详情' })
  fireEvent.click(screen.getByRole('button', { name: '删除旧档案' }))
  expect(mocks.api.mock.calls.some(call => call[1] === 'DELETE')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认删除旧档案' }))
  await screen.findByText('旧档案已从列表删除。')
  expect(screen.queryByRole('region', { name: '旧档案详情' })).toBeNull()
})
