import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { StrategyPresets } from './strategy-presets'
import { api } from './api'
import { useTaskDeepLink } from './task-deep-link'
import { WatchPoolPanel } from './watch-pool'
import { TrendLeadersPanel } from './trend-leaders'
import { LimitUpLadderPanel } from './limit-up-ladder'
import { SectorCapitalPanel } from './sector-capital'
import { AbnormalScanPanel } from './abnormal-scan'
import { SentimentValuationPanel } from './sentiment-valuation'
import { MatrixPoolPanel } from './matrix-pool'
import { ScreeningExports } from './screening-exports'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
type Candidate = { symbol: string; name?: string; dataset_id: string; as_of_date: string; score: number; ret40: number; turnover20: number | null; amount20: number | null; amplitude20: number; retrace20: number; trend_class: string; pullback_days: number; vol_slope20: number; up_down_volume_ratio: number; ai_confidence: number; quality_flags: string[] }
type Summary = Record<'input' | 'step1' | 'step2' | 'step3' | 'step4', number>
type Run = { id: string; created_at: string; as_of_date: string; summary: Summary; quality_flags: string[]; request?: { datasets: Array<{ dataset_id: string; float_shares: number | null; float_shares_as_of_date: string | null; float_shares_source_sha256: string | null }>; as_of_date?: string; return_window_days?: number; config?: Settings & { mode: 'strict' | 'loose' } }; result?: { pools: Record<keyof Summary, Candidate[]>; rejections: Record<string, { stage: string; reasons: string[] }>; excluded: Array<{ symbol: string; reason: string }>; metric_label: string } }
type UniverseJob = { id: string; kind: 'funnel' | 'b1' | 'trend' | 'ladder' | 'sector' | 'abnormal'; state: string; total: number; processed: number; candidates: number; skipped_or_failed: number; run_id: string | null; as_of_date: string; markets: string[]; max_bars: number; float_shares_error: string | null; recent_errors: Array<{ symbol: string; code: string }> }
type ResearchTab = 'b1' | 'funnel' | 'trend' | 'ladder' | 'sector' | 'abnormal' | 'valuation' | 'matrix'
const researchTabLabels: Record<ResearchTab, string> = { funnel: '四步漏斗', b1: 'B1 多周期', trend: '趋势龙头', ladder: '涨停梯队', sector: '板块资金', abnormal: '严重异动', valuation: '情绪估值', matrix: '矩阵信号' }
type B1Params = { vol_ratio: number; chg_limit: number; amp_limit_10cm: number; amp_limit_20cm: number; kdj_j_upper: number }
type B1Hit = { symbol: string; name?: string; dataset_id: string; close: number; change_pct: number; amplitude_pct: number; volume_ratio: number; kdj_j: number; weekly_macd: number; monthly_macd: number }
type B1Run = { id: string; as_of_date: string; total_scanned: number; hit_count: number; quality_flags: string[]; result?: { hits: B1Hit[]; excluded: Array<{ symbol: string; reason: string }>; b1_params: B1Params } }
type Step = 'step1' | 'step2' | 'step3' | 'step4'
type Settings = Record<Step, Record<string, number | boolean | string[]>>

const defaults: Settings = {
  step1: { rank_start: 1, top_n: 500, turnover_threshold: 0.05, amount_threshold: 5e8, amplitude_threshold: 0.03 },
  step2: { retrace_min: 0.05, retrace_max: 0.25, max_pullback_days: 3, min_ma10_above_ma20_days: 5, min_ma5_above_ma10_days: 3, max_price_vs_ma20: 0.08, require_above_ma20: true, allow_b_trend: false },
  step3: { min_vol_slope20: 0.05, min_up_down_volume_ratio: 1.3, max_pullback_volume_ratio: 0.9, allow_blowoff_top: false, allow_divergence_5d: false, allow_upper_shadow_risk: false, allow_degraded: false },
  step4: { final_top_n: 8, min_ai_confidence: 0.55, allowed_theme_stages: ['发酵中', '高潮'], allow_degraded: true },
}
const labels: Record<string, string> = {
  rank_start: '涨幅排名起始', top_n: '涨幅排名截止', turnover_threshold: '20 日平均换手率下限', amount_threshold: '20 日平均成交额下限（元）', amplitude_threshold: '20 日平均振幅下限',
  retrace_min: '20 日回撤下限', retrace_max: '20 日回撤上限', max_pullback_days: '最大回撤天数', min_ma10_above_ma20_days: 'MA10 高于 MA20 最少天数', min_ma5_above_ma10_days: 'MA5 高于 MA10 最少天数', max_price_vs_ma20: '价格偏离 MA20 上限', require_above_ma20: '要求价格不低于 MA20', allow_b_trend: '允许 B 类趋势',
  min_vol_slope20: '20 日量能斜率下限', min_up_down_volume_ratio: '上涨 / 下跌量比下限', max_pullback_volume_ratio: '回撤量比上限', allow_blowoff_top: '允许放量滞涨', allow_divergence_5d: '允许五日量价背离', allow_upper_shadow_risk: '允许长上影风险', allow_degraded: '允许指标质量降级',
  final_top_n: '最终保留数量', min_ai_confidence: '候选置信度下限（公式代理值）', allowed_theme_stages: '允许题材阶段',
}
const stageLabels: Record<keyof Summary, string> = { input: '输入池', step1: '流动性', step2: '图形', step3: '量能与风险', step4: '最终观察池' }
const b1Defaults: B1Params = { vol_ratio: 0.8, chg_limit: 3, amp_limit_10cm: 5, amp_limit_20cm: 8, kdj_j_upper: 50 }
const b1Labels: Record<keyof B1Params, string> = { vol_ratio: '当日量 / 20 日均量上限', chg_limit: '日涨跌幅绝对值上限 (%)', amp_limit_10cm: '10cm 振幅上限 (%)', amp_limit_20cm: '20cm 振幅上限 (%)', kdj_j_upper: '前一日 KDJ J 上限' }
const SCREENER_PREFS_KEY = 'trade-rebuild.screener-prefs.v1'
const SCREENER_LAST_RUN_KEY = 'trade-rebuild.screener-last-run.v1'
const B1_LAST_RUN_KEY = 'trade-rebuild.b1-last-run.v1'
function readLocal<T>(key: string, fallback: T): T {
  try { return JSON.parse(window.localStorage.getItem(key) || 'null') ?? fallback }
  catch { return fallback }
}
const reasonLabels: Record<string, string> = { RANK_WINDOW: '涨幅排名不在范围内', STEP1_CAP_400: '第一步最多保留 400 只', TURNOVER_MISSING: '缺流通股本，换手率未知', TURNOVER_BELOW_MIN: '换手率不足', AMOUNT_MISSING: '成交额缺失', AMOUNT_BELOW_MIN: '成交额不足', AMPLITUDE_BELOW_MIN: '振幅不足', RETRACE_OUTSIDE_RANGE: '回撤超出范围', PULLBACK_DAYS_ABOVE_MAX: '回撤天数超限', MA10_MA20_DAYS_BELOW_MIN: 'MA10 / MA20 天数不足', MA5_MA10_DAYS_BELOW_MIN: 'MA5 / MA10 天数不足', PRICE_VS_MA20_ABOVE_MAX: '偏离 MA20 过大', PRICE_BELOW_MA20: '价格低于 MA20', B_TREND_EXCLUDED: 'B 类趋势被排除', VOLUME_SLOPE_BELOW_MIN: '量能斜率不足', UP_DOWN_VOLUME_BELOW_MIN: '上涨 / 下跌量比不足', PULLBACK_VOLUME_ABOVE_MAX: '回撤量比过高', BLOWOFF_TOP_EXCLUDED: '放量滞涨风险', DIVERGENCE_EXCLUDED: '五日量价背离', UPPER_SHADOW_EXCLUDED: '长上影风险', DEGRADED_EXCLUDED: '指标质量降级', CONFIDENCE_BELOW_MIN: '候选置信度不足', THEME_STAGE_EXCLUDED: '题材阶段未允许', FINAL_TOP_N: '最终排名超出保留数量' }
const qualityLabels: Record<string, string> = { LATEST_BAR_BEFORE_AS_OF_DATE: '最新行情早于筛选日期（需核对休市或缺价）', HISTORICAL_AVAILABLE_AT_UNKNOWN: '历史可得时间未知', BARS_AFTER_DECISION_EXCLUDED: '已排除筛选日之后才可得的行情', FLOAT_SHARES_NOT_FOUND: '缺流通股本', FLOAT_SHARES_AS_OF_UNKNOWN: '股本历史日期未知', FLOAT_SHARES_FROM_FUTURE: '股本日期晚于筛选日', AMOUNT_NOT_FOUND: '成交额缺失', TDX_INPUTS_FROZEN_SEQUENTIALLY: '通达信文件逐只冻结，非同一瞬间快照' }
const qualityText = (flags: string[]) => flags.map(flag => qualityLabels[flag] || flag).join('、') || '—'

export function ScreenerEditor({ datasets, onPromoted, onOpenMarket, preferredStrategy }: { preferredStrategy?: string; datasets: Dataset[]; onPromoted: (id: string) => void; onOpenMarket: (datasetId: string) => void }) {
  const [researchTab, setResearchTab] = useState<ResearchTab>(() => readLocal('trade-rebuild.research-tab.v1', 'funnel' as ResearchTab))
  const [selectedIds, setSelectedIds] = useState<string[]>(() => readLocal(SCREENER_PREFS_KEY, { selectedIds: [] as string[] }).selectedIds || [])
  const [floatShares, setFloatShares] = useState<Record<string, string>>(() => readLocal(SCREENER_PREFS_KEY, { floatShares: {} as Record<string, string> }).floatShares)
  const [shareDates, setShareDates] = useState<Record<string, string>>(() => readLocal(SCREENER_PREFS_KEY, { shareDates: {} as Record<string, string> }).shareDates)
  const [shareHashes, setShareHashes] = useState<Record<string, string>>(() => readLocal(SCREENER_PREFS_KEY, { shareHashes: {} as Record<string, string> }).shareHashes)
  const [asOfDate, setAsOfDate] = useState(() => readLocal(SCREENER_PREFS_KEY, { asOfDate: new Date().toLocaleDateString('sv-SE') }).asOfDate)
  const [windowDays, setWindowDays] = useState(() => readLocal(SCREENER_PREFS_KEY, { windowDays: 40 }).windowDays)
  const [mode, setMode] = useState<'strict' | 'loose'>(() => readLocal(SCREENER_PREFS_KEY, { mode: 'strict' as const }).mode)
  const [settings, setSettings] = useState<Settings>(() => readLocal(SCREENER_PREFS_KEY, { settings: defaults }).settings)
  const [runs, setRuns] = useState<Run[]>([])
  const [universeJobs, setUniverseJobs] = useState<UniverseJob[]>([])
  const [universeMarkets, setUniverseMarkets] = useState<Array<'sh' | 'sz' | 'bj'>>(() => readLocal(SCREENER_PREFS_KEY, { universeMarkets: ['sh', 'sz', 'bj'] as Array<'sh' | 'sz' | 'bj'> }).universeMarkets)
  const [maxBars, setMaxBars] = useState(() => readLocal(SCREENER_PREFS_KEY, { maxBars: 360 }).maxBars)
  const [universeBusy, setUniverseBusy] = useState(false)
  const [b1Enabled, setB1Enabled] = useState(true)
  const [b1Params, setB1Params] = useState<B1Params>(() => readLocal(SCREENER_PREFS_KEY, { b1Params: b1Defaults }).b1Params)
  const [b1Runs, setB1Runs] = useState<B1Run[]>([])
  const [b1Selected, setB1Selected] = useState<B1Run | null>(null)
  const [b1Busy, setB1Busy] = useState(false)
  const [b1MarketBusy, setB1MarketBusy] = useState(false)
  const [b1MaxBars, setB1MaxBars] = useState(() => readLocal(SCREENER_PREFS_KEY, { b1MaxBars: 1000 }).b1MaxBars)
  const completedUniverseIds = useRef(new Set<string>())
  const [selected, setSelected] = useState<Run | null>(null)
  const [stage, setStage] = useState<keyof Summary>(() => readLocal(SCREENER_PREFS_KEY, { stage: 'step4' as keyof Summary }).stage)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [signalNotice, setSignalNotice] = useState('')
  useTaskDeepLink('universe', async id => {
    const job = await api<UniverseJob>(`/research/tdx-universe-jobs/${id}`)
    setResearchTab(job.kind === 'b1' ? 'b1' : job.kind)
    setSignalNotice(`已定位全市场任务 ${id.slice(0, 12)}`)
    if (job.run_id && job.kind === 'b1') await openB1(job.run_id)
    if (job.run_id && job.kind === 'funnel') await openRun(job.run_id)
  }, '[aria-label="筛选工作区"]', setError)
  const refresh = useCallback(() => api<Run[]>('/research/screener-runs').then(setRuns), [])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])
  useEffect(() => { try { window.localStorage.setItem('trade-rebuild.research-tab.v1', JSON.stringify(researchTab)) } catch { /* keep current tab */ } }, [researchTab])
  useEffect(() => {
    try { window.localStorage.setItem(SCREENER_PREFS_KEY, JSON.stringify({ selectedIds, floatShares, shareDates, shareHashes, asOfDate, windowDays, mode, settings, universeMarkets, maxBars, b1Params, b1MaxBars, stage })) }
    catch { /* A full browser quota must not prevent screening. */ }
  }, [selectedIds, floatShares, shareDates, shareHashes, asOfDate, windowDays, mode, settings, universeMarkets, maxBars, b1Params, b1MaxBars, stage])
  useEffect(() => {
    const lastId = readLocal<string | null>(SCREENER_LAST_RUN_KEY, null)
    if (lastId) api<Run>('/research/screener-runs/' + lastId).then(setSelected).catch(() => {})
  }, [])
  useEffect(() => { if (selected) try { window.localStorage.setItem(SCREENER_LAST_RUN_KEY, JSON.stringify(selected.id)) } catch { /* keep current result */ } }, [selected])
  useEffect(() => { if (preferredStrategy === 'b1_mtf_v1') setResearchTab('b1'); if (preferredStrategy === 'matrix_signal_v1') setResearchTab('matrix') }, [preferredStrategy])
  useEffect(() => { api<Array<{ id: string; enabled_in_rebuild?: boolean }>>('/research/strategies').then(rows => setB1Enabled(rows.find(row => row.id === 'b1_mtf_v1')?.enabled_in_rebuild !== false)).catch(err => setError(err.message)) }, [])
  useEffect(() => { api<B1Run[]>('/research/b1-runs').then(setB1Runs).catch(() => {}) }, [])
  useEffect(() => {
    const lastId = readLocal<string | null>(B1_LAST_RUN_KEY, null)
    if (lastId) api<B1Run>('/research/b1-runs/' + lastId).then(setB1Selected).catch(() => {})
  }, [])
  useEffect(() => { if (b1Selected) try { window.localStorage.setItem(B1_LAST_RUN_KEY, JSON.stringify(b1Selected.id)) } catch { /* keep current result */ } }, [b1Selected])
  useEffect(() => {
    let active = true
    const load = () => api<UniverseJob[]>('/research/tdx-universe-jobs').then(jobs => {
      if (!active) return
      setUniverseJobs(jobs.filter(job => job.kind === 'funnel' || job.kind === 'b1'))
      const newlyCompleted = jobs.filter(job => (job.kind === 'funnel' || job.kind === 'b1') && job.run_id && !completedUniverseIds.current.has(job.id))
      newlyCompleted.forEach(job => completedUniverseIds.current.add(job.id))
      if (newlyCompleted.some(job => job.kind === 'funnel')) refresh().catch(() => {})
      if (newlyCompleted.some(job => job.kind === 'b1')) api<B1Run[]>('/research/b1-runs').then(setB1Runs).catch(() => {})
    }).catch(() => {})
    load()
    const timer = window.setInterval(load, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [refresh])

  function setSetting(step: Step, field: string, value: number | boolean | string[]) {
    setSettings(current => ({ ...current, [step]: { ...current[step], [field]: value } }))
  }

  async function loadTdxShares() {
    setError('')
    try {
      const query = new URLSearchParams()
      selectedIds.forEach(id => {
        const dataset = datasets.find(item => item.id === id)
        if (dataset) query.append('symbol', dataset.symbol)
      })
      const result = await api<{ requested_values: Record<string, number>; missing: string[]; source: { sha256: string } }>(
        '/market/tdx-float-shares?' + query.toString())
      const values = { ...floatShares }, hashes = { ...shareHashes }
      selectedIds.forEach(id => {
        const symbol = datasets.find(item => item.id === id)?.symbol
        if (symbol && result.requested_values[symbol]) {
          values[id] = String(result.requested_values[symbol])
          hashes[id] = result.source.sha256
        }
      })
      setFloatShares(values); setShareHashes(hashes)
      if (result.missing.length) setError(`通达信股本未收录 ${result.missing.length} 只证券，请核对后人工填写。`)
    } catch (err) { setError(err instanceof Error ? err.message : '通达信股本读取失败') }
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true)
    try {
      const payload = { datasets: selectedIds.map(id => ({ dataset_id: id,
        float_shares: floatShares[id] ? Number(floatShares[id]) : null,
        float_shares_as_of_date: shareDates[id] || null,
        float_shares_source_sha256: shareHashes[id] || null })),
      as_of_date: asOfDate, return_window_days: windowDays,
      config: { mode, ...settings } }
      const run = await api<Run>('/research/screener-runs', 'POST', payload)
      setSelected(run); setStage('step4'); await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '筛选运行失败') }
    finally { setBusy(false) }
  }

  async function openRun(id: string) {
    try { setSelected(await api<Run>('/research/screener-runs/' + id)); setStage('step4') }
    catch (err) { setError(err instanceof Error ? err.message : '筛选记录读取失败') }
  }

  function copySelectedParameters() {
    const request = selected?.request
    if (!request?.config) return
    setMode(request.config.mode)
    setSettings({ step1: request.config.step1, step2: request.config.step2,
                  step3: request.config.step3, step4: request.config.step4 })
    if (request.as_of_date) setAsOfDate(request.as_of_date)
    if (request.return_window_days) setWindowDays(request.return_window_days)
  }

  async function submitUniverse() {
    setError(''); setUniverseBusy(true)
    try {
      const job = await api<UniverseJob>('/research/tdx-universe-jobs', 'POST', {
        markets: universeMarkets, as_of_date: asOfDate,
        return_window_days: windowDays, max_bars: maxBars,
        config: { mode, ...settings },
      })
      setUniverseJobs(current => [job, ...current])
    } catch (err) { setError(err instanceof Error ? err.message : '全市场任务提交失败') }
    finally { setUniverseBusy(false) }
  }

  async function cancelUniverse(id: string) {
    try {
      const job = await api<UniverseJob>(`/research/tdx-universe-jobs/${id}/cancel`, 'POST', {})
      setUniverseJobs(current => current.map(item => item.id === id ? job : item))
    } catch (err) { setError(err instanceof Error ? err.message : '取消全市场任务失败') }
  }

  async function submitB1() {
    setError(''); setB1Busy(true)
    try {
      const run = await api<B1Run>('/research/b1-runs', 'POST', {
        dataset_ids: selectedIds, as_of_date: asOfDate, b1_params: b1Params,
      })
      setB1Selected(run)
      setB1Runs(await api<B1Run[]>('/research/b1-runs'))
    } catch (err) { setError(err instanceof Error ? err.message : 'B1 扫描失败') }
    finally { setB1Busy(false) }
  }

  async function openB1(id: string) {
    setResearchTab('b1')
    try { setB1Selected(await api<B1Run>('/research/b1-runs/' + id)) }
    catch (err) { setError(err instanceof Error ? err.message : 'B1 记录读取失败') }
  }

  async function promoteB1(symbol: string) {
    if (!b1Selected) return
    setError(''); setSignalNotice('')
    try {
      const signal = await api<{ id: string }>(`/research/b1-runs/${b1Selected.id}/signals/${symbol}`, 'POST', {})
      setSignalNotice(`${symbol} 已生成可复核的观察信号。将在单股研究页打开，请核对依据后填写模拟委托草稿。`)
      onPromoted(signal.id)
    } catch (err) { setError(err instanceof Error ? err.message : 'B1 信号建立失败') }
  }

  async function promoteFinalist(datasetId: string, symbol: string) {
    if (!selected) return
    setError(''); setSignalNotice('')
    try {
      const signal = await api<{ id: string }>(`/research/screener-runs/${selected.id}/signals/${datasetId}`, 'POST', {})
      setSignalNotice(`${symbol} 已生成可复核的最终观察信号。将在单股研究页打开，请核对依据后填写模拟委托草稿。`)
      onPromoted(signal.id)
    } catch (err) { setError(err instanceof Error ? err.message : '漏斗观察信号建立失败') }
  }

  async function submitB1Market() {
    setError(''); setB1MarketBusy(true)
    try {
      const job = await api<UniverseJob>('/research/tdx-b1-jobs', 'POST', {
        markets: universeMarkets.filter(market => market !== 'bj'), as_of_date: asOfDate,
        max_bars: b1MaxBars, b1_params: b1Params,
      })
      setUniverseJobs(current => [job, ...current])
    } catch (err) { setError(err instanceof Error ? err.message : '全市场 B1 任务提交失败') }
    finally { setB1MarketBusy(false) }
  }

  return <section className="card span-all" aria-label="筛选工作区"><h2>策略扫描</h2><div role="tablist" aria-label="策略扫描模块" className="research-tabs">{(Object.keys(researchTabLabels) as ResearchTab[]).map(tab => <button key={tab} type="button" role="tab" aria-selected={researchTab === tab} className={researchTab === tab ? 'button primary' : 'button secondary'} onClick={() => setResearchTab(tab)}>{researchTabLabels[tab]}</button>)}</div>
    {researchTab === 'funnel' && <><h3>四步选股漏斗</h3><p className="muted">使用明确选择的冻结日线样本。缺流通股本或成交额的证券留在输入池，并标出原因，不会以估计值通过第一步。候选置信度是旧版公式代理值，不是 AI 模型评分；历史数据可得时间未知会标记质量限制。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    {signalNotice && <div className="alert" role="status">{signalNotice}</div>}
    <form className="form" onSubmit={submit}><div className="form-grid"><label className="field"><span>截至日期</span><input type="date" value={asOfDate} onChange={event => setAsOfDate(event.target.value)} required /></label><label className="field"><span>涨幅窗口（交易日）</span><input type="number" min="5" max="120" value={windowDays} onChange={event => setWindowDays(Number(event.target.value))} required /></label><label className="field"><span>筛选模式</span><select value={mode} onChange={event => setMode(event.target.value as 'strict' | 'loose')}><option value="strict">严格</option><option value="loose">宽松</option></select></label></div>
      <h3>选择冻结行情与流通股本</h3><p className="muted">单次最多 100 个不同证券；需至少 251 根原始日线。流通股本单位为股。可从已配置的通达信目录读取，文件哈希随运行保存；通达信文件没有可靠的历史可得日期，仍需人工核对。</p><button type="button" className="button secondary" onClick={loadTdxShares} disabled={!selectedIds.length}>读取所选证券的通达信股本</button><div className="table-wrap"><table><thead><tr><th>加入</th><th>证券 / 数据</th><th>流通股本（股）</th><th>股本已知日期</th><th>来源</th></tr></thead><tbody>{datasets.map(row => <tr key={row.id}><td><input type="checkbox" aria-label={`选择 ${row.symbol} ${row.id.slice(0, 8)}`} checked={selectedIds.includes(row.id)} onChange={event => setSelectedIds(current => event.target.checked ? [...current, row.id] : current.filter(id => id !== row.id))} /></td><td>{row.symbol}<br /><small className="muted">{row.first_date} 至 {row.last_date} · {row.id.slice(0, 10)}</small></td><td><input type="number" min="1" step="1" aria-label={`${row.symbol} 流通股本`} value={floatShares[row.id] || ''} onChange={event => { setFloatShares(current => ({ ...current, [row.id]: event.target.value })); setShareHashes(current => ({ ...current, [row.id]: '' })) }} /></td><td><input type="date" aria-label={`${row.symbol} 股本已知日期`} value={shareDates[row.id] || ''} onChange={event => setShareDates(current => ({ ...current, [row.id]: event.target.value }))} /></td><td>{shareHashes[row.id] ? `通达信 ${shareHashes[row.id].slice(0, 10)}` : '人工输入'}</td></tr>)}</tbody></table></div>{!datasets.length && <p className="muted">请先在行情页导入冻结日线。</p>}
      {(['step1', 'step2', 'step3', 'step4'] as Step[]).map(stepKey => <details key={stepKey}><summary>{stageLabels[stepKey]}参数</summary><div className="form-grid">{Object.entries(settings[stepKey]).map(([field, value]) => field === 'allowed_theme_stages' ? <fieldset key={field}><legend>{labels[field]}</legend>{(['发酵中', '高潮', '退潮', 'Unknown'] as const).map(stageName => <label className="check-field" key={stageName}><input type="checkbox" checked={(value as string[]).includes(stageName)} onChange={event => setSetting(stepKey, field, event.target.checked ? [...(value as string[]), stageName] : (value as string[]).filter(item => item !== stageName))} />{stageName}</label>)}</fieldset> : typeof value === 'boolean' ? <label className="check-field" key={field}><input type="checkbox" checked={value} onChange={event => setSetting(stepKey, field, event.target.checked)} />{labels[field]}</label> : <label className="field" key={field}><span>{labels[field]}</span><input type="number" step="any" value={value} onChange={event => setSetting(stepKey, field, Number(event.target.value))} /></label>)}</div></details>)}
      <button className="button primary" disabled={busy || selectedIds.length < 1 || selectedIds.length > 100}>{busy ? '正在筛选…' : `运行漏斗（${selectedIds.length} 只）`}</button>
      <h3>通达信全市场自动输入池</h3><p className="muted">扫描所选市场的 A 股日线文件，逐只冻结为新样本并保留来源哈希；后台任务可跨页面及重启恢复。默认保存每股最近 360 根 K 线；查询更早日期时可提高冻结数量。首次全市场运行会占用较多本地磁盘。</p><div className="form-grid"><fieldset><legend>市场</legend>{(['sh', 'sz', 'bj'] as const).map(market => <label key={market} className="check-field"><input type="checkbox" checked={universeMarkets.includes(market)} onChange={event => setUniverseMarkets(current => event.target.checked ? [...current, market] : current.filter(item => item !== market))} />{({ sh: '上海', sz: '深圳', bj: '北京' } as const)[market]}</label>)}</fieldset><label className="field"><span>每股冻结 K 线数量</span><input type="number" min="251" max="2000" value={maxBars} onChange={event => setMaxBars(Number(event.target.value))} /></label></div><button type="button" className="button secondary" disabled={universeBusy || universeMarkets.length === 0 || maxBars < 251 || maxBars > 2000} onClick={submitUniverse}>{universeBusy ? '正在扫描本地目录…' : '启动全市场筛选任务'}</button></form>
    <h3>历史筛选记录</h3><div className="table-wrap"><table><thead><tr><th>日期</th><th>输入</th><th>四步结果</th><th>质量</th><th>查看</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td>{run.as_of_date}<br /><small className="muted">{run.id.slice(0, 12)}</small></td><td>{run.summary.input}</td><td>{run.summary.step1} → {run.summary.step2} → {run.summary.step3} → {run.summary.step4}</td><td>{qualityText(run.quality_flags)}</td><td><button className="link-button" onClick={() => openRun(run.id)}>查看</button></td></tr>)}</tbody></table></div>{!runs.length && <p className="muted">暂无漏斗运行记录</p>}
    <WatchPoolPanel input={selected?.result?.pools.input || []} current={selected?.result?.pools[stage] || []} b1Symbols={b1Selected?.result?.hits.map(hit => hit.symbol) || []} onOpenMarket={onOpenMarket} />
    {selected?.result && <div className="market-detail"><h3>筛选结果 · {selected.id.slice(0, 16)}</h3>{selected.request?.config && <button type="button" className="button secondary" onClick={copySelectedParameters}>复制此记录的筛选参数到表单</button>}<p>{(Object.keys(stageLabels) as Array<keyof Summary>).map(key => <button type="button" className={stage === key ? 'button primary' : 'button secondary'} key={key} onClick={() => setStage(key)}>{stageLabels[key]} {selected.summary[key]}</button>)}</p><ScreeningExports key={`${selected.id}:${stage}`} kind="funnel" runId={selected.id} stage={stage} stageLabel={stageLabels[stage]} rows={selected.result.pools[stage]} /><div className="table-wrap"><table><thead><tr><th>证券</th><th>名称</th><th>样本</th><th>涨幅</th><th>换手</th><th>成交额</th><th>回撤</th><th>分数 / 候选置信度</th><th>未通过原因</th><th>质量</th><th>操作</th></tr></thead><tbody>{selected.result.pools[stage].map(row => <tr key={row.dataset_id}><td>{row.symbol}</td><td>{row.name || "—"}</td><td>{row.dataset_id.slice(0, 10)} · {row.as_of_date}</td><td>{(row.ret40 * 100).toFixed(2)}%</td><td>{row.turnover20 === null ? '缺失' : `${(row.turnover20 * 100).toFixed(2)}%`}</td><td>{row.amount20 === null ? '缺失' : row.amount20.toLocaleString()}</td><td>{(row.retrace20 * 100).toFixed(2)}%</td><td>{row.score} / {row.ai_confidence.toFixed(2)}</td><td>{selected.result?.rejections[row.dataset_id]?.reasons.map(reason => reasonLabels[reason] || reason).join('、') || '—'}</td><td>{qualityText(row.quality_flags)}</td><td><button type="button" className="link-button" onClick={() => onOpenMarket(row.dataset_id)}>查看 K 线</button>{stage === "step4" && <button type="button" className="link-button" onClick={() => promoteFinalist(row.dataset_id, row.symbol)}>转观察信号</button>}</td></tr>)}</tbody></table></div>{selected.request && <details><summary>输入股本来源</summary><ul>{selected.request.datasets.map(item => <li key={item.dataset_id}>{item.dataset_id.slice(0, 10)}：{item.float_shares?.toLocaleString() || '缺失'} 股；{item.float_shares_source_sha256 ? `通达信文件 ${item.float_shares_source_sha256.slice(0, 16)}` : '人工输入'}；已知日期 {item.float_shares_as_of_date || '未知'}</li>)}</ul></details>}{selected.result.excluded.length > 0 && <p className="muted">数据不足未入池：{selected.result.excluded.map(item => item.symbol).join('、')}</p>}</div>}</>}
    {researchTab === 'b1' && <>
      {error && <div className="alert error" role="alert">{error}</div>}{signalNotice && <div className="alert" role="status">{signalNotice}</div>}
      <div className="form-grid"><label className="field"><span>B1 截至日期</span><input type="date" value={asOfDate} onChange={event => setAsOfDate(event.target.value)} /></label><fieldset><legend>B1 全市场范围</legend>{(['sh', 'sz'] as const).map(market => <label key={market} className="check-field"><input type="checkbox" checked={universeMarkets.includes(market)} onChange={event => setUniverseMarkets(current => event.target.checked ? [...current, market] : current.filter(item => item !== market))} />{market === 'sh' ? '上海' : '深圳'}</label>)}</fieldset></div>
      <details><summary>选择 B1 冻结样本 · 已选 {selectedIds.length} 只</summary><div className="table-wrap"><table><thead><tr><th>选择</th><th>证券</th><th>冻结日期</th></tr></thead><tbody>{datasets.map(row => <tr key={row.id}><td><input type="checkbox" aria-label={`B1 样本 ${row.symbol} ${row.id.slice(0, 8)}`} checked={selectedIds.includes(row.id)} onChange={event => setSelectedIds(current => event.target.checked ? [...current, row.id] : current.filter(id => id !== row.id))} /></td><td>{row.symbol}</td><td>{row.first_date} → {row.last_date}</td></tr>)}</tbody></table></div></details>
    <h3>B1 多周期扫描</h3><StrategyPresets strategyId="b1_mtf_v1" params={Object.fromEntries(Object.entries(b1Params).map(([key, value]) => [key, String(value)]))} onApply={next => setB1Params(Object.fromEntries(Object.entries(next).map(([key, value]) => [key, Number(value)])) as B1Params)} />{!b1Enabled && <p className="muted">B1 策略已停用，新扫描请先在系统设置启用；历史记录保留。</p>}<p className="muted">月 MACD 多头、周 DIF 大于零、日均线与缩量、涨跌幅 / 振幅及 KDJ 勾头。每只股票至少需要截至日期前 900 根日线；运行记录保存参数、命中和数据不足原因。全市场任务扫描所选上海和深圳通达信目录，逐只冻结样本。</p><div className="form-grid">{(Object.keys(b1Defaults) as Array<keyof B1Params>).map(key => <label className="field" key={key}><span>{b1Labels[key]}</span><input type="number" step="any" value={b1Params[key]} onChange={event => setB1Params(current => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}<label className="field"><span>全市场每股冻结 K 线数量</span><input type="number" min="900" max="2000" value={b1MaxBars} onChange={event => setB1MaxBars(Number(event.target.value))} /></label></div><button type="button" className="button secondary" disabled={!b1Enabled || b1Busy || selectedIds.length < 1 || selectedIds.length > 100} onClick={submitB1}>{b1Busy ? '正在扫描…' : `运行 B1（已选 ${selectedIds.length} 只）`}</button> <button type="button" className="button secondary" disabled={!b1Enabled || b1MarketBusy || !universeMarkets.some(market => market !== 'bj') || b1MaxBars < 900 || b1MaxBars > 2000} onClick={submitB1Market}>{b1MarketBusy ? '正在扫描目录…' : '启动全市场 B1 任务'}</button>
    {b1Runs.length > 0 && <><h4>B1 历史记录</h4><div className="table-wrap"><table><thead><tr><th>日期</th><th>扫描</th><th>命中</th><th>查看</th></tr></thead><tbody>{b1Runs.map(run => <tr key={run.id}><td>{run.as_of_date}</td><td>{run.total_scanned}</td><td>{run.hit_count}</td><td><button className="link-button" onClick={() => openB1(run.id)}>查看</button></td></tr>)}</tbody></table></div></>}
    {b1Selected?.result && <div className="market-detail"><h4>B1 结果 · {b1Selected.id.slice(0, 12)}</h4><ScreeningExports key={b1Selected.id} kind="b1" runId={b1Selected.id} stage="hits" stageLabel="B1 命中" rows={b1Selected.result.hits} /><button type="button" className="button secondary" onClick={() => { setB1Params(b1Selected.result!.b1_params); setAsOfDate(b1Selected.as_of_date) }}>复制此 B1 记录的参数到表单</button><p>命中 {b1Selected.hit_count} / 扫描 {b1Selected.total_scanned}；数据不足 {b1Selected.result.excluded.length}。{qualityText(b1Selected.quality_flags)}</p><div className="table-wrap"><table><thead><tr><th>证券</th><th>名称</th><th>收盘</th><th>涨跌幅</th><th>振幅</th><th>量比</th><th>KDJ J</th><th>周 MACD</th><th>月 MACD</th><th>操作</th></tr></thead><tbody>{b1Selected.result.hits.map(hit => <tr key={hit.dataset_id}><td>{hit.symbol}</td><td>{hit.name || "—"}</td><td>{hit.close}</td><td>{hit.change_pct}%</td><td>{hit.amplitude_pct}%</td><td>{hit.volume_ratio}</td><td>{hit.kdj_j}</td><td>{hit.weekly_macd}</td><td>{hit.monthly_macd}</td><td><button type="button" className="link-button" onClick={() => onOpenMarket(hit.dataset_id)}>查看 K 线</button><button type="button" className="link-button" onClick={() => promoteB1(hit.symbol)}>转观察信号</button></td></tr>)}</tbody></table></div>{b1Selected.result.excluded.length > 0 && <details><summary>数据不足证券</summary>{b1Selected.result.excluded.map(item => <span key={item.symbol}>{item.symbol} </span>)}</details>}</div>}
    <p className="muted">全市场任务在后台继续，进度与取消操作见下方任务记录。</p></>}
    {(researchTab === 'funnel' || researchTab === 'b1') && <>    {universeJobs.length > 0 && <><h3>全市场任务进度</h3><div className="table-wrap"><table><thead><tr><th>任务</th><th>市场 / 日期</th><th>进度</th><th>状态</th><th>错误</th><th>操作</th></tr></thead><tbody>{universeJobs.map(job => <tr key={job.id}><td>{job.kind === 'b1' ? 'B1' : '漏斗'} · {job.id.slice(0, 8)}</td><td>{job.markets.join('、')} · {job.as_of_date}</td><td>{job.processed}/{job.total} · 命中 {job.candidates}</td><td>{({ queued: '排队中', running: '运行中', cancelling: '取消中', cancelled: '已取消', succeeded: '已完成', partial_failed: '部分失败', failed: '失败' } as Record<string, string>)[job.state] || job.state}</td><td>{job.float_shares_error && <div>股本：{job.float_shares_error}</div>}{job.recent_errors.slice(0, 3).map(item => <div key={item.symbol}>{item.symbol}：{item.code}</div>)}</td><td>{['queued', 'running', 'cancelling'].includes(job.state) && <button className="link-button" onClick={() => cancelUniverse(job.id)}>取消</button>}{job.run_id && <button className="link-button" onClick={() => job.kind === 'b1' ? openB1(job.run_id!) : openRun(job.run_id!)}>查看结果</button>}</td></tr>)}</tbody></table></div></>}
</>}
    {researchTab === 'trend' && <TrendLeadersPanel datasetIds={selectedIds} onOpenMarket={onOpenMarket} />}
    {researchTab === 'ladder' && <LimitUpLadderPanel datasetIds={selectedIds} onOpenMarket={onOpenMarket} />}
    {researchTab === 'sector' && <SectorCapitalPanel />}
    {researchTab === 'abnormal' && <AbnormalScanPanel onOpenMarket={onOpenMarket} />}
    {researchTab === 'valuation' && <SentimentValuationPanel datasets={datasets} />}
    {researchTab === 'matrix' && <MatrixPoolPanel onOpenMarket={onOpenMarket} onPromoted={onPromoted} />}
  </section>
}
