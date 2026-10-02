/** Deterministic chart series. Source formula: legacy shared/utils/thsVolumeSignal.ts.
 * Missing observations break and restart force series; they are never converted to zero.
 */
export type IndicatorBar = { event_date: string; open: string; high: string; low: string; close: string; volume: number }
export type ForcePoint = { date: string; mainForce: number | null; retailForce: number | null; mainForceState: 'rising' | 'falling' | 'flat'; purpleToYellow: boolean; goldenCross: boolean }
export type ChartResearchRun = { id: string; dataset_id: string; strategy_id: string; strategy_version: string; decision_at: string; strict: boolean; params: Record<string, string>; result: {
  status: string; signal: boolean | null; source_date: string | null; draft_eligible?: boolean; quality_flags?: string[]
  primary_signal?: string; indicator?: Record<string, unknown>; evaluation?: Record<string, unknown>
} }
const value = (raw: unknown): number | null => raw === null || raw === undefined || raw === '' || !Number.isFinite(Number(raw)) ? null : Number(raw)
const round = (raw: number, places: number) => Number(raw.toFixed(places))

export function movingAverage(bars: IndicatorBar[], period: number): Array<number | null> {
  if (!Number.isInteger(period) || period <= 0) return bars.map(() => null)
  const closes = bars.map(row => value(row.close))
  return closes.map((_current, index) => {
    if (index + 1 < period) return null
    const window = closes.slice(index - period + 1, index + 1)
    if (window.some(item => item === null || item <= 0)) return null
    return round((window as number[]).reduce((sum, item) => sum + item, 0) / period, 2)
  })
}

export function calculateForceSeries(bars: IndicatorBar[]): ForcePoint[] {
  let previousClose: number | null = null, main = 0, retail = 0, previousMain = 0, previousRetail = 0, previousPreviousMain = 0, count = 0
  let up: number[] = [], down: number[] = []
  return bars.map(bar => {
    const close = value(bar.close), volume = value(bar.volume)
    if (close === null || close <= 0 || volume === null || volume < 0) {
      previousClose = null; main = retail = previousMain = previousRetail = previousPreviousMain = count = 0; up = []; down = []
      return { date: bar.event_date, mainForce: null, retailForce: null, mainForceState: 'flat', purpleToYellow: false, goldenCross: false }
    }
    up.push(previousClose !== null && close > previousClose ? volume / 100 : 0)
    down.push(previousClose !== null && close < previousClose ? volume / 100 : 0)
    if (up.length > 3) { up.shift(); down.shift() }
    const upMean = up.reduce((a, b) => a + b, 0) / up.length, downMean = down.reduce((a, b) => a + b, 0) / down.length
    main = count ? 0.5 * upMean + 0.5 * main : upMean
    retail = count ? (2 / 11) * downMean + (9 / 11) * retail : downMean
    const state = main > previousMain ? 'rising' : main < previousMain ? 'falling' : 'flat'
    const row: ForcePoint = { date: bar.event_date, mainForce: round(main, 6), retailForce: round(retail, 6), mainForceState: state,
      purpleToYellow: count >= 2 && previousMain > 0 && previousMain < previousPreviousMain && main > 0 && main > previousMain,
      goldenCross: count >= 1 && main > retail && previousMain <= previousRetail }
    previousPreviousMain = previousMain; previousMain = main; previousRetail = retail; previousClose = close; count += 1
    return row
  })
}

/** Starts a new path after every missing value, including gaps within a visible range. */
export function seriesPath(values: Array<number | null>, start: number, count: number, x: (offset: number) => number, y: (value: number) => number): string {
  let drawing = false, path = ''
  for (let offset = 0; offset < count; offset += 1) {
    const item = values[start + offset]
    if (item === null || item === undefined || !Number.isFinite(item)) { drawing = false; continue }
    path += `${drawing ? ' L' : ' M'}${x(offset).toFixed(2)},${y(item).toFixed(2)}`
    drawing = true
  }
  return path.trim()
}

const accumulation = new Set(['PS', 'SC', 'AR', 'ST', 'TSO', 'SPRING', 'SOS', 'JOC', 'LPS'])
const risk = new Set(['PSY', 'BC', 'AR(D)', 'ST(D)', 'UTAD', 'SOW', 'LPSY'])
const creek = new Set(['AR', 'ST', 'TSO', 'JOC', 'LPS'])
const ice = new Set(['AR(D)', 'ST(D)', 'LPSY', 'SOW'])
const normalizeCode = (code: string) => code.trim().toUpperCase()
export type EventCategory = 'accumulation' | 'risk' | 'other'
export type EventMarker = { key: string; runId: string; date: string; event: string; category: EventCategory; anchor: 'high' | 'low'; price: number; knownAt: string; confirmation: string; phase: string; strategy: string }
export type EvidenceLayer = { events: EventMarker[]; signals: Array<{ runId: string; date: string; label: string; strategy: string; knownAt: string; eligible: boolean }>; phases: Array<{ runId: string; start: string; end: string; phase: string }>; boundaries: Array<{ runId: string; label: string; start: EventMarker; end: EventMarker }>; excluded: Array<{ runId: string; reason: string }> }

export function buildEvidence(runs: ChartResearchRun[], bars: IndicatorBar[], datasetId: string | undefined, visibleThrough: string): EvidenceLayer {
  const byDate = new Map(bars.map(row => [row.event_date, row]))
  const output: EvidenceLayer = { events: [], signals: [], phases: [], boundaries: [], excluded: [] }
  for (const run of runs) {
    const source = run.result.source_date
    if (!datasetId || run.dataset_id !== datasetId || !source || !byDate.has(source) || source > visibleThrough) {
      output.excluded.push({ runId: run.id, reason: '样本不匹配、源日期无行情或所选研究晚于当前图表末端' }); continue
    }
    const indicator = run.result.indicator || {}, phase = typeof indicator.phase === 'string' ? indicator.phase : '未明'
    const confirmations = indicator.event_confirmation_map && typeof indicator.event_confirmation_map === 'object' ? indicator.event_confirmation_map as Record<string, unknown> : {}
    const chain = Array.isArray(indicator.event_chain) ? indicator.event_chain as Array<{ event?: unknown; date?: unknown }> : []
    const events: EventMarker[] = []
    const seen = new Set<string>()
    for (const node of chain) {
      if (typeof node.event !== 'string' || typeof node.date !== 'string') continue
      const bar = byDate.get(node.date), code = normalizeCode(node.event), key = `${run.id}:${code}:${node.date}`
      if (!bar || node.date > source || seen.has(key)) continue
      const category = accumulation.has(code) ? 'accumulation' : risk.has(code) ? 'risk' : 'other'
      const anchor = creek.has(code) ? 'high' : ice.has(code) ? 'low' : category === 'risk' ? 'high' : 'low'
      const price = value(bar[anchor])
      if (price === null || price <= 0) continue
      seen.add(key)
      events.push({ key, runId: run.id, date: node.date, event: node.event, category, anchor, price,
        knownAt: run.decision_at, confirmation: String(confirmations[node.event] || '未提供'), phase, strategy: run.strategy_id })
    }
    events.sort((a, b) => a.date.localeCompare(b.date) || a.event.localeCompare(b.event))
    output.events.push(...events)
    if (events.length) output.phases.push({ runId: run.id, start: events[0].date, end: events.at(-1)!.date, phase })
    for (const [label, codes] of [['小溪线', creek], ['冰线', ice]] as const) {
      const selected = events.filter(event => codes.has(normalizeCode(event.event)))
      const dates = new Map(selected.map(event => [event.date, event]))
      const ordered = [...dates.values()]
      if (ordered.length >= 2) output.boundaries.push({ runId: run.id, label, start: ordered[0], end: ordered.at(-1)! })
    }
    if (run.result.signal === true) output.signals.push({ runId: run.id, date: source,
      label: ['A', 'B', 'C'].includes(run.result.primary_signal || '') ? run.result.primary_signal! : '观察',
      strategy: run.strategy_id, knownAt: run.decision_at, eligible: run.result.draft_eligible === true })
  }
  return output
}
