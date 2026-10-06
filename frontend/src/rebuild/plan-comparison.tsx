import { useEffect, useState } from 'react'
import { api, type Trade } from './api'
import { Icon } from './workspace-icons'

type Plan = { written_on: string; target_date: string; forecast: string; position_plan: string; risk_plan: string; watchlist: Array<{ code: string; name: string; condition: string; action: string; matching_trade_ids: string[] }>; rehearsal: Array<{ code: string; name: string; qty: number | null; explicit_target: boolean; actual_qty: number | null; quantity_delta: number | null; quality: string }>; calendar?: { target_status: 'open' | 'closed' | 'unknown' } }
type Comparison = { date: string; plans: Plan[]; actual_trades: Trade[]; snapshot: { total_assets: string; available_cash: string | null; position_count: number } | null; actual_market_observation: string | null; method: string }

export function PlanComparison({ accountId, day }: { accountId: string; day: string }) {
  const [data, setData] = useState<Comparison | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setData(null)
    api<Comparison>(`/accounts/${accountId}/plan-comparison/${day}`)
      .then(result => { if (active) { setData(result); setError('') } })
      .catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [accountId, day, revision])
  return <section className="card"><div className="section-heading"><div><h2 className="title-with-icon"><Icon name="period" />昨日计划与今日实际</h2><p>查看此前指定今天执行的手工计划。</p></div><button className="button secondary" onClick={() => setRevision(value => value + 1)}><Icon name="refresh" />刷新</button></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    <p className="muted">{data?.method}</p>
    {data && !data.plans.length && <p className="muted">没有指向 {day} 的已保存计划。</p>}
    {data?.plans.map(plan => <div className="market-detail" key={plan.written_on}><h3>{plan.written_on} 编写 · {plan.target_date} 执行</h3><p className="muted">执行日日历：{plan.calendar?.target_status === 'open' ? '本地日历开市' : plan.calendar?.target_status === 'closed' ? '本地日历休市' : '未知，人工指定日期'}</p><p>大盘预判：{plan.forecast || '未填写'}</p><p>今日市场观察：{data.actual_market_observation || '未填写'}</p><p>仓位计划：{plan.position_plan || '未填写'}；风险预案：{plan.risk_plan || '未填写'}</p>
      <div className="table-wrap"><table><thead><tr><th>关注股</th><th>触发条件</th><th>计划动作</th><th>实际成交</th></tr></thead><tbody>{plan.watchlist.map((row, index) => <tr key={`${row.code}:${index}`}><td>{row.code} {row.name}</td><td>{row.condition}</td><td>{row.action}</td><td>{row.matching_trade_ids.length} 笔</td></tr>)}</tbody></table></div>
      <div className="table-wrap"><table><thead><tr><th>预演持仓</th><th>计划股数</th><th>快照股数</th><th>差额</th></tr></thead><tbody>{plan.rehearsal.map((row, index) => <tr key={`${row.code}:${index}`}><td>{row.code} {row.name}</td><td>{row.qty ?? '未列入计划'}</td><td>{row.actual_qty ?? '缺快照'}</td><td>{row.quantity_delta ?? '—'}</td></tr>)}</tbody></table></div>
    </div>)}
    {data && <p className="muted">当日成交 {data.actual_trades.length} 笔；{data.snapshot ? `确认快照资产 ¥ ${data.snapshot.total_assets}，现金 ${data.snapshot.available_cash === null ? '未填写' : `¥ ${data.snapshot.available_cash}`}` : '未录入当日确认快照'}。</p>}
  </section>
}
