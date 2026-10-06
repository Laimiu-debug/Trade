import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Period = { key: string; start_date: string; end_date: string; return_pct: string | null; return_quality: string; baseline_date: string | null; last_confirmed_date: string | null; confirmed_points: number; coverage_days: number | null; last_nav: string | null; min_drawdown_pct: string | null; node_achievements: Array<{ level: number; first_lit_date: string; days_from_start: number }>; rounds: { closed_rounds: number; winning_rounds: number; losing_rounds: number; closed_pnl: string; win_rate_pct: string | null; payoff_ratio: string | null; profit_factor: string | null; round_ids: string[] } }
type Performance = { projection_status: string; projection_version: string | null; calculation_version: string | null; items: Period[]; method: string }

export function PerformanceSummary({ accountId, onOpenRounds }: { accountId: string; onOpenRounds: () => void }) {
  const [kind, setKind] = useState<'daily' | 'weekly' | 'monthly'>('daily')
  const [data, setData] = useState<Performance | null>(null)
  const [selectedKey, setSelectedKey] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setData(null); setError('')
    api<Performance>(`/accounts/${accountId}/performance?kind=${kind}&limit=24`)
      .then(result => { if (active) { setData(result); setSelectedKey(current => result.items.some(row => row.key === current) ? current : result.items[0]?.key || '') } })
      .catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [accountId, kind])
  const selected = data?.items.find(row => row.key === selectedKey)
  return <section className="card">
    <div className="section-heading"><div><h2 className="title-with-icon"><Icon name="statistics" />收益与回合统计</h2><p>已确认快照计算收益；回合按结束日归属。</p></div><select aria-label="统计周期" value={kind} onChange={event => setKind(event.target.value as 'daily' | 'weekly' | 'monthly')}><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></select></div>
    <div className="toolbar"><a className="button secondary" href={`/api/v1/accounts/${accountId}/exports/performance.pdf?kind=${kind}&limit=24`}>导出统计 PDF</a><a className="button secondary" href={`/api/v1/accounts/${accountId}/exports/performance.xlsx?kind=${kind}&limit=24`}>导出统计 Excel</a><a className="button secondary" href={`/api/v1/accounts/${accountId}/exports/performance/summary.csv?kind=${kind}&limit=24`}>导出汇总 CSV</a></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    <p className="muted">{data?.method} 结果状态：{data?.projection_status ?? '加载中'}；版本：{data?.calculation_version ?? '—'}。</p>
    {data && !data.items.length && <p className="muted">暂无可统计的日期。先录入初始资金和资产快照。</p>}
    <div className="table-wrap"><table><thead><tr><th>周期</th><th>收益率</th><th>快照数</th><th>最大回撤</th><th>已结束回合</th><th>回合盈亏</th></tr></thead><tbody>{data?.items.map(row => <tr key={row.key}><td><button className="link-button" onClick={() => setSelectedKey(row.key)}>{row.key}</button></td><td>{row.return_pct === null ? '缺基线 / 快照' : `${row.return_pct}%${row.return_quality === 'gap' ? ' · 有缺口' : ''}`}</td><td>{row.confirmed_points}</td><td>{row.min_drawdown_pct === null ? '—' : `${row.min_drawdown_pct}%`}</td><td>{row.rounds.closed_rounds}</td><td>¥ {row.rounds.closed_pnl}</td></tr>)}</tbody></table></div>
    {selected && <div className="market-detail"><h3>{selected.key} 详情</h3><div className="period-summary"><span>周期 {selected.start_date} 至 {selected.end_date}</span><span>基线快照 {selected.baseline_date ?? '缺失'}</span><span>末次快照 {selected.last_confirmed_date ?? '缺失'}</span><span>确认快照 {selected.confirmed_points} 个</span><span>胜率 {selected.rounds.win_rate_pct === null ? '—' : `${selected.rounds.win_rate_pct}%`}</span><span>盈亏比 {selected.rounds.payoff_ratio ?? '—'}</span><span>Profit Factor {selected.rounds.profit_factor ?? '—'}</span><span>节点首次达成 {selected.node_achievements.map(row => `第 ${row.level} 级（${row.days_from_start} 天）`).join('、') || '无'}</span></div><p className="muted">结束回合 {selected.rounds.round_ids.length} 个，可在总览的交易回合中追溯成交。</p>{selected.rounds.round_ids.length > 0 && <button className="button secondary" onClick={onOpenRounds}>查看交易回合</button>}</div>}
  </section>
}
