import { sameMarketSymbol } from './market-symbols'
import { useMemo, useState } from 'react'
import { buildEvidence, calculateForceSeries, movingAverage, seriesPath, type ChartResearchRun, type ForcePoint } from './chart-indicators'
import { ChartResearchOverlay } from './chart-research-overlay'

export type ChartBar = { event_date: string; open: string; high: string; low: string; close: string; volume: number; amount?: string; available_at: string | null }
export type ChartExecution = { id: string; date: string; symbol: string; side: 'buy' | 'sell'; quantity: number; price: string; source: 'real' | 'sim' }

const WIDTH = 900
const PRICE_TOP = 18
const PRICE_BOTTOM = 258
const VOLUME_TOP = 286
const VOLUME_BOTTOM = 366

function ForceChart({ points, start, count }: { points: ForcePoint[]; start: number; count: number }) {
  const visible = points.slice(start, start + count), step = WIDTH / count
  const max = Math.max(1, ...visible.flatMap(row => [row.mainForce ?? 0, row.retailForce ?? 0])) * 1.12
  const y = (item: number) => 130 - item / max * 108
  const x = (index: number) => (index + 0.5) * step
  const last = visible.at(-1)
  return <div><h3>主力 / 散户量能</h3><p className="muted">主力 = EMA(MA(上涨量,3),3)，散户 = EMA(MA(下跌量,3),10)，量从股换为手。上涨/下跌按相邻收盘比较；各点只使用其之前的数据。此为旧指标名称，不代表真实机构或散户成交。</p>
    <div className="market-chart-legend"><span style={{ color: 'var(--chart-series3)' }}>主力上升柱</span><span style={{ color: 'var(--chart-series4)' }}>主力回落柱</span><span>主力实线</span><span style={{ color: 'var(--chart-series1)' }}>散户虚线</span><span>最后可见：主力 {last?.mainForce ?? '缺失'} 手 / 散户 {last?.retailForce ?? '缺失'} 手</span></div>
    <svg viewBox={`0 0 ${WIDTH} 160`} role="img" aria-label="主力与散户量能独立坐标" preserveAspectRatio="none"><line x1="0" x2={WIDTH} y1="130" y2="130" className="chart-grid" /><text x="2" y="12" className="chart-axis">{max.toFixed(2)} 手</text><text x="2" y="145" className="chart-axis">0</text>
      {visible.map((row, index) => row.mainForce === null ? null : <g key={row.date}><rect x={x(index) - Math.max(1, step * 0.3)} y={y(row.mainForce)} width={Math.max(2, step * 0.6)} height={130 - y(row.mainForce)} fill={row.mainForceState === 'falling' ? 'var(--chart-series4)' : 'var(--chart-series3)'} opacity="0.55" /><title>{row.date} 主力 {row.mainForce} 手，散户 {row.retailForce} 手；{row.mainForceState}</title>{(row.goldenCross || row.purpleToYellow) && <g role="img" aria-label={`${row.date} ${row.goldenCross ? '金叉' : ''}${row.purpleToYellow ? ' 紫转黄' : ''}`}><circle cx={x(index)} cy={Math.max(18, y(row.mainForce) - 8)} r="4" fill="var(--status-info)" /><text x={x(index) + 5} y={Math.max(21, y(row.mainForce) - 5)} fontSize="9" fill="var(--text-primary)">{row.goldenCross ? '金叉' : '转黄'}</text></g>}</g>)}
      <path d={seriesPath(points.map(row => row.mainForce), start, count, x, y)} fill="none" stroke="var(--text-primary)" strokeWidth="1.5" /><path d={seriesPath(points.map(row => row.retailForce), start, count, x, y)} fill="none" stroke="var(--chart-series1)" strokeDasharray="5 3" strokeWidth="1.5" />
    </svg><p className="muted">MA5/10/20 不足周期时留空；量能遵循旧版起始不足 3 日取已有均值。无效观测断线并重启量能序列，缺失不按零处理。</p></div>
}


export function MarketChart({ bars, symbol, quality, onOpenIntraday, manualStartDate, aiStartDate, executions = [], datasetId }: { datasetId?: string; bars: ChartBar[]; symbol: string; quality: string; onOpenIntraday?: (day: string) => void; manualStartDate?: string | null; aiStartDate?: string | null; executions?: ChartExecution[] }) {
  const [windowSize, setWindowSize] = useState(Math.min(60, bars.length))
  const [endIndex, setEndIndex] = useState(bars.length - 1)
  const [rangeStart, setRangeStart] = useState<number | null>(null)
  const [rangeEnd, setRangeEnd] = useState<number | null>(null)
  const [layers, setLayers] = useState({ ma: true, volume: true, force: true, accumulation: true, risk: true, other: true, phase: true, boundary: true, signals: true })
  const [researchRuns, setResearchRuns] = useState<ChartResearchRun[]>([])
  const force = useMemo(() => calculateForceSeries(bars), [bars])
  const averages = useMemo(() => ({ 5: movingAverage(bars, 5), 10: movingAverage(bars, 10), 20: movingAverage(bars, 20) }), [bars])
  const start = Math.max(0, endIndex - windowSize + 1)
  const visible = bars.slice(start, endIndex + 1)
  const visibleDates = new Set(visible.map(row => row.event_date))
  const visibleTrades = executions.filter(row => sameMarketSymbol(row.symbol, symbol) && visibleDates.has(row.date))
  const aiOffset = visible.findIndex(row => row.event_date === aiStartDate)
  const manualOffset = visible.findIndex(row => row.event_date === manualStartDate)
  const evidence = useMemo(() => buildEvidence(researchRuns, bars, datasetId, visible.at(-1)?.event_date || ''), [researchRuns, bars, datasetId, visible])
  if (!visible.length) return null
  const visibleMA = layers.ma ? Object.values(averages).flatMap(values => values.slice(start, endIndex + 1)).filter((item): item is number => item !== null) : []
  const low = Math.min(...visible.map(row => Number(row.low)), ...visibleMA)
  const high = Math.max(...visible.map(row => Number(row.high)), ...visibleMA)
  const span = Math.max(high - low, high * 0.01, 0.01)
  const top = high + span * 0.08
  const bottom = Math.max(0, low - span * 0.08)
  const priceY = (value: number) => PRICE_BOTTOM - (value - bottom) / (top - bottom) * (PRICE_BOTTOM - PRICE_TOP)
  const step = WIDTH / visible.length
  const volumeMax = Math.max(...visible.map(row => row.volume), 1)
  const selectedRange = rangeStart !== null && rangeEnd !== null
    ? [Math.min(rangeStart, rangeEnd), Math.max(rangeStart, rangeEnd)] : null
  const rangeBars = selectedRange ? bars.slice(selectedRange[0], selectedRange[1] + 1) : []
  const rangeVolume = rangeBars.reduce((total, row) => total + row.volume, 0)
  const rangeAmount = rangeBars.length && rangeBars.every(row => row.amount !== undefined)
    ? rangeBars.reduce((total, row) => total + BigInt(row.amount!.replace('.', '')), 0n) : null
  const rangeReturn = rangeBars.length > 0
    ? (Number(rangeBars.at(-1)?.close) / Number(rangeBars[0].close) - 1) * 100 : null

  function chooseBar(index: number) {
    if (rangeStart === null || rangeEnd !== null) { setRangeStart(index); setRangeEnd(null) }
    else setRangeEnd(index)
  }

  function maPath(period: 5 | 10 | 20): string {
    return seriesPath(averages[period], start, visible.length, offset => (offset + 0.5) * step, priceY)
  }
  const categoryColor = { accumulation: 'var(--chart-series1)', risk: 'var(--status-danger)', other: 'var(--chart-series4)' }
  const eventRows = evidence.events.filter(event => visibleDates.has(event.date))
  const phaseColor = (phase: string) => /accum|吸筹/i.test(phase) ? 'var(--chart-series1)' : /distri|派发/i.test(phase) ? 'var(--status-danger)' : 'var(--chart-series4)'

  return <div className="market-chart"><div className="period-controls"><strong>{symbol} · 冻结行情</strong><label>显示 K 线 <input aria-label="显示 K 线数量" type="range" min={Math.min(10, bars.length)} max={Math.min(200, bars.length)} value={windowSize} onChange={event => { const size = Number(event.target.value); setWindowSize(size); setEndIndex(current => Math.max(current, size - 1)) }} />{windowSize}</label><label>结束位置 <input aria-label="K 线结束位置" type="range" min={Math.min(windowSize - 1, bars.length - 1)} max={bars.length - 1} value={endIndex} onChange={event => { setEndIndex(Number(event.target.value)); setRangeStart(null); setRangeEnd(null) }} /></label></div><p className="muted">{visible[0].event_date} 至 {visible.at(-1)?.event_date} · {quality === 'provided_availability' ? '提供历史可得时间' : '历史可得时间未知'} · 点击两根 K 线可统计区间。</p><div className="market-chart-legend">{Object.entries({ ma: '均线 MA5/10/20', volume: '成交量', force: '主力 / 散户量能', accumulation: '吸筹事件', risk: '派发 / 风险事件', other: '其他事件', phase: '阶段区间', boundary: '小溪线 / 冰线', signals: '研究观察标记' }).map(([key, label]) => <label className="check-field" key={key}><input type="checkbox" checked={layers[key as keyof typeof layers]} onChange={event => setLayers(current => ({ ...current, [key]: event.target.checked }))} />{label}</label>)}</div>{datasetId && <ChartResearchOverlay datasetId={datasetId} onChange={setResearchRuns} />}{evidence.excluded.length > 0 && <p className="muted">{evidence.excluded.length} 个所选研究暂未绘制：源日期在当前图表末端之后或样本不匹配。</p>}<svg viewBox={`0 0 ${WIDTH} 390`} role="img" aria-label={`${symbol} 日 K 线、成交量及 5、10、20 日均线`} preserveAspectRatio="none"><line x1="0" x2={WIDTH} y1={PRICE_BOTTOM} y2={PRICE_BOTTOM} className="chart-grid" /><line x1="0" x2={WIDTH} y1={VOLUME_BOTTOM} y2={VOLUME_BOTTOM} className="chart-grid" />{layers.phase && evidence.phases.map(phase => {
      const left = Math.max(0, visible.findIndex(row => row.event_date >= phase.start))
      const right = visible.reduce((last, row, index) => row.event_date <= phase.end ? index : last, -1)
      return right >= left ? <rect key={phase.runId} role="img" aria-label={`研究阶段 ${phase.phase} ${phase.start} 至 ${phase.end}`} x={left * step} y={PRICE_TOP} width={(right - left + 1) * step} height={PRICE_BOTTOM - PRICE_TOP} fill={phaseColor(phase.phase)} opacity="0.1"><title>{phase.phase} · 仅所选研究事件跨度，非每日重新分类</title></rect> : null
    })}{visible.map((bar, offset) => {
      const index = start + offset
      const x = (offset + 0.5) * step
      const open = Number(bar.open), close = Number(bar.close)
      const up = close >= open
      const bodyTop = priceY(Math.max(open, close))
      const bodyHeight = Math.max(Math.abs(priceY(open) - priceY(close)), 1.5)
      const selected = selectedRange && index >= selectedRange[0] && index <= selectedRange[1]
      return <g key={bar.event_date} className={up ? 'candle up' : 'candle down'} tabIndex={0} role="button" aria-label={`${bar.event_date} 开 ${bar.open} 高 ${bar.high} 低 ${bar.low} 收 ${bar.close} 量 ${bar.volume}`} onClick={() => chooseBar(index)} onDoubleClick={() => onOpenIntraday?.(bar.event_date)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseBar(index) } else if (event.key === 'i' && onOpenIntraday) onOpenIntraday(bar.event_date) }}><rect x={offset * step} y="0" width={step} height="390" fill={selected ? 'var(--bg-selected)' : 'transparent'} /><line x1={x} x2={x} y1={priceY(Number(bar.high))} y2={priceY(Number(bar.low))} /><rect x={x - Math.max(1, step * 0.28)} y={bodyTop} width={Math.max(2, step * 0.56)} height={bodyHeight} />{layers.volume && <rect x={x - Math.max(1, step * 0.28)} y={VOLUME_BOTTOM - bar.volume / volumeMax * (VOLUME_BOTTOM - VOLUME_TOP)} width={Math.max(2, step * 0.56)} height={bar.volume / volumeMax * (VOLUME_BOTTOM - VOLUME_TOP)} opacity="0.55" />}<title>{bar.event_date} · 收 {bar.close} · 量 {bar.volume} · 额 {bar.amount ?? '未知'} · 可得 {bar.available_at || '未知'}</title>{offset % Math.max(1, Math.ceil(visible.length / 8)) === 0 && <text x={x} y="385" className="chart-axis">{bar.event_date.slice(5)}</text>}</g>
    })}{aiOffset >= 0 && <g role="img" aria-label={`AI 候选起爆日 ${aiStartDate}`}><line x1={(aiOffset + 0.5) * step} x2={(aiOffset + 0.5) * step} y1={PRICE_TOP} y2={PRICE_BOTTOM} stroke="var(--text-primary)" strokeDasharray="6 6" strokeWidth="2" /><text x={Math.min((aiOffset + 0.5) * step + 5, WIDTH - 100)} y={PRICE_TOP + 27} fill="var(--text-primary)" fontSize="12">AI 候选起爆日</text><title>模型候选日期，依据来自已冻结研究记录</title></g>}{layers.ma && <><path d={maPath(5)} className="chart-ma ma5" /><path d={maPath(10)} className="chart-ma ma10" /><path d={maPath(20)} className="chart-ma ma20" /></>}{layers.boundary && evidence.boundaries.map(line => {
      const a = visible.findIndex(row => row.event_date === line.start.date), b = visible.findIndex(row => row.event_date === line.end.date)
      if (a < 0 || b < 0) return null
      return <g key={line.runId + line.label} role="img" aria-label={`${line.label} ${line.start.date} 至 ${line.end.date}`}><line x1={(a + 0.5) * step} y1={priceY(line.start.price)} x2={(b + 0.5) * step} y2={priceY(line.end.price)} stroke={line.label === '小溪线' ? 'var(--chart-series1)' : 'var(--chart-series2)'} strokeDasharray="4 4" strokeWidth="1.5" /><title>{line.label} · 基于所选研究的首末事件锚点；两端须在当前范围</title></g>
    })}{eventRows.filter(row => layers[row.category]).map((event, index) => {
      const offset = visible.findIndex(row => row.event_date === event.date)
      const y = event.anchor === 'high' ? Math.max(PRICE_TOP + 10, priceY(event.price) - 10 - index % 3 * 13) : Math.min(PRICE_BOTTOM - 3, priceY(event.price) + 12 + index % 3 * 13)
      const label = `事件 ${event.event} ${event.date} · 确认状态 ${event.confirmation} · 已知截至 ${event.knownAt}`
      return <g key={event.key} role="img" aria-label={label}><circle cx={(offset + 0.5) * step} cy={y} r="4" fill={categoryColor[event.category]} /><text x={(offset + 0.5) * step + 5} y={y + 3} fontSize="9" fill={categoryColor[event.category]}>{event.event}</text><title>{label} · {event.strategy}</title></g>
    })}{layers.signals && evidence.signals.filter(row => visibleDates.has(row.date)).map((signal, index) => {
      const offset = visible.findIndex(row => row.event_date === signal.date), y = PRICE_TOP + 10 + index % 4 * 14
      return <g key={signal.runId} role="img" aria-label={`${signal.strategy} ${signal.label} ${signal.date} · 决策 ${signal.knownAt}`}><rect x={(offset + 0.5) * step - 7} y={y - 8} width="22" height="12" fill="var(--bg-surface)" stroke="var(--chart-series2)" /><text x={(offset + 0.5) * step + 4} y={y + 1} fontSize="9" textAnchor="middle" fill="var(--chart-series2)">{signal.label}</text><title>{signal.strategy} · {signal.eligible ? '允许进一步核对模拟草稿' : '仅观察，不能直接买入'} · 确认截至 {signal.knownAt}</title></g>
    })}{visibleTrades.map((trade, index) => {
      const offset = visible.findIndex(row => row.event_date === trade.date)
      const bar = visible[offset]
      const sameDaySideIndex = visibleTrades.slice(0, index).filter(row => row.date === trade.date && row.side === trade.side).length
      const x = (offset + 0.5) * step
      const y = trade.side === 'buy'
        ? Math.min(VOLUME_TOP - 9, priceY(Number(bar.low)) + 12 + sameDaySideIndex * 14)
        : Math.max(PRICE_TOP + 8, priceY(Number(bar.high)) - 12 - sameDaySideIndex * 14)
      const label = `${trade.source === 'sim' ? '模拟' : '实盘'} ${trade.date} ${trade.side === 'buy' ? '买入' : '卖出'} ${trade.quantity} 股，成交价 ${trade.price}`
      return <g key={trade.id} role="img" aria-label={label} className={trade.side === 'buy' ? 'trade-marker buy' : 'trade-marker sell'}><circle cx={x} cy={y} r="7" /><text x={x} y={y + 3.5} textAnchor="middle">{trade.side === 'buy' ? '买' : '卖'}</text><title>{label}</title></g>
    })}{manualOffset >= 0 && <><line x1={(manualOffset + 0.5) * step} x2={(manualOffset + 0.5) * step} y1={PRICE_TOP} y2={VOLUME_BOTTOM} stroke="var(--brand-accent)" strokeDasharray="5 4" strokeWidth="2" /><text x={(manualOffset + 0.5) * step + 4} y="14" className="chart-axis">人工启动日</text></>}</svg>{layers.force && <ForceChart points={force} start={start} count={visible.length} />}{eventRows.length > 0 && <details><summary>事件来源与确认状态（{eventRows.length} 条）</summary><p className="muted">图中是所选研究决策时已保存的事件快照，不表示这些确认状态在事件发生日已经可知。移动到研究源日期之前会隐藏整份快照；缺少对应交易日时不移到邻近日期。</p><div className="table-wrap"><table><thead><tr><th>事件 / 发生日</th><th>所选研究截至</th><th>确认状态</th><th>策略 / 运行</th></tr></thead><tbody>{eventRows.map(row => <tr key={row.key}><td>{row.event} · {row.date}</td><td>{row.knownAt}</td><td>{row.confirmation}</td><td>{row.strategy}<br />{row.runId.slice(0, 12)}</td></tr>)}</tbody></table></div></details>}{aiStartDate && <p className="muted">AI 候选日：{aiStartDate}{aiOffset < 0 ? '（在当前可见区间之外）' : ' · 图中虚线仅表示所选分析记录'}</p>}{visibleTrades.length > 0 && <div className="market-chart-legend"><span>{visibleTrades[0].source === 'sim' ? '模拟' : '实盘'}买入 {visibleTrades.filter(row => row.side === 'buy').length}</span><span>{visibleTrades[0].source === 'sim' ? '模拟' : '实盘'}卖出 {visibleTrades.filter(row => row.side === 'sell').length}</span><span>仅显示当前账户已确认成交；行情与成交来源独立。</span></div>}{rangeBars.length > 0 && <div className="period-summary"><span>区间：{rangeBars[0].event_date} 至 {rangeBars.at(-1)?.event_date}</span><span>交易日：{rangeBars.length}</span><span>收盘涨跌：{rangeReturn?.toFixed(2)}%</span><span>最高：{Math.max(...rangeBars.map(row => Number(row.high))).toFixed(4)}</span><span>最低：{Math.min(...rangeBars.map(row => Number(row.low))).toFixed(4)}</span><span>成交量合计：{rangeVolume}</span><span>成交额合计：{rangeAmount === null ? '数据缺失' : `¥ ${rangeAmount / 100n}.${String(rangeAmount % 100n).padStart(2, '0')}`}</span><button className="link-button" onClick={() => { setRangeStart(null); setRangeEnd(null) }}>清除选区</button></div>}{rangeStart !== null && rangeEnd === null && <p className="muted">已选起点 {bars[rangeStart].event_date}，请再选终点。</p>}</div>
}
