import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { StrategyPresets } from './strategy-presets'
import { api } from './api'
import { navigateResearch, type ResearchNavigation } from './research-navigation'
import { type EventProfileCatalog } from './event-profiles'
import { WyckoffResult, type FrozenEventProfile } from './wyckoff-result'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
export type ParamSchema = { type: 'number' | 'integer' | 'boolean' | 'enum'; title?: string; minimum?: number; maximum?: number; options?: string[] }
export type Strategy = { id: string; name: string; version: string; status: string; signal_params: Record<string, string> | null; params_schema: Record<string, ParamSchema>; limitations: string[]; capabilities: Record<string, boolean>; enabled_in_legacy: boolean; is_legacy_default: boolean; enabled_in_rebuild?: boolean; default_in_rebuild?: boolean; execution_entry?: string; description: string; family_id?: string; family_name?: string; variant_name?: string; origin?: string; execution_paths?: string[]; execution_semantics?: Record<string, string>; availability?: string; legacy_status?: string; legacy_limitations?: string[] }
type Run = { id: string; dataset_id: string; strategy_id: string; strategy_version: string; decision_at: string; strict: boolean; params: Record<string, string | boolean>; result: { status: string; signal: boolean | null; shape_signal?: boolean | null; draft_eligible?: boolean; draft_block_reason?: string | null; event_profile?: FrozenEventProfile; event_age_days?: Record<string, number>; code_sha256?: string; calculation_version?: string; source_date: string | null; quality_flags: string[]; candidate: Record<string, string | number> | null; score: null; executable_date: null; indicator?: Record<string, unknown>; universe?: { status: string; passed: boolean; reasons: string[]; checks: Array<{ parameter: string; actual: number | boolean; threshold: number | boolean; passed: boolean; reason: string | null }>; source_path: string }; evaluation?: Record<string, unknown> }; created_at: string }
type SimAccount = { id: string; name: string }
type DraftSizing = { quantity: number; estimated_fees: string; required_cash: string; spendable_cash: string; cash_gap: string; max_affordable_quantity: number; can_create: boolean; note: string; quote_sha256?: string; denominator_assets?: string; valuation_date?: string; requested_budget?: string }
const labels: Record<string, string> = { min_ret40: '40 日涨幅下限', max_retrace20: '20 日回撤上限', min_up_down_volume_ratio: '涨跌量比下限', min_vol_slope20: '量能斜率下限', min_ai_confidence: '候选置信度下限', min_day_gain_a: '模式 A 当日涨幅下限 %', min_hist: '历史弹性下限 %', min_gain_3d_b: '模式 B 三日涨幅下限 %', min_gain_5d_b: '模式 B 五日涨幅下限 %', max_vol_ratio_c: '模式 C 量比上限', min_hist_c: '模式 C 历史弹性下限 %', min_prev_gain_c: '模式 C 前日涨幅下限 %', min_day_gain: '当日涨幅下限 %', min_volume_ratio_prev: '相对昨日放量倍数', min_volume_ratio_ma5: '相对五日均量倍数', sector_rank_weight: '板块排序权重（当前无板块数据）', lookback_days: '量能信号回溯交易日', min_score: '指标分下限', convergence_threshold_pct: '收敛带宽上限', pre_convergence_min_spread_pct: '收敛前带宽下限', min_current_spread_pct: '当前带宽下限', min_spread_expansion_multiple: '带宽扩张倍数下限', min_volume_ratio20: '20 日量比下限', min_breakout_return_pct: '突破涨幅下限', max_convergence_age_days: '收敛距今天数上限', min_rising_ma_count: '上行均线数量下限' }
const qualityLabels: Record<string, string> = { classic_reference_variant: '经典策略参考变体', historical_availability_unknown: '历史可得时间未知', sector_context_unavailable: '缺少同一时点板块上下文', historical_elasticity_short_window: '历史弹性窗口不足 250 日', st_status_unknown: 'ST 状态未知', ma5_volume_unavailable: '五日均量不足', indicator_warmup_short: '指标预热样本偏短', candidate_universe_filter_not_run: '候选入池条件未执行', candidate_universe_insufficient_data: '候选历史样本不足', candidate_universe_rejected: '候选入池条件未通过', MATRIX_PLUGIN_APPROXIMATION: '旧矩阵插件近似指标' }

function ResearchIndicator({ run }: { run: Run }) {
  const item = run.result.indicator
  if (run.strategy_id.startsWith('classic_')) {
    const evaluation = run.result.evaluation || {}
    const reasonNames: Record<string, string> = { DONCHIAN_PRIOR_HIGH_BREAKOUT: '收盘突破此前高点通道', DONCHIAN_PRIOR_LOW_BREAKDOWN: '收盘跌破此前低点通道', SMA_TREND_ABOVE: '收盘高于均线及入场缓冲', SMA_TREND_BELOW: '收盘低于均线及退出缓冲', BOLLINGER_LOWER_REENTRY: '前日低于下轨，本日回到下轨至中轨之间', BOLLINGER_MIDDLE_REACHED: '收盘达到或高于中轨', INSUFFICIENT_CLASSIC_HISTORY: '已知历史不足，不能判断', NO_CURRENT_VOLUME: '当前日成交量为零', NO_CLASSIC_TRIGGER: '未满足当前入场或退出条件' }
    const metricNames: Record<string, string> = { close: '已知收盘价', volume: '成交量（股）', prior_entry_high: '此前入场通道高点', prior_exit_low: '此前退出通道低点', entry_window_start: '入场通道起始日', exit_window_start: '退出通道起始日', channel_end: '通道截止日（不含信号日）', sma: '趋势均线', entry_threshold: '入场阈值', exit_threshold: '退出阈值', lower: '布林下轨', middle: '布林中轨', upper: '布林上轨', population_stddev: '总体标准差', previous_close: '前日收盘价', previous_lower: '前日布林下轨', previous_middle: '前日布林中轨', previous_upper: '前日布林上轨', previous_population_stddev: '前日总体标准差' }
    const enough = run.result.status === 'computed'
    const reason = (value: unknown) => value ? reasonNames[String(value)] || String(value) : '—'
    return <section aria-label="经典策略信号依据"><h3>经典策略信号依据</h3><div className="period-summary"><span>入场信号：{!enough ? '数据不足' : run.result.signal ? '是' : '否'}</span><span>退出信号：{!enough ? '数据不足' : evaluation.exit_signal ? '是' : '否'}</span><span>已知日线：{String(evaluation.observed_bars ?? '—')} / 最少 {String(evaluation.required_bars ?? '—')} 根</span></div><p>入场依据：{reason(evaluation.entry_reason)}<br />退出依据：{reason(evaluation.exit_reason)}</p><p className="muted">{Array.isArray(evaluation.reasons) ? evaluation.reasons.map(reason).join('；') : ''}</p>{item && Object.keys(item).length > 0 && <div className="table-wrap"><table aria-label="经典策略阈值"><thead><tr><th>指标 / 阈值</th><th>冻结值</th></tr></thead><tbody>{Object.entries(item).map(([key, value]) => <tr key={key}><td>{metricNames[key] || key}</td><td>{String(value ?? '—')}</td></tr>)}</tbody></table></div>}<p className="muted">局部分数：{String(evaluation.local_score ?? '—')}。只描述当前策略的信号强度，不是跨策略排名；观察信号不会按该日收盘价倒填成交。</p>{evaluation.local_score_formula != null && <details><summary>局部分数公式</summary><code>{String(evaluation.local_score_formula)}</code></details>}</section>
  }
  if (!item) return null
  if (['wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'].includes(run.strategy_id)) return <WyckoffResult indicator={item} evaluation={run.result.evaluation} profile={run.result.event_profile} eventAge={run.result.event_age_days} />
  if (run.strategy_id === 'ths_force_rhythm_v1') return <div className="period-summary">
    <span>节奏波形成：{run.result.evaluation?.rhythm_pattern_formed ? '是' : '否'}</span>
    <span>周期数：{String(item.cycle_count ?? '—')}</span>
    <span>周期变异系数：{String(item.cycle_cv ?? '—')}</span>
    <span>波谷分位：{String(item.trough_percentile ?? '—')}</span>
    <span>主力量能：{String(item.main_force ?? '—')}</span>
    <span>散户卖出提示：{run.result.evaluation?.retail_sell_signal ? '是' : '否'}</span>
    <span>触发原因：{String(run.result.evaluation?.trigger_reason || '—')}</span>
    <span>指标分：{String(run.result.evaluation?.signal_score ?? '—')}</span>
  </div>
  if (run.strategy_id === 'wulong_cluster_v1') return <div className="period-summary">
    <span>形态触发：{run.result.shape_signal ?? run.result.signal ? '是' : '否'}</span><span>均线多头：{item.bullish_alignment ? '是' : '否'}</span>
    <span>收敛带宽：{String(item.convergence_spread_pct ?? '—')}</span>
    <span>当前带宽：{String(item.current_spread_pct ?? '—')}</span>
    <span>扩张倍数：{String(item.spread_expansion_multiple ?? '—')}</span>
    <span>20 日量比：{String(item.volume_ratio_20 ?? '—')}</span>
    <span>突破强度：{String(run.result.evaluation?.breakout_strength_pct ?? '—')}</span>
    <span>指标分：{String(run.result.evaluation?.signal_score ?? '—')}</span>
    <span>入池筛选：{!run.result.universe ? '旧记录未运行' : run.result.universe.passed ? '通过' : run.result.universe.status === 'insufficient_data' ? '数据不足' : '未通过'}</span><span>{run.result.universe?.reasons.join('、')}</span>
  </div>
  if (run.strategy_id === 'emotion_limit_up_v1') return <div className="period-summary">
    <span>涨停共振：{item.limit_up ? '是' : '否'}</span>
    <span>金叉：{item.has_golden_cross ? '是' : '否'}</span>
    <span>紫转黄：{item.has_purple_to_yellow ? '是' : '否'}</span>
    <span>信号年龄：{String(item.signal_age_days ?? '—')} 交易日</span>
    <span>当日涨幅：{String(item.day_gain ?? '—')}%</span>
    <span>指标分：{String(item.signal_score ?? '—')}</span>
    <span>板块排名：{item.sector_rank === 99 ? '缺失' : String(item.sector_rank)}</span>
  </div>
  if (run.strategy_id.startsWith('ths_main_force_')) return <div className="period-summary">
    <span>主力紫转黄：{item.purple_to_yellow ? '是' : '否'}</span>
    <span>主散金叉：{item.golden_cross ? '是' : '否'}</span>
    <span>主力量能：{String(item.main_force ?? '—')}</span>
    <span>散户量能：{String(item.retail_force ?? '—')}</span>
    <span>主力状态：{String(item.main_force_state ?? '—')}</span>
    <span>指标分：{String(item.signal_score ?? '—')}</span>
  </div>
  if (run.strategy_id === 'limit_up_arb_v1') return <div className="period-summary">
    <span>前日涨停：{item.prev_limit_up ? '是' : '否'}</span>
    <span>涨停日：{String(item.limit_up_date || '—')}</span>
    <span>当日涨幅：{String(item.day_gain ?? '—')}%</span>
    <span>相对前日放量：{String(item.volume_ratio_prev ?? '—')}</span>
    <span>相对五日均量：{String(item.volume_ratio_ma5 ?? '—')}</span>
    <span>指标分：{String(run.result.evaluation?.signal_score ?? '—')}</span>
    <span>旧策略信号价格：{String(item.entry_price ?? '—')}（当日收盘；历史观察信号不能倒填成交）</span>
  </div>
  return <div className="period-summary">
    <span>当日涨幅：{String(item.day_gain ?? '—')}%</span>
    <span>历史弹性：{String(item.max_hist ?? '—')}%</span>
    <span>量比：{String(item.vol_ratio ?? '—')}</span>
    <span>模式 A / B / C：{['a', 'b', 'c'].map(key => run.result.evaluation?.[`mode_${key}`] ? key.toUpperCase() : '—').join(' / ')}</span>
    <span>指标分：{String(run.result.evaluation?.signal_score ?? '—')}</span>
    <span>次日参考价：{String(item.buy_aggressive ?? '—')} / {String(item.buy_conservative ?? '—')}</span>
  </div>
}

export function StrategyParamFields({ params, strategy, onChange }: { params: Record<string, string>; strategy: Strategy | undefined; onChange: (key: string, value: string) => void }) {
  const fields = Object.entries(params).map(([key, value]) => {
    const spec = strategy?.params_schema[key]
    return <label className="field" key={key}><span>{spec?.title || labels[key] || key}</span>
      {spec?.type === 'boolean' ? <select value={value} onChange={event => onChange(key, event.target.value)}><option value="true">启用</option><option value="false">关闭</option></select> :
        spec?.type === 'enum' ? <select value={value} onChange={event => onChange(key, event.target.value)}>{spec.options?.map(option => <option key={option} value={option}>{option}</option>)}</select> : <input type="number" step={spec?.type === 'integer' ? '1' : 'any'} min={spec?.minimum} max={spec?.maximum} value={value} onChange={event => onChange(key, event.target.value)} />}
    </label>
  })
  if (strategy?.id === 'ths_force_rhythm_v1' || strategy?.id === 'wulong_cluster_v1') return <details><summary>策略参数 · {fields.length} 项</summary><div className="form-grid">{fields}</div></details>
  return <>{fields}</>
}

export function ResearchEditor({ simAccounts, onOpenSim, accountId, initialRunId, initialStrategyId, onRunSelected, onNavigate }: { initialStrategyId?: string; onRunSelected?: (id: string) => void; onNavigate?: (next: ResearchNavigation) => void; accountId?: string; initialRunId?: string | null; simAccounts: SimAccount[]; onOpenSim: (id: string) => void; onOpenMarket: (datasetId: string) => void }) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [profileCatalog, setProfileCatalog] = useState<EventProfileCatalog | null>(null)
  const [profileId, setProfileId] = useState('')
  const [runs, setRuns] = useState<Run[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [strategyId, setStrategyId] = useState('')
  const strategyChosen = useRef(false)
  const initialStrategyApplied = useRef('')
  const [params, setParams] = useState<Record<string, string>>({})
  const [decision, setDecision] = useState(() => {
    const now = new Date()
    return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
  })
  const [strict, setStrict] = useState(true)
  const [selected, setSelected] = useState<Run | null>(null)
  const [simAccountId, setSimAccountId] = useState(() => simAccounts[0]?.id || '')
  const [draftQuantity, setDraftQuantity] = useState(100)
  const [draftPrice, setDraftPrice] = useState('')
  const [sizeMode, setSizeMode] = useState<'lots' | 'amount' | 'cash_percent' | 'asset_percent'>('lots')
  const [sizeValue, setSizeValue] = useState('1')
  const [equityReports, setEquityReports] = useState<Array<{ id: string; date_to: string; wallet_revision: number; summary: { ending_assets: string | null } }>>([])
  const [equityReportId, setEquityReportId] = useState('')
  const [sizing, setSizing] = useState<DraftSizing | null>(null)
  const [draftSaved, setDraftSaved] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const refresh = useCallback(async () => {
    const [nextDatasets, nextStrategies, nextRuns, nextProfiles] = await Promise.all([
      api<Dataset[]>('/market/datasets'), api<Strategy[]>('/research/strategies'),
      api<Run[]>('/research/runs'), api<EventProfileCatalog>('/research/event-profiles'),
    ])
    setProfileCatalog(nextProfiles); setProfileId(current => nextProfiles.profiles.some(row => row.profile_id === current) ? current : nextProfiles.active_profile_id)
    setDatasets(nextDatasets); setStrategies(nextStrategies); setRuns(nextRuns)
    setDatasetId(current => current || nextDatasets[0]?.id || '')
    if (!strategyChosen.current) { const initial = nextStrategies.find(row => row.default_in_rebuild && row.enabled_in_rebuild !== false); setStrategyId(initial?.id || ''); setParams(initial?.signal_params || {}) }
  }, [])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])
  useEffect(() => { if (!simAccountId && simAccounts[0]) setSimAccountId(simAccounts[0].id) }, [simAccountId, simAccounts])
  useEffect(() => {
    if (!initialRunId) return
    let active = true
    setSelected(null)
    api<Run>(`/research/runs/${initialRunId}`).then(value => { if (active) { setSelected(value); setDraftSaved(false) } }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [initialRunId])
  useEffect(() => {
    let active = true
    setSizing(null); setEquityReportId(''); setEquityReports([])
    if (sizeMode === 'asset_percent' && simAccountId) {
      api<typeof equityReports>(`/sim-accounts/${simAccountId}/equity-reports`).then(rows => { if (active) setEquityReports(rows) }).catch(err => { if (active) setError(err.message) })
    }
    return () => { active = false }
  }, [simAccountId, sizeMode])
  useEffect(() => {
    if (!initialStrategyId || !strategies.length || initialStrategyApplied.current === initialStrategyId) return
    const requested = strategies.find(row => row.id === initialStrategyId && row.enabled_in_rebuild !== false)
    if (!requested) { setError('该策略不存在或已停用，请在目录重新选择。'); return }
    initialStrategyApplied.current = initialStrategyId
    strategyChosen.current = true
    setStrategyId(requested.id); setParams(requested.signal_params || {})
  }, [initialStrategyId, strategies])
  const strategy = strategies.find(row => row.id === strategyId)

  const usesProfile = ['wyckoff_trend_v1', 'wyckoff_trend_v2', 'score_only_rank_v1'].includes(strategyId)

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    if (!strategy || !datasetId) return
    try {
      const created = await api<Run>('/research/runs', 'POST', {
        dataset_id: datasetId, strategy_id: strategy.id,
        decision_at: new Date(decision).toISOString(), strict, params,
        ...(usesProfile ? { event_profile_id: profileId, event_profile_revision: profileCatalog?.profiles.find(row => row.profile_id === profileId)?.revision } : {}),
      })
      setSelected(created)
      onRunSelected?.(created.id)
      setDraftSaved(false)
      setNotice('研究输入和结果已按版本保存')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '研究运行失败') }
  }

  async function saveDraft(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    if (!selected?.result.signal || !simAccountId) return
    try {
      if (sizeMode === 'asset_percent') {
        if (!sizing?.quote_sha256 || !sizing.can_create || !equityReportId) throw new Error('请先选择资产报告并重新估算数量')
        await api(`/sim-accounts/${simAccountId}/equity-reports/drafts`, 'POST', {
          source_run_id: selected.id, report_id: equityReportId, percent: sizeValue,
          limit_price: draftPrice, expected_quote_sha256: sizing.quote_sha256,
        })
      } else {
        await api(`/sim-accounts/${simAccountId}/drafts`, 'POST', {
          source_run_id: selected.id, quantity: draftQuantity, limit_price: draftPrice,
        })
      }
      setDraftSaved(true)
      setNotice('观察信号已加入所选模拟账户的委托草稿')
    } catch (err) { setError(err instanceof Error ? err.message : '草稿保存失败') }
  }

  async function quoteSize() {
    setError(''); setSizing(null)
    if (!simAccountId || !draftPrice) return
    try {
      const quote = sizeMode === 'asset_percent'
        ? await api<DraftSizing>(`/sim-accounts/${simAccountId}/equity-reports/size`, 'POST', { report_id: equityReportId, percent: sizeValue, limit_price: draftPrice })
        : await api<DraftSizing>(`/sim-accounts/${simAccountId}/drafts/size`, 'POST', { mode: sizeMode, value: sizeValue, limit_price: draftPrice })
      setSizing(quote)
      if (quote.quantity > 0) setDraftQuantity(quote.quantity)
    } catch (err) { setError(err instanceof Error ? err.message : '数量估算失败') }
  }

  return <div className="two-col wide-left research-single-grid"><section className="card"><h2>研究运行记录</h2><p className="muted">每次运行固定行情哈希、策略版本、参数、决策时间和严格模式。</p><div className="table-wrap"><table><thead><tr><th>策略</th><th>数据集</th><th>决策时间</th><th>结果</th><th>质量</th><th>查看</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td>{run.strategy_id}<br /><small className="muted">{run.strategy_version}</small></td><td>{run.dataset_id.slice(0, 12)}</td><td>{run.decision_at}</td><td>{run.result.status === 'computed' ? run.result.signal ? run.result.draft_eligible === false ? '观察通过，不能买入' : '观察信号' : '未触发' : '数据不足'}</td><td>{run.result.quality_flags.length ? run.result.quality_flags.map(flag => qualityLabels[flag] || flag).join('、') : '已提供可得时间'}</td><td><button className="link-button" onClick={() => { setSelected(run); setDraftSaved(false); onRunSelected?.(run.id) }}>查看</button></td></tr>)}</tbody></table></div>{!runs.length && <p className="muted">暂无研究运行</p>}
    {selected && <div className="market-detail"><h3>运行结果 · {selected.id.slice(0, 16)}</h3><p>源日期：{selected.result.source_date ?? '—'} · 状态：{selected.result.status} · 观察信号：{selected.result.signal === null ? '—' : selected.result.signal ? '是' : '否'}</p><p className="muted">信号年龄、同策略排名与延迟入场观察请在信号工作区查看；此结果不会自动生成模拟成交。</p><p className="muted">严格可得时间：{selected.strict ? "是" : "否"} · 质量标记：{selected.result.quality_flags.map(flag => qualityLabels[flag] || flag).join("、") || "无"} · 计算版本：{selected.result.calculation_version || "旧记录未提供"} · 算法摘要：{selected.result.code_sha256?.slice(0, 12) || "旧记录未提供"}</p>{selected.result.candidate && <div className="period-summary">{Object.entries(selected.result.candidate).map(([key, value]) => <span key={key}>{key}: {value}</span>)}</div>}<ResearchIndicator run={selected} />{selected.result.universe && <details><summary>候选入池条件明细</summary><p className="muted">旧单股候选口径 · {selected.result.universe.source_path}</p><div className="table-wrap"><table><thead><tr><th>条件</th><th>当前值</th><th>阈值 / 开关</th><th>结果</th></tr></thead><tbody>{selected.result.universe.checks.map(check => <tr key={check.parameter}><td>{labels[check.parameter] || strategies.find(row => row.id === selected.strategy_id)?.params_schema[check.parameter]?.title || check.parameter}</td><td>{String(check.actual)}</td><td>{String(check.threshold)}</td><td>{check.passed ? '通过' : check.reason}</td></tr>)}</tbody></table></div></details>}{selected.result.signal && selected.result.draft_eligible === false && <p className="muted">{selected.result.draft_block_reason || '候选入池条件未通过或旧记录未执行入池核对，不能生成模拟委托草稿。'}</p>}{selected.result.signal && selected.result.draft_eligible !== false && <form className="form" onSubmit={saveDraft}><h3>加入模拟委托草稿</h3><p className="muted">观察信号仅供复核。草稿不占用资金；在模拟账户预览费用与资金后再提交限价委托。</p><label className="field"><span>模拟账户</span><select value={simAccountId} onChange={event => { setSimAccountId(event.target.value); setSizing(null); setDraftSaved(false) }} required>{simAccounts.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><div className="form-grid"><label className="field"><span>委托限价</span><input type="number" min="0.0001" step="0.0001" value={draftPrice} onChange={event => { setDraftPrice(event.target.value); setSizing(null) }} required /></label><label className="field"><span>换算方式</span><select value={sizeMode} onChange={event => { setSizeMode(event.target.value as typeof sizeMode); setSizing(null) }}><option value="lots">手数（100 股 / 手）</option><option value="amount">目标金额</option><option value="cash_percent">可用资金比例 %</option><option value="asset_percent">总资产比例 %（本次买入预算）</option></select></label><label className="field"><span>{sizeMode === 'lots' ? '手数' : sizeMode === 'amount' ? '金额（元）' : '比例（%）'}</span><input type="number" min="0.01" step={sizeMode === 'lots' ? '1' : '0.01'} value={sizeValue} onChange={event => { setSizeValue(event.target.value); setSizing(null) }} /></label><label className="field"><span>草稿数量（股）</span><input type="number" min="1" readOnly={sizeMode === 'asset_percent'} value={draftQuantity} onChange={event => { setDraftQuantity(Number(event.target.value)); setSizing(null) }} required /></label></div>{sizeMode === 'asset_percent' && <><label className="field"><span>总资产分母报告</span><select value={equityReportId} onChange={event => { setEquityReportId(event.target.value); setSizing(null) }}><option value="">请选择已保存完整估值</option>{equityReports.map(row => <option key={row.id} value={row.id}>{row.date_to} · ¥ {row.summary.ending_assets ?? '缺失'} · 钱包版本 {row.wallet_revision}</option>)}</select></label><p className="muted">请先在模拟账户保存当前日完整资产报告。总资产比例是本次买入预算（含费用），按可用现金封顶；缺价、旧价或钱包版本变化时需重新估值。</p></>}<div className="form-actions"><button type="button" className="button secondary" disabled={!simAccountId || !draftPrice || (sizeMode === 'asset_percent' && !equityReportId)} onClick={quoteSize}>估算数量与费用</button></div>{sizing && <div className="period-summary"><span>估算股数：{sizing.quantity}</span><span>最多可买：{sizing.max_affordable_quantity}</span><span>预计费用：¥ {sizing.estimated_fees}</span><span>所需资金：¥ {sizing.required_cash}</span><span>可支配：¥ {sizing.spendable_cash}</span><span>资金缺口：¥ {sizing.cash_gap}</span><span>{sizing.note}</span>{sizing.denominator_assets && <span>总资产分母：¥ {sizing.denominator_assets} · {sizing.valuation_date} · 本次预算：¥ {sizing.requested_budget}</span>}</div>}<div className="form-actions"><button className="button secondary" disabled={!simAccountId || draftQuantity <= 0 || (sizeMode === 'asset_percent' && (!sizing?.can_create || !sizing.quote_sha256))}>保存草稿</button>{draftSaved && <button type="button" className="button ghost" onClick={() => onOpenSim(simAccountId)}>查看模拟草稿</button>}</div>{!simAccounts.length && <p className="muted">请先创建模拟账户。</p>}</form>}</div>}
  </section><section className="card"><h2>固定样本信号</h2><p className="muted">选择策略和冻结样本，逐项查看信号依据。维科夫 V1 / V2 与 ScoreOnlyRank 使用可选事件模板；五龙聚首同时检查形态和候选入池条件。仅满足买入条件的观察结果可创建模拟草稿。</p><label className="field"><span>策略</span><select aria-label="策略" value={strategyId} onChange={event => { const next = strategies.find(row => row.id === event.target.value); strategyChosen.current = true; setStrategyId(event.target.value); setParams(next?.signal_params || {}); setSelected(null) }}><option value="">请选择已启用策略</option>{strategies.filter(row => row.enabled_in_rebuild !== false).map(row => <option key={row.id} value={row.id}>{row.name} · {row.version}</option>)}</select></label>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    {strategy?.signal_params != null && strategy.enabled_in_rebuild !== false ? <form className="form" onSubmit={submit}><label className="field"><span>冻结行情样本</span><select value={datasetId} onChange={event => setDatasetId(event.target.value)} required>{datasets.map(row => <option key={row.id} value={row.id}>{row.symbol} · {row.first_date} 至 {row.last_date} · {row.id.slice(0, 12)}</option>)}</select></label><label className="field"><span>决策时间</span><input type="datetime-local" value={decision} onChange={event => setDecision(event.target.value)} required /></label><label className="check-field"><input type="checkbox" checked={strict} onChange={event => setStrict(event.target.checked)} />严格可得时间</label>
      {usesProfile && <label className="field"><span>本次事件模板</span><select value={profileId} onChange={event => setProfileId(event.target.value)} required>{profileCatalog?.profiles.map(row => <option key={row.profile_id} value={row.profile_id}>{row.name} · 修订 {row.revision}{row.profile_id === profileCatalog.active_profile_id ? ' · 当前' : ''}</option>)}</select></label>}
      <StrategyPresets key={strategyId} strategyId={strategyId} params={params} onApply={setParams} accountId={accountId} /><StrategyParamFields params={params} strategy={strategy} onChange={(key, value) => setParams(current => ({ ...current, [key]: value }))} /><button className="button primary" disabled={!datasetId}>运行信号判断</button></form> : <p className="muted">{strategyId === 'b1_mtf_v1' ? '默认 / 所选策略为 B1，请在选股筛选页的 B1 多周期入口选择样本并运行。' : strategyId === 'matrix_signal_v1' ? '默认 / 所选策略为矩阵，请在选股筛选页的矩阵信号入口选择冻结池并运行。' : '请先选择已启用策略；启用与默认设置位于系统设置。'}</p>}
  {strategy?.signal_params == null && <button type="button" className="button secondary" onClick={() => (onNavigate || navigateResearch)({ view: 'screener', strategyId })}>打开选股筛选</button>}</section></div>
}
