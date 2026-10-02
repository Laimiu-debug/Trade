import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

type Item = { dataset_id: string; symbol: string; provider: string; last_date: string; age_calendar_days: number; stale_calendar_days: boolean; availability_quality: string; content_status: string; bytes: number | null }
type Diagnostics = { dataset_count: number; provider_counts: Record<string, number>; total_bytes: number; verified_contents: number; verification_requested: boolean; missing_or_bad_count: number; stale_calendar_days_count: number; items: Item[] }

export function MarketDiagnostics() {
  const [data, setData] = useState<Diagnostics | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const refresh = useCallback(async (verify: boolean) => {
    setBusy(true); setError('')
    try { setData(await api<Diagnostics>('/market/diagnostics?verify=' + verify)) }
    catch (err) { setError(err instanceof Error ? err.message : '行情诊断失败') }
    finally { setBusy(false) }
  }, [])
  useEffect(() => { refresh(false).catch(() => {}) }, [refresh])
  return <div className="market-detail"><div className="section-heading"><h3>数据状态</h3><button className="button secondary" disabled={busy} onClick={() => refresh(true)}>校验全部文件哈希</button></div>{error && <p className="alert error">{error}</p>}{data && <><div className="period-summary"><span>冻结样本：{data.dataset_count}</span><span>占用空间：{(data.total_bytes / 1024 / 1024).toFixed(2)} MB</span><span>缺失或损坏：{data.missing_or_bad_count}</span><span>距末日超过 7 个自然日：{data.stale_calendar_days_count}</span><span>{data.verification_requested ? `已校验 ${data.verified_contents} 个文件内容` : '文件内容未校验'}</span></div><p className="muted">超过 7 个自然日只提示日期较旧，周末和休市日不等同于同步失败。来源：{Object.entries(data.provider_counts).map(([name, count]) => `${name} ${count}`).join(' · ') || '无'}</p>{data.items.length > 0 && <div className="table-wrap"><table><thead><tr><th>代码</th><th>末日</th><th>自然日龄</th><th>文件</th><th>历史可得时间</th></tr></thead><tbody>{data.items.map(item => <tr key={item.dataset_id}><td>{item.symbol}<br /><small className="muted">{item.dataset_id.slice(0, 12)}</small></td><td>{item.last_date}</td><td>{item.age_calendar_days}</td><td>{({ available: '存在', missing: '缺失', hash_mismatch: '哈希不符', unreadable: '无法读取' } as Record<string, string>)[item.content_status]}</td><td>{item.availability_quality === 'provided_availability' ? '已提供' : '未知'}</td></tr>)}</tbody></table></div>}</>}</div>
}
