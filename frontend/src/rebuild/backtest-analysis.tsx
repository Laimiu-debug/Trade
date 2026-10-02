import { useState } from 'react'

export type AdvancedAnalysis = { status: string; reason?: string; version?: string; risk?: Record<string, unknown>; stability?: { months: Array<{ month: string; return: number | null; sessions: number; first_observed_date: string; last_observed_date: string }>; monthly_return_std: number | null; scope: string; reason: string | null }; regimes?: { scope: string; buckets: Array<{ regime: string; trade_count: number; win_rate: number | null; pnl_contribution: number | null; closed_trade_sequence_drawdown: number | null }> }; monte_carlo?: { status: string; reason: string | null; scope: string; seed: number; iterations: number; sample_count: number; total_return: Record<string, number> | null; max_drawdown: Record<string, number> | null; ruin_probability: number | null; capital_loss_20_probability: number | null }; methodology?: Record<string, string | number>; walk_forward?: { status: string; reason: string } }

const percent = (value: unknown) => typeof value === 'number' ? `${(value * 100).toFixed(2)}%` : '—'
const number = (value: unknown) => typeof value === 'number' ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '—'
const metrics: Array<[string, string, boolean]> = [['sharpe', 'Sharpe', false], ['sortino', 'Sortino', false], ['calmar', 'Calmar', false], ['annualized_return', '样本年化收益', true], ['profit_factor', '盈利因子', false], ['expectancy', '每轮期望收益', true], ['average_win', '平均盈利', true], ['average_loss', '平均亏损', true], ['average_net_pnl', '平均每轮净盈亏', false], ['max_consecutive_losses', '最大连续亏损', false], ['fill_rate', '单股信号成交率', true], ['max_concurrent_positions', '最大并发持仓', false]]

export function BacktestAnalysis({ value }: { value?: AdvancedAnalysis }) {
  if (!value || value.status !== 'generated' || !value.risk) return <div className="market-detail"><h3>高级分析</h3><p className="muted">{value?.reason || '该历史结果未生成高级分析'}。可启用高级分析后创建新实验。</p></div>
  const risk = value.risk, mc = value.monte_carlo
  const recovery = risk.recovery as { status: string; calendar_days: number | null; observed_calendar_days: number }
  return <div className="market-detail"><h3>风险与稳定性分析</h3><p className="muted">版本 {value.version} · {String(risk.daily_sample_count)} 个日收益 · {String(risk.completed_trade_count)} 轮交易。年化值仅描述样本，无风险利率设为 0。</p>
    <div className="metrics">{metrics.map(([key, title, ratio]) => <div className="metric" key={key}><span>{title}</span><strong>{ratio ? percent(risk[key]) : number(risk[key])}</strong></div>)}</div>
    <p>最大回撤恢复：{recovery.status === 'no_drawdown' ? '没有回撤' : recovery.status === 'recovered' ? `${recovery.calendar_days} 个自然日` : `截至样本末尾尚未恢复，已观察 ${recovery.observed_calendar_days} 个自然日`}</p>
    {Object.values(risk.unavailable_reasons as Record<string, string>).map((reason, index) => <p key={index} className="muted">{reason}</p>)}
    {value.stability && <details><summary>月收益与稳定性</summary><p className="muted">{value.stability.scope} · 月收益标准差 {percent(value.stability.monthly_return_std)} {value.stability.reason}</p><div className="table-wrap"><table><thead><tr><th>月份</th><th>实际覆盖</th><th>日线数</th><th>收益率</th></tr></thead><tbody>{value.stability.months.map(row => <tr key={row.month}><td>{row.month}</td><td>{row.first_observed_date} 至 {row.last_observed_date}</td><td>{row.sessions}</td><td>{percent(row.return)}</td></tr>)}</tbody></table></div></details>}
    {value.regimes && <details><summary>入场前市场状态代理</summary><p className="muted">{value.regimes.scope}</p><div className="table-wrap"><table><thead><tr><th>状态</th><th>交易数</th><th>胜率</th><th>净盈亏贡献</th><th>平仓序列回撤</th></tr></thead><tbody>{value.regimes.buckets.map(row => <tr key={row.regime}><td>{{ bull: '标的上涨', range: '标的震荡', bear: '标的下跌', unknown: '历史不足' }[row.regime]}</td><td>{row.trade_count}</td><td>{percent(row.win_rate)}</td><td>{percent(row.pnl_contribution)}</td><td>{percent(row.closed_trade_sequence_drawdown)}</td></tr>)}</tbody></table></div></details>}
    {mc && <details><summary>蒙特卡洛情景分析</summary><p>{mc.scope}</p><p className="muted">随机种子 {mc.seed} · 完整交易 {mc.sample_count} · 实际抽样 {mc.iterations} 次 · {mc.reason || '已生成'}</p>{mc.status === 'generated' && <><div className="table-wrap"><table><thead><tr><th>指标</th><th>P5</th><th>P50</th><th>P95</th></tr></thead><tbody>{([['总收益', mc.total_return], ['最大回撤', mc.max_drawdown]] as const).map(([title, row]) => <tr key={title}><td>{title}</td>{['p5', 'p50', 'p95'].map(key => <td key={key}>{percent(row?.[key])}</td>)}</tr>)}</tbody></table></div><p>期末亏损达到 20% 的样本比例 {percent(mc.capital_loss_20_probability)} · 资金耗尽样本比例 {percent(mc.ruin_probability)}</p></>}</details>}
    <p className="muted">Walk-forward：{value.walk_forward?.reason || '未生成'}</p><details><summary>指标计算口径</summary><dl>{Object.entries(value.methodology || {}).map(([key, detail]) => <div key={key}><dt>{key}</dt><dd>{String(detail)}</dd></div>)}</dl></details>
  </div>
}

type Equity = { date: string; total_assets: string; cash: string; quantity: number }
type Fill = { date: string; side: string; quantity: number; price: string; fees: string; reason?: string }
export function BacktestDailyDetail({ equity, trades }: { equity: Equity[]; trades: Fill[] }) {
  const [selected, setSelected] = useState('')
  const day = equity.find(row => row.date === selected) || equity.at(-1)
  if (!day) return null
  return <details><summary>按日期查看资产、持仓和成交</summary><label className="field"><span>回测观察日</span><select aria-label="回测观察日" value={day.date} onChange={event => setSelected(event.target.value)}>{equity.map(row => <option key={row.date}>{row.date}</option>)}</select></label><p>收盘总资产 ¥ {day.total_assets} · 现金 ¥ {day.cash} · 持仓 {day.quantity} 股</p>{trades.filter(row => row.date === day.date).map((row, index) => <p key={index}>{row.side === 'buy' ? '买入' : '卖出'} {row.quantity} 股 · 成交价 {row.price} · 费用 ¥ {row.fees} · {row.reason}</p>)}{!trades.some(row => row.date === day.date) && <p className="muted">当日无成交</p>}<p className="muted">此处展示历史已执行结果。下一交易日计划尚未生成，不以之后发生的成交倒填计划。</p></details>
}
