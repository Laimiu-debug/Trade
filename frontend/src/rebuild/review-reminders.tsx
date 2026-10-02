import { useEffect, useState } from 'react'
import { api } from './api'

type Row = { date: string; has_trades: boolean; has_cash_flows: boolean; has_review: boolean; has_snapshot: boolean }
type Reminders = { missing_reviews: Row[]; missing_snapshots: Row[]; total_count: { missing_reviews: number; missing_snapshots: number }; limit: number; method: string }
type Props = { accountId: string; revision?: number; showReviews?: boolean; onReview?: (date: string) => void; onSnapshot: (date: string) => void }

export function ReviewReminders({ accountId, revision, showReviews = true, onReview, onSnapshot }: Props) {
  const [value, setValue] = useState<Reminders | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    setValue(null); setError('')
    api<Reminders>(`/accounts/${accountId}/review-reminders`).then(result => { if (active) setValue(result) })
      .catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [accountId, revision, refresh])
  const groups = ([['missing_reviews', '待补复盘', '填写复盘', onReview], ['missing_snapshots', '待补资产快照', '核对资产', onSnapshot]] as const)
    .filter(([key]) => showReviews || key === 'missing_snapshots')
  return <section className="card" aria-label="记录遗漏提醒"><div className="section-heading"><h2>记录遗漏提醒</h2><button className="link-button" onClick={() => setRefresh(current => current + 1)}>刷新提醒</button></div>
    {error && <p className="alert error" role="alert">{error}</p>}{!value && !error && <p role="status">正在检查已保存记录…</p>}
    {value && <><p className="muted">{value.method}</p>{groups.map(([key, title, label, onOpen]) => <div key={key}><h3>{title} · {value.total_count[key]} 日</h3>
      <div className="table-wrap"><table><thead><tr><th>日期</th><th>已有记录</th><th>操作</th></tr></thead><tbody>{value[key].map(row => <tr key={row.date}><td>{row.date}</td><td>{[row.has_trades && '交易', row.has_cash_flows && '资金流水', row.has_review && '复盘', row.has_snapshot && '资产快照'].filter(Boolean).join('、')}</td><td><button className="link-button" disabled={!onOpen} onClick={() => onOpen?.(row.date)}>{label}</button></td></tr>)}</tbody></table></div>
      {!value[key].length && <p className="muted">当前已保存记录没有此类遗漏。</p>}{value.total_count[key] > value.limit && <p className="muted">显示最近 {value.limit} 日，补齐后可刷新查看更早记录。</p>}
    </div>)}</>}
  </section>
}
