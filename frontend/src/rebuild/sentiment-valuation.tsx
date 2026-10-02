import { AIContextButton } from './ai-launcher'
import { useEffect, useState } from 'react'
import { api } from './api'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string }
type Scenario = { label: string; earnings_yi: string; growth_rate_pct: number; base_pe: number; index_points: number; sentiment_coef: number; actual_cap_yi: string }
type ValuationRow = Scenario & { status: string; growth_coef: number; index_coef: number; theoretical_cap_yi: number | null; implied_sentiment_coef: number | null; implied_sentiment_label?: string; gap_pct: number | null }
type ValuationRun = { id: string; created_at: string; symbol: string; scenario_count: number; request?: { scenarios: Scenario[] }; result?: { formula: string; scenarios: ValuationRow[] } }
type Quote = { symbol: string; name: string; industry: string; price: number | null; market_cap_yi: number | null; pe_ttm: number | null; pe_dynamic: number | null; pe_static: number | null; implied_earnings_yi: number | null; suggested_pe: number; suggested_pe_label: string; suggested_pe_tier: string; index_close: number | null; index_coef: number | null; suggested_sentiment_coef: number | null; limit_up_count_60d: number | null; big_gain_days_60d: number | null; activity_dataset_id: string | null; activity_last_date: string | null; fetched_at: string; source: string; quality_flags: string[] }
const storageKey = 'trade-rebuild.sentiment-valuation.v1'
const initial: Scenario = { label: '基准', earnings_yi: '1', growth_rate_pct: 20, base_pe: 15, index_points: 3000, sentiment_coef: 1, actual_cap_yi: '' }
const presets: Array<{ tier: string; label: string; pe: number }> = [
  { tier: 'bank_insurance', label: '银行 / 保险', pe: 10 },
  { tier: 'traditional', label: '传统行业', pe: 12.5 },
  { tier: 'consumer', label: '消费', pe: 17.5 },
  { tier: 'semi_tech', label: '半科技', pe: 22.5 },
  { tier: 'tech', label: '科技', pe: 27.5 },
  { tier: 'high_growth', label: '高成长稀缺', pe: 30 },
]
function cached() {
  try { return JSON.parse(window.localStorage.getItem(storageKey) || 'null') || {} }
  catch { return {} }
}

export function SentimentValuationPanel({ datasets }: { datasets: Dataset[] }) {
  const [symbol, setSymbol] = useState<string>(() => cached().symbol || '')
  const [datasetId, setDatasetId] = useState<string>(() => cached().datasetId || '')
  const [scenarios, setScenarios] = useState<Scenario[]>(() => cached().scenarios || [initial])
  const [quote, setQuote] = useState<Quote | null>(null)
  const [runs, setRuns] = useState<ValuationRun[]>([])
  const [selected, setSelected] = useState<ValuationRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [quoteBusy, setQuoteBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    try { window.localStorage.setItem(storageKey, JSON.stringify({ symbol, datasetId, scenarios, lastRunId: selected?.id || cached().lastRunId })) }
    catch { /* Keep current scenario in memory if storage is full. */ }
  }, [symbol, datasetId, scenarios, selected?.id])
  useEffect(() => {
    api<ValuationRun[]>('/research/valuation-runs').then(setRuns).catch(() => {})
    const id = cached().lastRunId
    if (id) api<ValuationRun>('/research/valuation-runs/' + id).then(setSelected).catch(() => {})
  }, [])
  function update(index: number, field: keyof Scenario, value: string | number) {
    setScenarios(current => current.map((row, at) => at === index ? { ...row, [field]: value } : row))
  }
  function applyToBase(fields: Partial<Scenario>) {
    setScenarios(current => current.map((row, index) => index === 0 ? { ...row, ...fields } : row))
  }
  async function loadQuote() {
    setError(''); setQuoteBusy(true)
    try {
      const query = new URLSearchParams({ symbol: symbol.trim() })
      if (datasetId) query.set('dataset_id', datasetId)
      const response = await api<Quote>('/research/valuation-quote?' + query.toString())
      setQuote(response)
    } catch (err) { setError(err instanceof Error ? err.message : '实时行情读取失败') }
    finally { setQuoteBusy(false) }
  }
  function applyQuote() {
    if (!quote) return
    const fields: Partial<Scenario> = { base_pe: quote.suggested_pe }
    if (quote.implied_earnings_yi !== null) fields.earnings_yi = quote.implied_earnings_yi.toFixed(4)
    if (quote.index_close !== null) fields.index_points = quote.index_close
    if (quote.market_cap_yi !== null) fields.actual_cap_yi = String(quote.market_cap_yi)
    if (quote.suggested_sentiment_coef !== null) fields.sentiment_coef = quote.suggested_sentiment_coef
    applyToBase(fields)
  }
  async function calculate() {
    setError(''); setBusy(true)
    try {
      const run = await api<ValuationRun>('/research/valuation-runs', 'POST', {
        symbol: symbol.trim(), scenarios: scenarios.map(row => ({
          ...row, actual_cap_yi: row.actual_cap_yi.trim() === '' ? null : row.actual_cap_yi,
        })),
      })
      setSelected(run)
      setRuns(await api<ValuationRun[]>('/research/valuation-runs'))
    } catch (err) { setError(err instanceof Error ? err.message : '估值情景计算失败') }
    finally { setBusy(false) }
  }
  async function open(id: string) {
    try { setSelected(await api<ValuationRun>('/research/valuation-runs/' + id)) }
    catch (err) { setError(err instanceof Error ? err.message : '估值记录读取失败') }
  }
  const symbolDatasets = datasets.filter(row => row.symbol.slice(-6) === symbol.trim().slice(-6))

  return <section className="market-detail"><h3>五因子情绪估值</h3><p className="muted">市值（亿元）= 当期盈利（亿元）× 复合增速系数 × 基准 PE × 大盘水位系数 × 情绪溢价。情景是人工假设，不代表价格预测。亏损或盈利为零时不计算理论市值；TTM PE 缺失时不以动态 / 静态 PE 反推盈利。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="form-grid"><label className="field"><span>证券代码</span><input value={symbol} onChange={event => { setSymbol(event.target.value); setQuote(null); setDatasetId('') }} placeholder="600000 或 sh600000" /></label><label className="field"><span>活动统计用冻结行情（可选）</span><select value={datasetId} onChange={event => setDatasetId(event.target.value)}><option value="">不带入 60 日涨停活跃度</option>{symbolDatasets.map(row => <option key={row.id} value={row.id}>{row.symbol} · {row.first_date} 至 {row.last_date} · {row.id.slice(0, 8)}</option>)}</select></label></div><button type="button" className="button secondary" disabled={quoteBusy || symbol.trim().length < 6} onClick={loadQuote}>{quoteBusy ? '正在读取…' : '读取实时行情提示'}</button>
    {quote && <div className="market-detail"><h4>{quote.symbol} {quote.name || '名称未知'} · 实时行情提示</h4><p className="muted">来源：东方财富证券行情；本地获取于 {quote.fetched_at}。行业：{quote.industry || '未知'}；质量：{quote.quality_flags.join('、') || '—'}。</p><div className="table-wrap"><table><thead><tr><th>最新价</th><th>总市值（亿元）</th><th>TTM PE</th><th>动态 PE</th><th>静态 PE</th><th>TTM 推算盈利（亿元）</th><th>上证指数点位</th><th>行业 PE 提示</th><th>60 日活跃度</th></tr></thead><tbody><tr><td>{quote.price ?? '—'}</td><td>{quote.market_cap_yi ?? '—'}</td><td>{quote.pe_ttm ?? '—'}</td><td>{quote.pe_dynamic ?? '—'}</td><td>{quote.pe_static ?? '—'}</td><td>{quote.implied_earnings_yi ?? '—'}</td><td>{quote.index_close ?? '—'}</td><td>{quote.suggested_pe} · {quote.suggested_pe_label}</td><td>{quote.limit_up_count_60d === null ? '未选冻结样本' : `${quote.limit_up_count_60d} 次涨停 / ${quote.big_gain_days_60d} 次大涨，截至 ${quote.activity_last_date}`}</td></tr></tbody></table></div><button type="button" className="button secondary" onClick={applyQuote}>将可用提示填入基准情景</button></div>}
    <h4>情景对照</h4><p className="muted">盈利单位亿元；增长率是预计年增长百分比，换算为 1 + 增长率；大盘水位以 3000 点为 1 倍、最低 0.5 倍。行业 PE 预设只填基准情景，其他数值可逐项修改。</p><div className="form-grid"><label className="field"><span>行业基准 PE 预设</span><select value="" onChange={event => { const selectedPreset = presets.find(row => row.tier === event.target.value); if (selectedPreset) applyToBase({ base_pe: selectedPreset.pe }) }}><option value="">选择后填入基准情景</option>{presets.map(row => <option key={row.tier} value={row.tier}>{row.label} · PE {row.pe}</option>)}</select></label></div>
    {scenarios.map((row, index) => <div className="market-detail" key={index}><h4>情景 {index + 1}</h4><div className="form-grid"><label className="field"><span>名称</span><input value={row.label} onChange={event => update(index, 'label', event.target.value)} /></label><label className="field"><span>当期盈利（亿元）</span><input type="number" step="any" value={row.earnings_yi} onChange={event => update(index, 'earnings_yi', event.target.value)} /></label><label className="field"><span>年增长率（%）</span><input type="number" step="any" value={row.growth_rate_pct} onChange={event => update(index, 'growth_rate_pct', Number(event.target.value))} /></label><label className="field"><span>基准 PE</span><input type="number" min="0" step="any" value={row.base_pe} onChange={event => update(index, 'base_pe', Number(event.target.value))} /></label><label className="field"><span>大盘点位</span><input type="number" min="1" step="any" value={row.index_points} onChange={event => update(index, 'index_points', Number(event.target.value))} /></label><label className="field"><span>情绪溢价系数</span><input type="number" min="0.01" step="any" value={row.sentiment_coef} onChange={event => update(index, 'sentiment_coef', Number(event.target.value))} /></label><label className="field"><span>实际市值（亿元，可选）</span><input type="number" min="0" step="any" value={row.actual_cap_yi} onChange={event => update(index, 'actual_cap_yi', event.target.value)} /></label></div>{scenarios.length > 1 && <button type="button" className="link-button" onClick={() => setScenarios(current => current.filter((_, at) => at !== index))}>移除此情景</button>}</div>)}
    <div className="form-actions"><button type="button" className="button secondary" disabled={scenarios.length >= 6} onClick={() => setScenarios(current => [...current, { ...current[current.length - 1], label: `情景 ${current.length + 1}` }])}>添加对照情景</button><button type="button" className="button primary" disabled={busy} onClick={calculate}>{busy ? '正在计算…' : '计算并保存情景'}</button></div>
    {runs.length > 0 && <details><summary>历史估值情景 · {runs.length} 次</summary><div className="table-wrap"><table><thead><tr><th>证券</th><th>创建时间</th><th>情景数</th><th>操作</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td>{run.symbol || '未指定'}</td><td>{run.created_at}</td><td>{run.scenario_count}</td><td><button type="button" className="link-button" onClick={() => open(run.id)}>查看</button></td></tr>)}</tbody></table></div></details>}
    {selected?.result && <div className="market-detail"><h4>估值结果 · {selected.id.slice(0, 12)}</h4><AIContextButton source={{ page: 'valuation', artifact: { type: 'valuation', id: selected.id }, label: selected.symbol + ' · 估值假设' }} />{selected.request && <button type="button" className="button secondary" onClick={() => { setSymbol(selected.symbol); setScenarios(selected.request!.scenarios.map(row => ({ ...row, actual_cap_yi: row.actual_cap_yi || '' }))) }}>复制此记录的参数到表单</button>}<p className="muted">{selected.result.formula}</p><div className="table-wrap"><table><thead><tr><th>情景</th><th>盈利（亿元）</th><th>增长系数</th><th>基准 PE</th><th>大盘系数</th><th>情绪系数</th><th>理论市值（亿元）</th><th>实际市值（亿元）</th><th>差距</th><th>反推情绪</th></tr></thead><tbody>{selected.result.scenarios.map(row => <tr key={row.label}><td>{row.label}</td><td>{row.earnings_yi}</td><td>{row.growth_coef.toFixed(2)}</td><td>{row.base_pe}</td><td>{row.index_coef.toFixed(2)}</td><td>{row.sentiment_coef}</td><td>{row.theoretical_cap_yi === null ? '亏损 / 零盈利不适用' : row.theoretical_cap_yi.toFixed(2)}</td><td>{row.actual_cap_yi ?? '—'}</td><td>{row.gap_pct === null ? '—' : `${row.gap_pct.toFixed(2)}%`}</td><td>{row.implied_sentiment_coef === null ? '—' : `${row.implied_sentiment_coef.toFixed(2)} · ${row.implied_sentiment_label}`}</td></tr>)}</tbody></table></div></div>}
  </section>
}
