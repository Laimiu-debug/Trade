import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type ClosedFill = { fill_id: string; symbol: string; sell_date: string; quantity: number; sell_gross: string; fees: string; realized_pnl: string; buy_allocations: Array<{ buy_fill_id: string | null; buy_date: string; quantity: number; cost_basis?: string; sell_fees?: string; realized_pnl?: string }>; quality: string }
type SimPerformance = { buy_attribution_unavailable_fill_ids: string[]; buy_fill_count: number; sell_fill_count: number; realized_pnl: string; win_rate_pct: string | null; profit_factor: string | null; max_consecutive_wins: number; max_consecutive_losses: number; best_fill_id: string | null; worst_fill_id: string | null; monthly: Array<{ month: string; sell_count: number; realized_pnl: string; fill_ids: string[] }>; realized_curve: Array<{ date: string; fill_id: string; cumulative_realized_pnl: string }>; closed_fills: ClosedFill[]; method: string }

export function SimPerformanceSummary({ accountId, fillCount }: { accountId: string; fillCount: number }) {
  const [data, setData] = useState<SimPerformance | null>(null)
  const [dateBasis, setDateBasis] = useState<'sell' | 'buy'>('sell')
  const [rangeDraft, setRangeDraft] = useState({ from: '', to: '' })
  const [range, setRange] = useState({ from: '', to: '' })
  const [selectedFillId, setSelectedFillId] = useState('')
  const [error, setError] = useState('')
  const query = new URLSearchParams({ date_basis: dateBasis, ...(range.from ? { date_from: range.from } : {}), ...(range.to ? { date_to: range.to } : {}) }).toString()
  const exportRoot = `/api/v1/accounts/${accountId}/exports/performance`
  useEffect(() => {
    let active = true
    setData(null); setError(''); setSelectedFillId('')
    api<SimPerformance>(`/sim-accounts/${accountId}/performance?${query}`)
      .then(result => { if (active) { setData(result); setError('') } })
      .catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [accountId, fillCount, query])
  const selected = data?.closed_fills.find(row => row.fill_id === selectedFillId)
  return <section className="card">
    <h2 className="title-with-icon"><Icon name="statistics" />模拟已实现交易统计</h2>
    {data && <div className="toolbar"><a className="button secondary" href={`${exportRoot}.pdf?${query}`} download>导出当前归属口径 PDF</a><a className="button secondary" href={`${exportRoot}.xlsx?${query}`}>导出统计 Excel</a><a className="button secondary" href={`${exportRoot}/summary.csv?${query}`}>导出月份 CSV</a></div>}
    <label className="field"><span>月份归属日期</span><select value={dateBasis} onChange={event => setDateBasis(event.target.value as 'buy' | 'sell')}><option value="sell">卖出日</option><option value="buy">买入日（事后归属）</option></select></label>
    <form className="form-grid" onSubmit={event => { event.preventDefault(); if (rangeDraft.from && rangeDraft.to && rangeDraft.from > rangeDraft.to) { setError('开始日期不能晚于结束日期'); return } setRange({ ...rangeDraft }) }}><label className="field">统计开始日期<input type="date" value={rangeDraft.from} onChange={event => setRangeDraft(value => ({ ...value, from: event.target.value }))} /></label><label className="field">统计结束日期<input type="date" value={rangeDraft.to} onChange={event => setRangeDraft(value => ({ ...value, to: event.target.value }))} /></label><div className="form-actions"><button className="button secondary">应用统计区间</button><button type="button" className="button secondary" onClick={() => { setRangeDraft({ from: '', to: '' }); setRange({ from: '', to: '' }); setError('') }}>清空统计区间</button></div></form>
    <p className="muted">当前统计及导出范围：{range.from || '不限起点'} 至 {range.to || '当前模拟日期'}，按{dateBasis === 'buy' ? '买入批次日期' : '卖出成交日期'}筛选。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    <p className="muted">{data?.method || '正在读取成交统计…'}</p>
    {data && <>
      <div className="period-summary"><span>买入成交 {data.buy_fill_count}</span><span>卖出成交 {data.sell_fill_count}</span><span>已实现盈亏 ¥ {data.realized_pnl}</span><span>卖出胜率 {data.win_rate_pct === null ? '—' : `${data.win_rate_pct}%`}</span><span>Profit Factor {data.profit_factor ?? '—'}</span><span>最长连胜 {data.max_consecutive_wins}</span><span>最长连亏 {data.max_consecutive_losses}</span></div>
      <p className="muted">最佳成交：{data.best_fill_id?.slice(0, 8) || '—'}；最差成交：{data.worst_fill_id?.slice(0, 8) || '—'}。</p>
      {dateBasis === 'buy' && data.buy_attribution_unavailable_fill_ids.length > 0 && <p className="alert warning">{data.buy_attribution_unavailable_fill_ids.length} 笔旧卖出缺少精确批次分摊，未纳入买入月汇总；{range.from || range.to ? '筛选范围内也不猜测其盈亏。' : '总盈亏仍保留全部卖出。'}</p>}
      <h3>{dateBasis === 'buy' ? '买入月份（已实现部分）' : '卖出月份'}</h3><div className="table-wrap"><table><thead><tr><th>月份</th><th>卖出笔数</th><th>已实现盈亏</th></tr></thead><tbody>{data.monthly.map(row => <tr key={row.month}><td>{row.month}</td><td>{row.sell_count}</td><td>¥ {row.realized_pnl}</td></tr>)}</tbody></table></div>
      <h3>卖出成交与买入归属</h3><div className="table-wrap"><table><thead><tr><th>卖出日</th><th>代码</th><th>数量</th><th>卖出额</th><th>费用</th><th>已实现盈亏</th><th>累计已实现</th></tr></thead><tbody>{data.closed_fills.map((row, index) => <tr key={row.fill_id}><td><button className="link-button" onClick={() => setSelectedFillId(selectedFillId === row.fill_id ? '' : row.fill_id)}>{row.sell_date}</button></td><td>{row.symbol}</td><td>{row.quantity}</td><td>¥ {row.sell_gross}</td><td>¥ {row.fees}</td><td>¥ {row.realized_pnl}</td><td>¥ {data.realized_curve[index]?.cumulative_realized_pnl}</td></tr>)}</tbody></table></div>
      {selected && <div className="market-detail"><h3>{selected.symbol} · {selected.sell_date} 的 FIFO 来源</h3>{selected.buy_allocations.map(row => <p key={`${row.buy_fill_id}:${row.buy_date}`} className="muted">买入 {row.buy_date} · {row.quantity} 股 · 成交 {row.buy_fill_id?.slice(0, 12) || '旧批次未关联成交ID'}{row.cost_basis && ` · 含买入费用成本 ¥ ${row.cost_basis} · 分摊卖出费用 ¥ ${row.sell_fees} · 已实现 ¥ ${row.realized_pnl}`}</p>)}{selected.quality !== 'complete' && <p className="danger">部分卖出缺少可匹配买入批次，请核对模拟成交。</p>}</div>}
    </>}
  </section>
}
