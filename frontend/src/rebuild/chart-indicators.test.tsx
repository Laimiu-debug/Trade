import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { buildEvidence, calculateForceSeries, movingAverage, seriesPath, type ChartResearchRun } from './chart-indicators'
import { MarketChart, type ChartBar } from './market-chart'
import legacySeries from './__fixtures__/legacy-ths-main-retail-series.json'

const api = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api }))

const bars: ChartBar[] = Array.from({ length: 70 }, (_, i) => ({ event_date: new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10),
  open: String(10 + i % 8), high: String(12 + i % 8), low: String(9 + i % 8), close: String(11 + (i * 7) % 8),
  volume: 100000 + i * 1731, amount: '1000000.00', available_at: null }))
const run = (change: Partial<ChartResearchRun> = {}): ChartResearchRun => ({ id: 'event-run', dataset_id: 'frozen-dataset', strategy_id: 'wyckoff_trend_v2', strategy_version: 'v2', decision_at: '2025-03-11T07:00:00+00:00', strict: false, params: {}, result: {
  status: 'computed', signal: true, source_date: '2025-03-11', draft_eligible: false,
  indicator: { phase: 'Accumulation', event_chain: [
    { event: 'LPS', date: '2025-03-10' }, { event: 'AR', date: '2025-02-20' }, { event: 'SOS', date: '2025-03-08' },
    { event: 'UTAD', date: '2025-03-07' }, { event: 'SC', date: '2024-12-31' }, { event: 'JOC', date: '2025-03-12' },
  ], event_confirmation_map: { SOS: 'confirmed', LPS: 'pending' } },
}, ...change })

describe('original chart indicator formulas', () => {
  it('matches the actual legacy utility for every point, unit and marker', () => {
    // Output of the retired shared/utils/thsVolumeSignal.ts for these bars, recorded at tag legacy-final.
    const original = legacySeries
    const actual = calculateForceSeries(bars)
    expect(actual).toHaveLength(original.length)
    actual.forEach((row, index) => {
      for (const field of ['mainForce', 'retailForce', 'mainForceState', 'purpleToYellow', 'goldenCross'] as const) expect(row[field]).toEqual(original[index][field])
    })
  })
  it('each prefix is unchanged by future bars and by viewport position', () => {
    const all = calculateForceSeries(bars)
    for (const count of [1, 2, 3, 12, 35, 69]) expect(calculateForceSeries(bars.slice(0, count))).toEqual(all.slice(0, count))
    for (const count of [5, 20, 35]) expect(movingAverage(bars.slice(0, count), 5)).toEqual(movingAverage(bars, 5).slice(0, count))
    expect(movingAverage(bars, 5).slice(0, 4)).toEqual([null, null, null, null])
  })
  it('breaks missing observations without treating missing data as zero or connecting across gaps', () => {
    const input = bars.map(row => ({ ...row }))
    input[10].volume = Number.NaN
    const force = calculateForceSeries(input)
    expect(force[10].mainForce).toBeNull()
    expect(force[11].mainForce).toBe(0)
    expect(force[11].goldenCross).toBe(false)
    input[12].close = ''
    expect(movingAverage(input, 5).slice(12, 17)).toEqual([null, null, null, null, null])
    const path = seriesPath([1, null, 2, 3], 0, 4, n => n, n => n)
    expect(path.match(/M/g)).toHaveLength(2)
    expect(path.match(/L/g)).toHaveLength(1)
  })
})

describe('frozen event evidence', () => {
  it('retains decision time, confirmation and exact dates; excludes missing and future events', () => {
    const result = buildEvidence([run()], bars, 'frozen-dataset', '2025-03-11')
    expect(result.events.map(row => row.event)).toEqual(['AR', 'UTAD', 'SOS', 'LPS'])
    expect(result.events.find(row => row.event === 'SOS')?.knownAt).toBe('2025-03-11T07:00:00+00:00')
    expect(result.events.find(row => row.event === 'SOS')?.confirmation).toBe('confirmed')
    expect(result.events.find(row => row.event === 'UTAD')?.category).toBe('risk')
    expect(result.boundaries[0].label).toBe('小溪线')
    expect(result.boundaries[0].start.date).toBe('2025-02-20')
    expect(result.boundaries[0].end.date).toBe('2025-03-10')
    expect(result.signals[0].label).toBe('观察')
    expect(result.signals[0].eligible).toBe(false)
  })
  it('hides a snapshot when graph ends before its source and never crosses dataset identities', () => {
    expect(buildEvidence([run()], bars, 'another-dataset', '2025-03-11').events).toEqual([])
    const past = buildEvidence([run()], bars, 'frozen-dataset', '2025-03-10')
    expect(past.events).toEqual([])
    expect(past.excluded).toHaveLength(1)
  })
})

it('loads only explicit saved evidence and preserves toggles, AI and exchange-specific executions', async () => {
  api.mockResolvedValue([run(), run({ id: 'foreign', dataset_id: 'another' })])
  render(<MarketChart bars={bars} datasetId="frozen-dataset" symbol="000001.SZ" quality="historical_availability_unknown" aiStartDate="2025-03-08" manualStartDate="2025-03-07" executions={[
    { id: 'correct', symbol: 'SZ000001', date: '2025-03-10', side: 'buy', quantity: 100, price: '11', source: 'sim' },
    { id: 'wrong', symbol: 'sh000001', date: '2025-03-10', side: 'buy', quantity: 999, price: '11', source: 'sim' },
  ]} />)
  expect(api).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('已保存研究的事件与策略图层'))
  fireEvent.click(screen.getByRole('button', { name: '读取可叠加研究' }))
  await waitFor(() => expect(screen.queryByLabelText('叠加研究 event-run')).not.toBeNull())
  expect(screen.queryByLabelText('叠加研究 foreign')).toBeNull()
  fireEvent.click(screen.getByLabelText('叠加研究 event-run'))
  expect(screen.queryByRole('img', { name: /事件 SOS 2025-03-08/ })).not.toBeNull()
  expect(screen.queryByRole('img', { name: /事件 UTAD 2025-03-07/ })).not.toBeNull()
  fireEvent.click(screen.getByLabelText('派发 / 风险事件'))
  expect(screen.queryByRole('img', { name: /事件 UTAD 2025-03-07/ })).toBeNull()
  expect(screen.queryByRole('img', { name: 'AI 候选起爆日 2025-03-08' })).not.toBeNull()
  expect(screen.queryByRole('img', { name: '模拟 2025-03-10 买入 100 股，成交价 11' })).not.toBeNull()
  expect(screen.queryByRole('img', { name: /买入 999 股/ })).toBeNull()
  fireEvent.click(screen.getByLabelText('主力 / 散户量能'))
  expect(screen.queryByRole('img', { name: '主力与散户量能独立坐标' })).toBeNull()
  fireEvent.change(screen.getByLabelText('K 线结束位置'), { target: { value: '68' } })
  expect(screen.queryByRole('img', { name: /事件 SOS/ })).toBeNull()
  expect(api).toHaveBeenCalledTimes(1)
  expect(api).toHaveBeenCalledWith('/research/runs')
})
