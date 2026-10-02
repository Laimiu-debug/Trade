import { sameMarketSymbol } from './market-symbols'
import { useEffect, useState } from 'react'
import { api, type SnapshotPosition } from './api'

type Dataset = { id: string; symbol: string; first_date: string; last_date: string; availability_quality: string }
type Estimate = { account_id: string; as_of_date: string; decision_at: string; strict: boolean;
  cash: string | null; known_position_value: string; total_assets: string | null;
  valuation_quality: string; quality_flags: string[]; difference_from_manual: string | null;
  manual_snapshot: { revision: number } | null;
  positions: Array<{ symbol: string; name: string; quantity: number; dataset_id: string | null;
    quote_date: string | null; close: string | null; market_value: string | null; quality_flags: string[] }> }

const labels: Record<string, string> = {
  dataset_missing: '未选择行情样本', quote_unavailable_at_decision: '决策时点没有可用收盘价',
  stale_quote: '使用较早交易日的收盘价', historical_availability_unknown: '历史可得时间未知',
  initial_capital_missing: '缺少初始资金', negative_inferred_cash: '推算现金为负',
  oversold_trade_history: '交易流水存在超卖',
}

export function AssetEstimate({ accountId, day, symbols, onApply }: {
  accountId: string; day: string; symbols: string[];
  onApply: (estimate: { total_assets: string; cash: string; position_value: string; positions: SnapshotPosition[] }) => void
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [decisionAt, setDecisionAt] = useState(`${day}T23:59:59Z`)
  const [strict, setStrict] = useState(true)
  const [result, setResult] = useState<Estimate | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { api<Dataset[]>('/market/datasets').then(setDatasets).catch(err => setError(err.message)) }, [])
  useEffect(() => { setDecisionAt(`${day}T23:59:59Z`); setResult(null) }, [day])
  const choices = [...new Set(symbols)].sort()
  async function estimate() {
    setBusy(true); setError(''); setResult(null)
    try {
      const query = new URLSearchParams({ decision_at: decisionAt, strict: String(strict) })
      for (const id of Object.values(selected).filter(Boolean)) query.append('dataset_id', id)
      setResult(await api<Estimate>(`/accounts/${accountId}/asset-estimate/${day}?${query}`))
    } catch (err) { setError(err instanceof Error ? err.message : '资产估算失败') }
    finally { setBusy(false) }
  }
  function apply() {
    if (!result?.total_assets || !result.cash || result.positions.some(row => row.market_value === null)) return
    onApply({ total_assets: result.total_assets, cash: result.cash,
      position_value: result.known_position_value,
      positions: result.positions.map(row => ({ symbol: row.symbol, name: row.name,
        quantity: row.quantity, market_value: row.market_value! })) })
  }
  return <section className="card span-all"><h2>根据流水与行情估算资产</h2><p className="muted">只读推算。选择每只持仓的冻结行情样本；估算结果不会自动改动已确认快照。</p>
    <div className="form"><div className="form-grid"><label className="field">估算决策时间（含时区）<input value={decisionAt} onChange={event => setDecisionAt(event.target.value)} /></label><label className="check-field"><input type="checkbox" checked={strict} onChange={event => setStrict(event.target.checked)} />严格要求历史可得时间</label></div>
      {choices.map(symbol => <label className="field" key={symbol}>{symbol} 行情样本<select value={selected[symbol] || ''} onChange={event => setSelected({ ...selected, [symbol]: event.target.value })}><option value="">未选择</option>{datasets.filter(row => sameMarketSymbol(row.symbol, symbol)).map(row => <option value={row.id} key={row.id}>{row.first_date} 至 {row.last_date} · {row.id.slice(0, 10)} · {row.availability_quality === 'provided_availability' ? '有可得时间' : '可得时间未知'}</option>)}</select></label>)}
      <div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={estimate}>估算资产</button>{result?.total_assets && result.cash && <button type="button" className="button primary" onClick={apply}>把估算值填入表单</button>}</div>
    </div>{error && <p className="danger" role="alert">{error}</p>}
    {result && <div className="market-detail"><div className="period-summary"><span>现金 ¥ {result.cash ?? '未知'}</span><span>已知持仓市值 ¥ {result.known_position_value}</span><span>估算总资产 ¥ {result.total_assets ?? '缺失'}</span><span>与手工快照差 ¥ {result.difference_from_manual ?? '—'}</span></div>{result.manual_snapshot && <p className="muted">该日已有手工确认快照。填入表单后仍需点击“保存快照”才会修订。</p>}{result.quality_flags.length > 0 && <p className="muted">数据限制：{result.quality_flags.map(flag => labels[flag] || flag).join('；')}</p>}<div className="table-wrap"><table><thead><tr><th>代码</th><th>数量</th><th>价格日期</th><th>收盘价</th><th>估算市值</th><th>质量</th></tr></thead><tbody>{result.positions.map(row => <tr key={row.symbol}><td>{row.symbol}</td><td>{row.quantity}</td><td>{row.quote_date || '—'}</td><td>{row.close || '—'}</td><td>{row.market_value ?? '缺失'}</td><td>{row.quality_flags.map(flag => labels[flag] || flag).join('；') || '完整'}</td></tr>)}</tbody></table></div></div>}
  </section>
}
