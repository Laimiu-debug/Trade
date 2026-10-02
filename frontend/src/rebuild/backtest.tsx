import { AIContextButton } from './ai-launcher'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { BacktestAnalysis, BacktestDailyDetail, type AdvancedAnalysis } from './backtest-analysis'
import { ResearchTradeDownloads } from './research-table-downloads'
import { StrategyPresets } from './strategy-presets'
import { api } from './api'
import { navigateResearch, type ResearchNavigation } from './research-navigation'
import { useTaskDeepLink } from './task-deep-link'
import { StrategyParamFields, type Strategy } from './research'
import type { EventProfileCatalog } from './event-profiles'
import type { FrozenEventProfile } from './wyckoff-result'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
type Summary = { ending_assets: string; total_return: string; max_drawdown: string; trade_count: number; win_rate: string | null; quality_flags: string[] }
type Result = Summary & { advanced_analysis?: AdvancedAnalysis; initial_capital: string; signal_count: number; skipped_insufficient_cash: number; realized_pnl: string; limitations: string[]; trades: Array<{ date: string; side: string; quantity: number; price: string; fees: string; realized_pnl: string | null; signal_date?: string; reason?: string; known_at?: string; decision_at?: string; execution_at?: string }>; equity: Array<{ date: string; total_assets: string; cash: string; quantity: number }> }
type Run = { id: string; dataset_id: string; strategy_id: string; state: string; error: string | null; summary: Summary | null; result?: Result; created_at: string; config: { initial_capital: string; holding_bars: number; strict: boolean; stop_loss_pct?: string; take_profit_pct?: string; trailing_stop_pct?: string; event_profile?: FrozenEventProfile } }

const defaultFees = { commission_rate: '0.0003', minimum_commission: '5.00', sell_stamp_rate: '0.001', transfer_rate: '0.00001', cash_buffer: '0.00', slippage_rate: '0' }
const reasonLabel: Record<string, string> = { CLASSIC_SIGNAL_NEXT_OPEN: '经典策略退出（下一开盘）', STRATEGY_LONG_ELIGIBLE: '策略买入条件通过', CLOSE_STOP_LOSS: '已知收盘价触发止损', CLOSE_TAKE_PROFIT: '已知收盘价触发止盈', CLOSE_TRAILING_STOP: '已知收盘价触发跟踪止损', MAX_HOLDING_BARS: '到达最大持有期', SAMPLE_END: '样本末日平仓' }
const stateLabel: Record<string, string> = { queued: '排队中', running: '计算中', cancelling: '取消中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }

function EquityLine({ points }: { points: Result['equity'] }) {
  if (points.length < 2) return null
  const values = points.map(row => Number(row.total_assets))
  const low = Math.min(...values)
  const high = Math.max(...values)
  const spread = Math.max(high - low, 1)
  const path = values.map((value, index) => `${index ? 'L' : 'M'} ${(index * 760 / (values.length - 1)).toFixed(2)} ${(150 - (value - low) / spread * 140).toFixed(2)}`).join(' ')
  return <div className="backtest-chart"><svg viewBox="0 0 760 160" role="img" aria-label={`总资产曲线，从 ${points[0].date} 至 ${points.at(-1)?.date}`} preserveAspectRatio="none"><path d={path} fill="none" stroke="currentColor" strokeWidth="2.5" /></svg><div className="period-summary"><span>{points[0].date} · ¥ {points[0].total_assets}</span><span>{points.at(-1)?.date} · ¥ {points.at(-1)?.total_assets}</span></div></div>
}

export function BacktestEditor({ accountId, onNavigate }: { accountId?: string; onNavigate?: (next: ResearchNavigation) => void }) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [profileCatalog, setProfileCatalog] = useState<EventProfileCatalog | null>(null)
  const [profileId, setProfileId] = useState('')
  const [exits, setExits] = useState({ stop_loss_pct: '0', take_profit_pct: '0', trailing_stop_pct: '0' })
  const [strategy, setStrategy] = useState<Strategy | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [selectedId, setSelectedId] = useState(() => /^backtest:((?:[a-f0-9]{32}|[a-f0-9]{64}))$/.exec(new URLSearchParams(window.location.search).get('task') || '')?.[1] || '')
  const [selected, setSelected] = useState<Run | null>(null)
  const [datasetId, setDatasetId] = useState('')
  const [capital, setCapital] = useState('100000.00')
  const [holdingBars, setHoldingBars] = useState(5)
  const [positionPct, setPositionPct] = useState('0.95')
  const [strict, setStrict] = useState(true)
  const [advanced, setAdvanced] = useState(false)
  const [analysisSeed, setAnalysisSeed] = useState(20260926)
  const [analysisIterations, setAnalysisIterations] = useState(400)
  const [params, setParams] = useState<Record<string, string>>({})
  const [fees, setFees] = useState(defaultFees)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const refresh = useCallback(async () => {
    const next = await api<Run[]>('/backtests')
    setRuns(next)
    if (selectedId) setSelected(await api<Run>('/backtests/' + selectedId))
  }, [selectedId])
  useEffect(() => {
    Promise.all([api<Dataset[]>('/market/datasets'), api<Strategy[]>('/research/strategies'), api<EventProfileCatalog>('/research/event-profiles')])
      .then(([nextDatasets, strategies, profiles]) => {
        setDatasets(nextDatasets); setStrategies(strategies.filter(row => row.signal_params !== null && row.enabled_in_rebuild !== false)); setProfileCatalog(profiles); setProfileId(profiles.active_profile_id)
        setDatasetId(current => current || nextDatasets[0]?.id || '')
        const active = strategies.find(row => row.default_in_rebuild && row.enabled_in_rebuild !== false && row.signal_params !== null) || null
        setStrategy(active)
        setParams(active?.signal_params || {})
      }).catch(err => setError(err.message))
  }, [])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])
  useEffect(() => {
    if (!runs.some(row => row.state === 'queued' || row.state === 'running' || row.state === 'cancelling')) return
    const timer = window.setInterval(() => refresh().catch(err => setError(err.message)), 900)
    return () => window.clearInterval(timer)
  }, [runs, refresh])

  useTaskDeepLink('backtest', async id => { setSelectedId(id) }, '[aria-label="单股回测结果"]', setError)

  const usesProfile = ['wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'].includes(strategy?.id || '')

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    if (!strategy || !datasetId) return
    try {
      const run = await api<Run>('/backtests', 'POST', {
        dataset_id: datasetId, strategy_id: strategy.id,
        initial_capital: capital, holding_bars: holdingBars,
        max_position_pct: positionPct, strict, params, fee_config: fees, ...exits,
        advanced_analysis: advanced, analysis_seed: analysisSeed, analysis_iterations: analysisIterations,
        ...(usesProfile ? { event_profile_id: profileId, event_profile_revision: profileCatalog?.profiles.find(row => row.profile_id === profileId)?.revision } : {}),
      })
      setSelectedId(run.id)
      setNotice(run.state === 'queued' ? '回测已加入后台队列' : '相同输入的历史回测已找到')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '回测提交失败') }
  }

  async function action(run: Run, name: 'cancel' | 'retry') {
    setError(''); setNotice('')
    try {
      await api('/backtests/' + run.id + '/' + name, 'POST', {})
      setNotice(name === 'cancel' ? '取消请求已提交' : '回测已重新排队')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '操作失败') }
  }

  return <div className="two-col wide-left research-single-grid"><section className="card"><h2>历史回测任务</h2><p className="muted">任务在后台运行；刷新页面后仍可查看输入快照和结果。</p><div className="table-wrap"><table><thead><tr><th>创建时间</th><th>数据集</th><th>状态</th><th>收益</th><th>交易</th><th>操作</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td>{run.created_at.slice(0, 19)}</td><td>{run.dataset_id.slice(0, 12)}</td><td>{stateLabel[run.state] || run.state}</td><td>{run.summary ? `${(Number(run.summary.total_return) * 100).toFixed(2)}%` : '—'}</td><td>{run.summary?.trade_count ?? '—'}</td><td><button className="link-button" onClick={() => setSelectedId(run.id)}>查看</button>{(run.state === 'queued' || run.state === 'running') && <button className="link-button danger" onClick={() => action(run, 'cancel')}>取消</button>}{(run.state === 'failed' || run.state === 'cancelled') && <button className="link-button" onClick={() => action(run, 'retry')}>重试</button>}</td></tr>)}</tbody></table></div>{!runs.length && <p className="muted">暂无回测任务</p>}</section>
    <section className="card"><h2>创建单股回测</h2><p className="muted">开盘前已可得的日线决定入场，按开盘价成交。可设置持有期，以及根据已知收盘价触发、在下一开盘执行的止损 / 止盈。此为统一单股执行口径，多证券共享资金的研究请使用独立的组合回测页。</p>{error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}<form className="form" onSubmit={submit}><label className="field"><span>冻结行情样本</span><select value={datasetId} onChange={event => setDatasetId(event.target.value)} required>{datasets.map(row => <option key={row.id} value={row.id}>{row.symbol} · {row.first_date} 至 {row.last_date} · {row.id.slice(0, 12)}</option>)}</select></label><label className="field"><span>回测策略</span><select value={strategy?.id || ''} onChange={event => { const next = strategies.find(row => row.id === event.target.value) || null; setStrategy(next); setParams(next?.signal_params || {}) }}><option value="">请选择已启用的单股策略</option>{strategies.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>{usesProfile && <label className="field"><span>回测事件模板</span><select value={profileId} onChange={event => setProfileId(event.target.value)}>{profileCatalog?.profiles.map(row => <option key={row.profile_id} value={row.profile_id}>{row.name} · 修订 {row.revision}</option>)}</select></label>}<div className="form-grid"><label className="field"><span>初始资金</span><input type="number" min="0.01" step="0.01" value={capital} onChange={event => setCapital(event.target.value)} required /></label><label className="field"><span>持有 K 线数</span><input type="number" min="1" max="60" value={holdingBars} onChange={event => setHoldingBars(Number(event.target.value))} required /></label></div><label className="field"><span>最大仓位比例</span><input type="number" min="0.01" max="1" step="0.01" value={positionPct} onChange={event => setPositionPct(event.target.value)} required /></label><label className="check-field"><input type="checkbox" checked={strict} onChange={event => setStrict(event.target.checked)} />严格可得时间</label>{strategy && <StrategyPresets key={strategy.id} strategyId={strategy.id} params={params} onApply={setParams} accountId={accountId} />}<details><summary>策略参数</summary><div className="form-grid"><StrategyParamFields params={params} strategy={strategy || undefined} onChange={(key, value) => setParams(current => ({ ...current, [key]: value }))} /></div></details><details><summary>收盘触发退出</summary><p className="muted">0 表示关闭；0.1 表示 10%。按可得收盘价判断，不以盘中最高 / 最低价假设成交。</p><div className="form-grid">{Object.entries(exits).map(([key, value]) => <label className="field" key={key}><span>{{ stop_loss_pct: '固定止损比例', take_profit_pct: '固定止盈比例', trailing_stop_pct: '跟踪止损比例' }[key]}</span><input type="number" min="0" max={key === 'take_profit_pct' ? '1.5' : '0.5'} step="0.01" value={value} onChange={event => setExits(current => ({ ...current, [key]: event.target.value }))} /></label>)}</div></details><details><summary>费用与现金缓冲</summary><div className="form">{Object.entries(fees).map(([key, value]) => <label className="field" key={key}><span>{{ commission_rate: '佣金比例', minimum_commission: '最低佣金', sell_stamp_rate: '卖出印花税比例', transfer_rate: '过户费比例', cash_buffer: '现金缓冲', slippage_rate: '滑点比例（0.01 表示 1%）' }[key] || key}</span><input type="number" min="0" max={key === 'slippage_rate' ? '0.05' : undefined} step="any" value={value} onChange={event => setFees({ ...fees, [key]: event.target.value })} /></label>)}</div></details><details><summary>高级分析设置</summary><label className="check-field"><input type="checkbox" checked={advanced} onChange={event => setAdvanced(event.target.checked)} />生成风险、月收益、市况代理和蒙特卡洛分析</label>{advanced && <div className="form-grid"><label className="field">蒙特卡洛随机种子<input type="number" min="0" max="4294967295" step="1" value={analysisSeed} onChange={event => setAnalysisSeed(Number(event.target.value))} /></label><label className="field">抽样次数<input type="number" min="100" max="2000" step="1" value={analysisIterations} onChange={event => setAnalysisIterations(Number(event.target.value))} /></label></div>}</details><button className="button primary" disabled={!datasetId || !strategy}>提交后台回测</button></form></section>
    {selected && <section className="card span-all" aria-label="单股回测结果"><h2>回测报告 · {selected.id.slice(0, 16)}</h2><p>状态：{stateLabel[selected.state] || selected.state} · 样本：{selected.dataset_id} · 初始资金：¥ {selected.config.initial_capital} · 严格模式：{selected.config.strict ? '是' : '否'}</p>{selected.config.event_profile && <p className="muted">冻结事件模板：{selected.config.event_profile.snapshot.name} · 修订 {selected.config.event_profile.revision} · {selected.config.event_profile.sha256.slice(0, 12)}</p>}{selected.error && <p className="danger">{selected.error}</p>}{selected.result && <><AIContextButton source={{ page: 'backtests', artifact: { type: 'backtest', id: selected.id }, label: '单股回测 · ' + selected.id.slice(0, 12) }} /><ResearchTradeDownloads kind="single" id={selected.id} /><a className="button secondary" href={`/api/v1/backtests/${selected.id}/export.xlsx`} download>导出本次回测 Excel</a><div className="metrics"><div className="metric"><span>期末资产</span><strong>¥ {selected.result.ending_assets}</strong></div><div className="metric"><span>总收益</span><strong>{(Number(selected.result.total_return) * 100).toFixed(2)}%</strong></div><div className="metric"><span>最大回撤</span><strong>{(Number(selected.result.max_drawdown) * 100).toFixed(2)}%</strong></div><div className="metric"><span>完成交易</span><strong>{selected.result.trade_count}</strong></div></div><EquityLine points={selected.result.equity} /><BacktestDailyDetail key={selected.id} equity={selected.result.equity} trades={selected.result.trades} /><BacktestAnalysis value={selected.result.advanced_analysis} /><p className="muted">信号 {selected.result.signal_count} 次 · 胜率 {selected.result.win_rate === null ? '—' : `${(Number(selected.result.win_rate) * 100).toFixed(2)}%`} · 数据质量 {selected.result.quality_flags.join(', ') || '完整'}</p><div className="table-wrap"><table><thead><tr><th>日期</th><th>方向</th><th>数量</th><th>价格</th><th>费用</th><th>已实现盈亏</th><th>信号日</th><th>成交依据</th></tr></thead><tbody>{selected.result.trades.map((row, index) => <tr key={index}><td>{row.date}</td><td>{row.side === 'buy' ? '买入' : '卖出'}</td><td>{row.quantity}</td><td>{row.price}</td><td>¥ {row.fees}</td><td>{row.realized_pnl ?? '—'}</td><td>{row.signal_date ?? '—'}</td><td>{reasonLabel[row.reason || ''] || row.reason || '固定持有期'}<br /><small>已知：{row.known_at || '—'}<br />决策：{row.decision_at || '—'}<br />成交：{row.execution_at || '—'}</small></td></tr>)}</tbody></table></div><p className="muted">{selected.result.limitations.join('；')}</p></>}</section>}{selected?.state === 'succeeded' && <div className="research-next-actions span-all" aria-label="回测后续研究"><button className="button secondary" onClick={() => (onNavigate || navigateResearch)({ view: 'experiments', mode: 'single', sourceId: selected.id })}>用此回测创建参数实验</button><button className="button secondary" onClick={() => (onNavigate || navigateResearch)({ view: 'experiments', mode: 'single-wf', sourceId: selected.id })}>用此回测创建 Walk-forward</button><button className="button secondary" onClick={() => (onNavigate || navigateResearch)({ view: 'reports', mode: 'single', sourceId: selected.id })}>保存到单股报告库</button></div>}</div>
}
