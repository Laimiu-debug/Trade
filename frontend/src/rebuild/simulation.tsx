import { useWorkspacePage } from './use-workspace-page'
import { WorkspaceNavigation } from './workspace-navigation'
const simulationPages = ['trading', 'settlement', 'drafts', 'valuation', 'review', 'recovery'] as const
const frozenSimulationPages = ['trading', 'settlement', 'drafts', 'review'] as const
import { sameMarketSymbol } from './market-symbols'
import { useCallback, useEffect, useState } from 'react'
import { api, type SimFill, type SimOrder, type SimPortfolio } from './api'
import { SimReviewTags } from './sim-review-tags'
import { SimPerformanceSummary } from './sim-performance'
import { SimEquityReports } from './sim-equity'
import { Icon } from './workspace-icons'

const emptyOrder = { symbol: '', side: 'buy' as 'buy' | 'sell', quantity: 100, limit_price: '', signal_date: '' }
type Dataset = { id: string; symbol: string; first_date: string; last_date: string }
type Valuation = { as_of_date: string; decision_at: string; cash: string; known_position_value: string; known_position_cost: string; known_unrealized_pnl: string; total_assets: string | null; valuation_quality: string; quality_flags: string[]; positions: Array<{ symbol: string; quantity: number; cost_basis: string; unrealized_pnl: string | null; quote_date: string | null; close: string | null; market_value: string | null; quality_flags: string[] }> }
type Draft = { id: string; source_run_id: string; symbol: string; signal_date: string; quantity: number; limit_price: string; status: string; revision: number; order_id: string | null }
type DraftPreview = { gross: string; estimated_fees: string; required_cash: string; available_cash: string; cash_buffer: string; cash_gap: string; can_submit: boolean; eligible_date: boolean; submit_date: string; decision_date: string; decision_at: string; wallet_revision: number; config_version: number; signal_quality_flags: string[]; note: string }
type DraftBatchPreview = { drafts: DraftPreview[]; total_required_cash: string; available_cash: string; cash_buffer: string; cash_gap: string; can_submit: boolean; wallet_revision: number; config_version: number }

export function SimulationEditor({ accountId, accountName, frozen, onAccountSwitch }: {
  accountId: string; accountName: string; frozen: boolean;
  onAccountSwitch: (id: string) => Promise<void>
}) {
  const [simulationPage, setSimulationPage] = useWorkspacePage<(typeof simulationPages)[number]>('simulation-view', frozen ? frozenSimulationPages : simulationPages, 'trading')
  const [portfolio, setPortfolio] = useState<SimPortfolio | null>(null)
  const [orders, setOrders] = useState<SimOrder[]>([])
  const [fills, setFills] = useState<SimFill[]>([])
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [selectedDraftId, setSelectedDraftId] = useState('')
  const [draftQuantity, setDraftQuantity] = useState(100)
  const [draftPrice, setDraftPrice] = useState('')
  const [draftPreview, setDraftPreview] = useState<DraftPreview | null>(null)
  const [selectedDraftIds, setSelectedDraftIds] = useState<string[]>([])
  const [batchPreview, setBatchPreview] = useState<DraftBatchPreview | null>(null)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [datasetBySymbol, setDatasetBySymbol] = useState<Record<string, string>>({})
  const [valuation, setValuation] = useState<Valuation | null>(null)
  const [valuationDecision, setValuationDecision] = useState('')
  const [valuationStrict, setValuationStrict] = useState(true)
  const [form, setForm] = useState(emptyOrder)
  const [settleDate, setSettleDate] = useState('')
  const [config, setConfig] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [resetDate, setResetDate] = useState(() => new Date().toLocaleDateString('sv-SE'))
  const [resetConfig, setResetConfig] = useState(false)
  const root = `/sim-accounts/${accountId}`
  const refresh = useCallback(async () => {
    const [nextPortfolio, nextOrders, nextFills, nextDatasets, nextDrafts] = await Promise.all([
      api<SimPortfolio>(root + '/portfolio'), api<SimOrder[]>(root + '/orders'),
      api<SimFill[]>(root + '/fills'), api<Dataset[]>('/market/datasets'),
      api<Draft[]>(root + '/drafts'),
    ])
    setPortfolio(nextPortfolio); setOrders(nextOrders); setFills(nextFills)
    setDrafts(nextDrafts)
    setSelectedDraftIds(current => current.filter(id => nextDrafts.some(row => row.id === id && row.status === 'draft')))
    setBatchPreview(null)
    setDatasets(nextDatasets); setDatasetId(current => current || nextDatasets[0]?.id || '')
    setDatasetBySymbol(current => {
      const next = { ...current }
      const symbols = new Set([
        ...nextOrders.filter(row => row.status === 'pending').map(row => row.symbol),
        ...nextPortfolio.positions.map(row => row.symbol),
      ])
      for (const symbol of symbols) {
        if (!next[symbol]) next[symbol] = nextDatasets.find(row => sameMarketSymbol(row.symbol, symbol))?.id || ''
      }
      return next
    })
    setValuation(null)
    setValuationDecision((() => {
      const close = new Date(nextPortfolio.as_of_date + 'T23:59:59Z')
      return new Date(close.getTime() - close.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
    })())
    setConfig(nextPortfolio.config)
    setForm(current => ({ ...current, signal_date: current.signal_date || nextPortfolio.as_of_date }))
    setSettleDate(nextPortfolio.as_of_date)
  }, [root])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])

  async function mutate(label: string, operation: () => Promise<unknown>) {
    setError(''); setMessage('')
    try { await operation(); setMessage(label); await refresh() }
    catch (err) { setError(err instanceof Error ? err.message : '操作失败') }
  }

  async function matchOpen(order: SimOrder) {
    if (!datasetId) return
    setError(''); setMessage('')
    try {
      const result = await api<{ status: string }>(root + `/orders/${order.id}/match-open`, 'POST',
        { expected_revision: order.revision, dataset_id: datasetId })
      setMessage(result.status === 'filled' ? '已按冻结行情的次日开盘价成交' :
        result.status === 'no_bar' ? '模拟日期没有对应的行情 K 线，委托保持待成交' :
        result.status === 'no_volume' ? '模拟日期没有成交量，委托保持待成交' :
        '开盘价未达到限价，委托保持待成交')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '开盘撮合失败') }
  }
  const pendingSymbols = [...new Set(orders.filter(row => row.status === 'pending').map(row => row.symbol))]
  const selectedDraft = drafts.find(row => row.id === selectedDraftId)
  const canAdvanceWithMarket = pendingSymbols.every(symbol => datasetBySymbol[symbol])

  async function advanceWithMarket() {
    if (!portfolio) return
    setError(''); setMessage('')
    try {
      const result = await api<{ outcomes: Array<{ status: string }> }>(root + '/advance-market-day', 'POST', {
        expected_wallet_revision: portfolio.wallet_revision, to_date: settleDate,
        datasets: Object.fromEntries(pendingSymbols.map(symbol => [symbol, datasetBySymbol[symbol]])),
      })
      setMessage(`日期已推进；成交 ${result.outcomes.filter(row => row.status === 'filled').length} 笔，未成交 ${result.outcomes.filter(row => row.status !== 'filled').length} 笔`)
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '自动撮合失败') }
  }

  async function calculateValuation() {
    if (!portfolio || !valuationDecision) return
    setError(''); setMessage('')
    try {
      const query = new URLSearchParams({ decision_at: new Date(valuationDecision).toISOString(),
                                          strict: String(valuationStrict) })
      for (const position of portfolio.positions) {
        const selected = datasetBySymbol[position.symbol]
        if (selected) query.append('dataset_id', selected)
      }
      setValuation(await api<Valuation>(root + '/valuation?' + query.toString()))
    } catch (err) { setError(err instanceof Error ? err.message : '估值失败') }
  }

  function chooseDraft(row: Draft) {
    setSelectedDraftId(row.id); setDraftQuantity(row.quantity)
    setDraftPrice(row.limit_price); setDraftPreview(null)
  }

  async function previewSelectedDraft() {
    if (!selectedDraft) return
    setError('')
    try { setDraftPreview(await api<DraftPreview>(root + `/drafts/${selectedDraft.id}/preview`)) }
    catch (err) { setError(err instanceof Error ? err.message : '草稿预览失败') }
  }

  async function previewSelectedBatch() {
    if (!selectedDraftIds.length) return
    setError(''); setBatchPreview(null)
    try {
      const query = new URLSearchParams()
      selectedDraftIds.forEach(id => query.append('draft_id', id))
      setBatchPreview(await api<DraftBatchPreview>(root + '/drafts/preview-batch?' + query.toString()))
    } catch (err) { setError(err instanceof Error ? err.message : '批量预览失败') }
  }

  async function submitSelectedBatch() {
    if (!batchPreview?.can_submit) return
    setError(''); setMessage('')
    try {
      const result = await api<{ orders: SimOrder[] }>(root + '/drafts/submit-batch', 'POST', {
        drafts: selectedDraftIds.map(id => ({ id, expected_revision: drafts.find(row => row.id === id)?.revision })),
        expected_wallet_revision: batchPreview.wallet_revision,
        expected_config_version: batchPreview.config_version,
      })
      setMessage(`已批量提交 ${result.orders.length} 笔模拟限价委托`)
      setSelectedDraftIds([]); setBatchPreview(null)
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '批量提交失败') }
  }

  async function resetAccount() {
    if (!portfolio || !window.confirm(`重置“${accountName}”？原账户及所有委托、成交和草稿会保留为只读恢复点，并创建一个干净的新模拟账户。`)) return
    setError(''); setMessage('')
    try {
      const result = await api<{ new_account: { id: string } }>(root + '/reset', 'POST', {
        expected_wallet_revision: portfolio.wallet_revision,
        start_date: resetDate, reset_config: resetConfig,
      })
      await onAccountSwitch(result.new_account.id)
    } catch (err) { setError(err instanceof Error ? err.message : '重置模拟账户失败') }
  }

  async function activateRecovery() {
    if (!portfolio || !window.confirm(`恢复“${accountName}”为可操作账户？同一恢复组的当前账户会转为只读，所有历史继续保留。`)) return
    setError('')
    try {
      await api(root + '/activate-recovery', 'POST', {
        expected_wallet_revision: portfolio.wallet_revision,
      })
      await onAccountSwitch(accountId)
    } catch (err) { setError(err instanceof Error ? err.message : '激活恢复点失败') }
  }

  const navigation = <WorkspaceNavigation label="模拟交易页面" current={simulationPage} items={frozen ? [['trading', '历史持仓'], ['settlement', '历史委托'], ['drafts', '历史草稿'], ['review', '成交与复盘']] : [['trading', '持仓与下单'], ['settlement', '委托与结算'], ['drafts', '策略草稿'], ['valuation', '持仓估值'], ['review', '成交与复盘'], ['recovery', '账户恢复']]} onChange={setSimulationPage} />
  if (frozen) return <>{navigation}
    <section className="card"><h2>只读模拟恢复点 · {accountName}</h2><p>这份账户保留重置前的资金、持仓、委托、成交和草稿。恢复为可操作账户时，同一恢复组当前的账户会转为只读。</p>{error && <div className="alert error" role="alert">{error}</div>}<button className="button primary" disabled={!portfolio} onClick={activateRecovery}>恢复为可操作账户</button></section>
    <div className="metrics sim-metrics"><div className="metric card"><span>历史日期</span><strong>{portfolio?.as_of_date || '—'}</strong></div><div className="metric card"><span>现金</span><strong>¥ {portfolio?.cash || '—'}</strong></div><div className="metric card"><span>持仓种类</span><strong>{portfolio?.positions.length ?? 0}</strong></div><div className="metric card"><span>成交笔数</span><strong>{fills.length}</strong></div></div>
    <section className="card" hidden={simulationPage !== 'trading'}><h2 className="title-with-icon"><Icon name="holdings" />历史持仓</h2><div className="table-wrap"><table><thead><tr><th>代码</th><th>数量</th><th>成本</th></tr></thead><tbody>{portfolio?.positions.map(row => <tr key={row.symbol}><td>{row.symbol}</td><td>{row.quantity}</td><td>¥ {row.cost_basis}</td></tr>)}</tbody></table></div></section>
    <section className="card" hidden={simulationPage !== 'settlement'}><h2>历史委托 · {orders.length}</h2><div className="table-wrap"><table><thead><tr><th>日期</th><th>代码</th><th>方向</th><th>数量</th><th>状态</th></tr></thead><tbody>{orders.map(row => <tr key={row.id}><td>{row.submit_date}</td><td>{row.symbol}</td><td>{row.side}</td><td>{row.quantity}</td><td>{row.status}</td></tr>)}</tbody></table></div></section>
    <section className="card" hidden={simulationPage !== 'review'}><h2>历史成交 · {fills.length}</h2><div className="table-wrap"><table><thead><tr><th>日期</th><th>价格</th><th>金额</th><th>价格来源</th></tr></thead><tbody>{fills.map(row => <tr key={row.id}><td>{row.fill_date}</td><td>{row.fill_price}</td><td>¥ {row.gross}</td><td>{row.price_source}</td></tr>)}</tbody></table></div></section>
    <div hidden={simulationPage !== 'review'}>    <SimReviewTags accountId={accountId} fills={fills} frozen />
    <SimPerformanceSummary accountId={accountId} fillCount={fills.length} />
    <SimEquityReports accountId={accountId} walletRevision={portfolio?.wallet_revision} />
    </div>
    <section className="card" hidden={simulationPage !== 'drafts'}><h2>历史草稿 · {drafts.length}</h2><div className="table-wrap"><table><thead><tr><th>信号日期</th><th>代码</th><th>数量</th><th>状态</th></tr></thead><tbody>{drafts.map(row => <tr key={row.id}><td>{row.signal_date}</td><td>{row.symbol}</td><td>{row.quantity}</td><td>{row.status}</td></tr>)}</tbody></table></div></section>
  </>

  return <>{navigation}
    <div className="metrics sim-metrics">
      <div className="metric card"><span>模拟日期</span><strong>{portfolio?.as_of_date ?? '—'}</strong><small>结算后前进</small></div>
      <div className="metric card"><span>现金</span><strong>¥ {portfolio?.cash ?? '—'}</strong><small>包含已预留资金</small></div>
      <div className="metric card"><span>可用资金</span><strong>¥ {portfolio?.available_cash ?? '—'}</strong><small>预留 ¥ {portfolio?.reserved_cash ?? '—'}</small></div>
      <div className="metric card"><span>成交笔数</span><strong>{fills.length}</strong><small>仅模拟成交</small></div>
    </div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {message && <div className="alert success" role="status">{message}</div>}
    <div className="two-col wide-left" hidden={simulationPage !== 'trading'}><section className="card"><h2 className="title-with-icon"><Icon name="holdings" />持仓与可卖数量</h2><div className="table-wrap"><table><thead><tr><th>代码</th><th>持仓数量</th><th>可卖数量</th><th>剩余成本</th><th>操作</th></tr></thead><tbody>{portfolio?.positions.map(row => <tr key={row.symbol}><td>{row.symbol}</td><td>{row.quantity}</td><td>{row.sellable_quantity}</td><td>¥ {row.cost_basis}</td><td><button className="link-button" disabled={row.sellable_quantity === 0} onClick={() => { setForm({ symbol: row.symbol, side: 'sell', quantity: row.sellable_quantity, limit_price: valuation?.positions.find(item => item.symbol === row.symbol)?.close || '', signal_date: portfolio.as_of_date }); document.getElementById('sim-order-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>快捷卖出</button></td></tr>)}</tbody></table></div>{!portfolio?.positions.length && <p className="muted">暂无模拟持仓</p>}<p className="muted">快捷卖出只预填可卖数量与已计算价格，仍需核对限价并提交模拟委托。</p></section>
      <section className="card" id="sim-order-form"><h2 className="title-with-icon"><Icon name="run" />提交模拟委托</h2><form className="form" onSubmit={event => { event.preventDefault(); if (!portfolio) return; mutate('模拟委托已提交', async () => { await api(root + '/orders', 'POST', { ...form, submit_date: portfolio.as_of_date }); setForm({ ...emptyOrder, signal_date: portfolio.as_of_date }) }) }}>
        <label className="field"><span>方向</span><select value={form.side} onChange={event => setForm({ ...form, side: event.target.value as 'buy' | 'sell' })}><option value="buy">买入</option><option value="sell">卖出</option></select></label>
        <label className="field"><span>代码</span><input value={form.symbol} onChange={event => setForm({ ...form, symbol: event.target.value })} required /></label>
        <div className="form-grid"><label className="field"><span>数量</span><input type="number" min="1" value={form.quantity} onChange={event => setForm({ ...form, quantity: Number(event.target.value) })} required /></label><label className="field"><span>限价</span><input type="number" min="0.0001" step="0.0001" value={form.limit_price} onChange={event => setForm({ ...form, limit_price: event.target.value })} required /></label></div>
        <label className="field"><span>信号日期</span><input type="date" value={form.signal_date} max={portfolio?.as_of_date} onChange={event => setForm({ ...form, signal_date: event.target.value })} required /></label>
        <button className="button primary"><Icon name="check" />提交模拟委托</button>
      </form></section></div>
    <section className="card" hidden={simulationPage !== 'valuation'}><h2 className="title-with-icon"><Icon name="asset" />模拟持仓估值</h2><p className="muted">逐个选择持仓代码的冻结行情；只使用模拟日期当天、在决策时刻已可得的收盘价。缺价时不生成总资产。</p><div className="form-grid">{portfolio?.positions.map(position => <label className="field" key={position.symbol}><span>{position.symbol} 的估值样本</span><select value={datasetBySymbol[position.symbol] || ''} onChange={event => setDatasetBySymbol({ ...datasetBySymbol, [position.symbol]: event.target.value })}><option value="">未选择</option>{datasets.filter(row => sameMarketSymbol(row.symbol, position.symbol)).map(row => <option key={row.id} value={row.id}>{row.first_date} 至 {row.last_date} · {row.id.slice(0, 12)}</option>)}</select></label>)}<label className="field"><span>决策时间</span><input type="datetime-local" value={valuationDecision} onChange={event => setValuationDecision(event.target.value)} /></label></div><label className="check-field"><input type="checkbox" checked={valuationStrict} onChange={event => setValuationStrict(event.target.checked)} />严格可得时间</label><button className="button secondary" onClick={calculateValuation}>计算当日市值</button>{valuation && <div className="period-summary"><span>现金：¥ {valuation.cash}</span><span>已知持仓市值：¥ {valuation.known_position_value}</span><span>已知持仓成本：¥ {valuation.known_position_cost}</span><span>已知未实现盈亏：¥ {valuation.known_unrealized_pnl}</span><span>总资产：{valuation.total_assets === null ? '数据不完整' : `¥ ${valuation.total_assets}`}</span><span>质量：{valuation.valuation_quality}</span>{valuation.positions.map(row => <span key={row.symbol}>{row.symbol}：{row.market_value === null ? row.quality_flags.join(', ') : `市值 ¥ ${row.market_value} · 成本 ¥ ${row.cost_basis} · 未实现 ¥ ${row.unrealized_pnl}`}</span>)}</div>}</section>
    <section className="card" hidden={simulationPage !== 'drafts'}><h2 className="title-with-icon"><Icon name="document" />策略信号委托草稿</h2><p className="muted">草稿不会预留资金或自动成交。查看预览后，可在当前模拟日期提交为限价委托。</p><div className="table-wrap"><table><thead><tr><th>信号日期</th><th>代码</th><th>数量</th><th>限价</th><th>状态</th><th>操作</th></tr></thead><tbody>{drafts.map(row => <tr key={row.id}><td>{row.signal_date}</td><td>{row.symbol}</td><td>{row.quantity}</td><td>{row.limit_price}</td><td>{({ draft: '草稿', submitted: '已提交', cancelled: '已取消' } as Record<string, string>)[row.status]}</td><td><button className="link-button" onClick={() => chooseDraft(row)}>查看</button></td></tr>)}</tbody></table></div>{!drafts.length && <p className="muted">暂无草稿。可从策略研究页将观察信号加入模拟账户。</p>}{selectedDraft && <div className="market-detail"><h3>{selectedDraft.symbol} · {selectedDraft.signal_date} 草稿</h3><p className="muted">来源研究运行：{selectedDraft.source_run_id.slice(0, 16)}</p>{selectedDraft.status === 'draft' && <><form className="form" onSubmit={event => { event.preventDefault(); setDraftPreview(null); mutate('草稿已更新', () => api(root + `/drafts/${selectedDraft.id}`, 'PUT', { expected_revision: selectedDraft.revision, quantity: draftQuantity, limit_price: draftPrice })) }}><div className="form-grid"><label className="field"><span>数量</span><input type="number" min="1" value={draftQuantity} onChange={event => { setDraftQuantity(Number(event.target.value)); setDraftPreview(null) }} required /></label><label className="field"><span>限价</span><input type="number" min="0.0001" step="0.0001" value={draftPrice} onChange={event => { setDraftPrice(event.target.value); setDraftPreview(null) }} required /></label></div><button className="button secondary"><Icon name="save" />保存草稿修改</button></form><div className="form-actions"><button className="button secondary" onClick={previewSelectedDraft}><Icon name="document" />预览费用与资金</button><button className="button ghost danger" onClick={() => { if (window.confirm('取消这份委托草稿？')) mutate('草稿已取消', () => api(root + `/drafts/${selectedDraft.id}/cancel?expected_revision=${selectedDraft.revision}`, 'POST', {})) }}><Icon name="close" />取消草稿</button></div>{draftPreview && <div className="period-summary"><span>委托金额：¥ {draftPreview.gross}</span><span>预计费用：¥ {draftPreview.estimated_fees}</span><span>所需资金：¥ {draftPreview.required_cash}</span><span>可用资金：¥ {draftPreview.available_cash}</span><span>资金缺口：¥ {draftPreview.cash_gap}</span><span>可提交：{draftPreview.can_submit ? '是' : '否'}</span><span>研究决策日期：{draftPreview.decision_date}</span><span>模拟日期：{draftPreview.submit_date}{!draftPreview.eligible_date ? '（尚未到达信号可用日期）' : ''}</span><span>信号质量：{draftPreview.signal_quality_flags.join(', ') || '未标记缺失'}</span></div>}{draftPreview && <><p className="muted">{draftPreview.note}</p><button className="button primary" disabled={!draftPreview.can_submit} onClick={() => { setDraftPreview(null); mutate('草稿已提交为模拟限价委托', () => api(root + `/drafts/${selectedDraft.id}/submit?expected_revision=${selectedDraft.revision}&expected_wallet_revision=${draftPreview.wallet_revision}&expected_config_version=${draftPreview.config_version}`, 'POST', {})) }}><Icon name="check" />提交到模拟委托</button></>}</>}</div>}</section>
    <div className="two-col wide-left" hidden={simulationPage !== 'settlement'}><section className="card"><h2 className="title-with-icon"><Icon name="document" />委托记录</h2><label className="field"><span>开盘撮合行情样本</span><select value={datasetId} onChange={event => setDatasetId(event.target.value)}><option value="">选择冻结样本</option>{datasets.map(row => <option key={row.id} value={row.id}>{row.symbol} · {row.first_date} 至 {row.last_date} · {row.id.slice(0, 12)}</option>)}</select></label><p className="muted">先将模拟日期推进到委托提交日之后，再按该日开盘价尝试撮合；价格未达到限价时保留委托。</p><div className="table-wrap"><table><thead><tr><th>日期</th><th>代码</th><th>方向</th><th>数量</th><th>限价</th><th>状态</th><th>操作</th></tr></thead><tbody>{orders.map(row => <tr key={row.id}><td>{row.submit_date}</td><td>{row.symbol}</td><td>{row.side === 'buy' ? '买入' : '卖出'}</td><td>{row.quantity}</td><td>{row.limit_price}{row.legacy_origin?.price_is_fill_reference && <small className="muted"> · 旧成交参考，原限价未知</small>}</td><td>{({ pending: '待成交', filled: '已成交', cancelled: '已撤销' } as Record<string, string>)[row.status]}{row.legacy_origin && <details><summary>旧来源</summary><p>原委托 {row.legacy_origin.source_order_id} · 当时费率未知，实际费用见成交记录。来源档案 {row.legacy_origin.import_id}</p></details>}</td><td>{row.status === 'pending' && <><button className="link-button" onClick={() => { const price = window.prompt('确认成交价（手动来源）', row.limit_price)?.trim(); if (price && portfolio) mutate('模拟成交已确认', () => api(root + `/orders/${row.id}/fill`, 'POST', { expected_revision: row.revision, fill_date: portfolio.as_of_date, fill_price: price })) }}>确认成交</button>{datasetId && <button className="link-button" onClick={() => matchOpen(row)}>按开盘价撮合</button>}<button className="link-button danger" onClick={() => { if (window.confirm('撤销这笔模拟委托？')) mutate('委托已撤销', () => api(root + `/orders/${row.id}/cancel?expected_revision=${row.revision}`, 'POST', {})) }}>撤销</button></>}</td></tr>)}</tbody></table></div>{!orders.length && <p className="muted">暂无委托</p>}</section>
      <section className="card"><h2 className="title-with-icon"><Icon name="calculator" />结算与费用</h2><p className="muted">结算推进模拟日期；买入批次在下一日期可卖。批量撮合使用所选冻结样本在目标日期的开盘价。</p><form className="form" onSubmit={event => { event.preventDefault(); mutate('模拟日期已结算', () => api(root + '/settle', 'POST', { to_date: settleDate })) }}><label className="field"><span>结算到</span><input type="date" min={portfolio?.as_of_date} value={settleDate} onChange={event => setSettleDate(event.target.value)} required /></label><button className="button secondary">仅推进日期</button></form>
        {pendingSymbols.map(symbol => <label className="field" key={symbol}><span>{symbol} 的撮合样本</span><select value={datasetBySymbol[symbol] || ''} onChange={event => setDatasetBySymbol({ ...datasetBySymbol, [symbol]: event.target.value })}><option value="">选择冻结样本</option>{datasets.filter(row => sameMarketSymbol(row.symbol, symbol)).map(row => <option value={row.id} key={row.id}>{row.first_date} 至 {row.last_date} · {row.id.slice(0, 12)}</option>)}</select></label>)}
        <button className="button primary" type="button" disabled={!canAdvanceWithMarket || !settleDate || settleDate <= (portfolio?.as_of_date || '')} onClick={advanceWithMarket}>推进并撮合待成交委托</button>
        <h3>费用配置</h3><form className="form" onSubmit={event => { event.preventDefault(); if (!portfolio) return; mutate('费用配置已保存；已有委托保留旧版本', () => api(root + '/config', 'PUT', { expected_version: portfolio.config_version, config })) }}>
          {([['commission_rate', '佣金比例'], ['minimum_commission', '最低佣金'], ['sell_stamp_rate', '卖出印花税比例'], ['transfer_rate', '过户费比例'], ['cash_buffer', '现金缓冲'], ['slippage_rate', '开盘撮合滑点比例（0.01 表示 1%）']] as const).map(([key, label]) => <label className="field" key={key}><span>{label}</span><input type="number" min="0" step="any" value={config[key] ?? ''} onChange={event => setConfig({ ...config, [key]: event.target.value })} required /></label>)}<button className="button secondary"><Icon name="save" />保存费用配置</button><p className="muted">滑点仅用于冻结开盘价撮合，买入向上、卖出向下调整；调整后不满足限价则不成交。委托保存提交时的规则。手动填写的成交价视为最终成交价。</p></form></section></div>
    <section className="card" hidden={simulationPage !== 'review'}><h2 className="title-with-icon"><Icon name="trades" />模拟成交</h2><div className="table-wrap"><table><thead><tr><th>日期</th><th>成交价</th><th>成交金额</th><th>佣金</th><th>印花税</th><th>过户费</th><th>已实现盈亏</th><th>价格来源</th></tr></thead><tbody>{fills.map(row => <tr key={row.id}><td>{row.fill_date}</td><td>{row.fill_price}</td><td>¥ {row.gross}</td><td>{row.commission}</td><td>{row.stamp}</td><td>{row.transfer}</td><td>{row.realized_pnl ?? '—'}</td><td>{row.price_source === 'manual' ? '手动' : row.price_source}</td></tr>)}</tbody></table></div>{!fills.length && <p className="muted">暂无模拟成交</p>}</section>
    <div hidden={simulationPage !== 'review'}>    <SimReviewTags accountId={accountId} fills={fills} frozen={false} />
    <SimPerformanceSummary accountId={accountId} fillCount={fills.length} />
    <SimEquityReports accountId={accountId} walletRevision={portfolio?.wallet_revision} />
    </div>
    <section className="card" hidden={simulationPage !== 'drafts'}><h2 className="title-with-icon"><Icon name="document" />批量提交委托草稿</h2><p className="muted">勾选多份草稿后预览合计资金；任意一笔不满足条件时，整批不会提交。</p><div className="form-grid">{drafts.filter(row => row.status === 'draft').map(row => <label className="check-field" key={row.id}><input type="checkbox" checked={selectedDraftIds.includes(row.id)} onChange={event => { setBatchPreview(null); setSelectedDraftIds(current => event.target.checked ? [...current, row.id] : current.filter(id => id !== row.id)) }} />{row.symbol} · {row.signal_date} · {row.quantity} 股 · ¥ {row.limit_price}</label>)}</div><div className="form-actions"><button className="button secondary" disabled={!selectedDraftIds.length} onClick={previewSelectedBatch}><Icon name="document" />预览所选草稿</button>{batchPreview && <button className="button primary" disabled={!batchPreview.can_submit} onClick={submitSelectedBatch}>批量提交委托</button>}</div>{batchPreview && <div className="period-summary"><span>草稿数量：{batchPreview.drafts.length}</span><span>合计所需：¥ {batchPreview.total_required_cash}</span><span>可用资金：¥ {batchPreview.available_cash}</span><span>资金缺口：¥ {batchPreview.cash_gap}</span><span>可提交：{batchPreview.can_submit ? '是' : '否'}</span></div>}</section>
    <section className="card" hidden={simulationPage !== 'recovery'}><h2 className="title-with-icon"><Icon name="refresh" />重置模拟账户</h2><p className="muted">重置会创建一个干净的新模拟账户，当前账户连同所有委托、成交与草稿保留为只读恢复点。以后可在账户列表选择恢复点并重新激活。</p><div className="form-grid"><label className="field"><span>新账户起始日期</span><input type="date" value={resetDate} onChange={event => setResetDate(event.target.value)} /></label><label className="check-field"><input type="checkbox" checked={resetConfig} onChange={event => setResetConfig(event.target.checked)} />费用恢复默认设置</label></div><button className="button secondary" disabled={!portfolio} onClick={resetAccount}><Icon name="add" />创建新账户并保留恢复点</button></section>
  </>
}
