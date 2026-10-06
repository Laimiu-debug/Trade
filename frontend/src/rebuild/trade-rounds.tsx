import { useCallback, useEffect, useState } from 'react'
import { api, type Analytics, type Trade } from './api'
import { RoundNoteEditor } from './round-note'
import { Icon } from './workspace-icons'

type Result = NonNullable<Analytics['result']>

export function TradeRounds({ accountId, result, trades, onOpenTrade }: { accountId: string; result: Result | null; trades: Trade[]; onOpenTrade: () => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [savedNotes, setSavedNotes] = useState<Array<{ round_id: string; round_exists: boolean; summary: string }>>([])
  const refreshNotes = useCallback(() => api<typeof savedNotes>(`/accounts/${accountId}/round-notes`).then(setSavedNotes).catch(() => {}), [accountId])
  useEffect(() => { refreshNotes() }, [refreshNotes])
  if (!result) return null
  const stats = result.trade_stats
  const rows = [...result.rounds].sort((a, b) => (b.end_date || b.start_date).localeCompare(a.end_date || a.start_date))
  const current = rows.find(row => row.id === selected)
  const details = current?.trade_ids.map(id => trades.find(trade => trade.id === id)).filter((row): row is Trade => Boolean(row)) || []
  return <section id="trade-rounds" className="card span-all"><div className="section-heading"><div><h2 className="title-with-icon"><Icon name="trades" />交易回合</h2><p>已完成、进行中和异常分开统计。点击回合可核对原始成交。</p></div></div>
    <div className="period-summary"><span>已完成 {stats.closed_rounds} 轮</span><span>胜率 {stats.win_rate_pct === null ? '—' : `${stats.win_rate_pct}%`}</span><span>盈亏比 {stats.payoff_ratio ?? '—'}</span><span>Profit Factor {stats.profit_factor ?? '—'}</span><span>最长连胜 {stats.max_consecutive_wins ?? 0}</span><span>最长连亏 {stats.max_consecutive_losses ?? 0}</span></div>
    <div className="table-wrap"><table><thead><tr><th>代码 / 名称</th><th>开始</th><th>结束</th><th>状态</th><th>盈亏</th><th>成交</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td><button className="link-button" onClick={() => setSelected(selected === row.id ? null : row.id)}>{row.symbol} {row.name}</button></td><td>{row.start_date}</td><td>{row.end_date || '—'}</td><td>{row.status === 'closed' ? '已完成' : row.status === 'open' ? '进行中' : '异常待核对'}</td><td>{row.pnl === null ? '—' : `¥ ${row.pnl}`}</td><td>{row.trade_ids.length}</td></tr>)}</tbody></table></div>{!rows.length && <p className="muted">暂无交易回合</p>}
    {current && <div className="market-detail"><h3>{current.symbol} · {current.status === 'anomaly' ? '异常成交' : '回合成交'}</h3>{current.reason && <p className="danger">卖出数量超过记录中的持仓，请核对流水。</p>}<div className="table-wrap"><table><thead><tr><th>日期</th><th>方向</th><th>数量</th><th>价格</th><th>费用</th></tr></thead><tbody>{details.map(row => <tr key={row.id}><td>{row.trade_date}</td><td>{row.side === 'buy' ? '买入' : '卖出'}</td><td>{row.quantity}</td><td>{row.price}</td><td>{row.fee}</td></tr>)}</tbody></table></div><button className="button secondary" onClick={onOpenTrade}>查看交易流水</button></div>}
    {savedNotes.some(row => !row.round_exists) && <div className="market-detail"><h3>已保留的历史回合摘要</h3>{savedNotes.filter(row => !row.round_exists).map(row => <button key={row.round_id} className="link-button" onClick={() => setSelected(row.round_id)}>{row.summary.slice(0, 60) || row.round_id}</button>)}</div>}
    {selected && <RoundNoteEditor key={selected} accountId={accountId} roundId={selected} onSaved={refreshNotes} />}
  </section>
}
