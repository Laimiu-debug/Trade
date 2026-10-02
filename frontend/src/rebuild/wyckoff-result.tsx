import type { EventProfileSnapshot } from './event-profiles'

export type FrozenEventProfile = { profile_id: string; revision: number; sha256: string; snapshot: EventProfileSnapshot }
type Props = { indicator: Record<string, unknown>; evaluation?: Record<string, unknown>; eventAge?: Record<string, number>; profile?: FrozenEventProfile }
const stateLabels: Record<string, string> = { confirmed: '已确认', pending: '待后续日线确认', failed: '确认失败', partial: '部分确认', unconfirmed: '未确认' }
const gateLabels: Record<string, string> = { min_score: '入场质量分', min_event_count: '事件数量', require_sequence: '事件序列', health_score_min: '健康分', event_score_min: '事件分', event_grade_min: '事件等级', require_key_event_confirmation: '主要关键事件确认', trigger_date: '事件日期' }
function show(value: unknown) { return value === null || value === undefined || value === '' ? '—' : typeof value === 'boolean' ? value ? '是' : '否' : String(value) }

export function WyckoffResult({ indicator, evaluation, eventAge, profile }: Props) {
  const chain = (indicator.event_chain || []) as Array<{ event: string; date: string; category: string }>
  const confirmations = (indicator.event_confirmation_map || {}) as Record<string, string>
  const grades = (indicator.event_grade_map || {}) as Record<string, string>
  const checks = (evaluation?.checks || []) as Array<{ parameter: string; actual: unknown; threshold: unknown; passed: boolean; reason: string }>
  return <div className="form"><h3>维科夫事件与评分</h3><div className="period-summary">
    <span>阶段：{show(indicator.phase)} · {show(indicator.phase_hint)}</span>
    <span>主事件：{show(indicator.signal)} · {show(indicator.trigger_date)}</span>
    <span>入场质量分：{show(indicator.entry_quality_score)}</span><span>健康分：{show(indicator.health_score)}</span>
    <span>事件分：{show(indicator.event_score)} · {show(indicator.event_grade)} 级</span>
    <span>确认：{stateLabels[String(indicator.confirmation_status)] || show(indicator.confirmation_status)}</span>
    <span>事件序列：{show(indicator.sequence_ok)}</span>
    <span>单股排序参考分：{show(evaluation?.local_score)}（{show(evaluation?.local_score_formula)}）</span>
  </div><p className="muted">确认状态只使用本次决策时已可得的日线。事件发生日不代表确认日；单股参考分不代表在股票池中的排名。</p>
    {profile && <details><summary>本次事件模板：{profile.snapshot.name} · 修订 {profile.revision}</summary><p className="muted">{profile.snapshot.description} · 摘要 {profile.sha256.slice(0, 16)}</p><p>{profile.snapshot.score_mode === 'legacy_formula' ? '经典公式' : '维度加权'} · {profile.snapshot.rule_values.length} 项判定规则</p><pre>{JSON.stringify(profile.snapshot, null, 2)}</pre></details>}
    <div className="table-wrap"><table><thead><tr><th>事件</th><th>日期</th><th>类别</th><th>年龄（交易日）</th><th>确认</th><th>等级</th></tr></thead><tbody>{chain.map((row, index) => <tr key={`${row.event}-${row.date}-${index}`}><td>{row.event}</td><td>{row.date}</td><td>{row.category === 'accumulation' ? '吸筹' : '派发 / 风险'}</td><td>{show(eventAge?.[row.event])}</td><td>{stateLabels[confirmations[row.event]] || '不适用'}</td><td>{show(grades[row.event])}</td></tr>)}</tbody></table></div>{!chain.length && <p className="muted">当前窗口无已识别事件。</p>}
    <details open><summary>观察门槛与拒绝原因</summary><div className="table-wrap"><table><thead><tr><th>门槛</th><th>当前值</th><th>阈值 / 要求</th><th>结果</th></tr></thead><tbody>{checks.map(row => <tr key={row.parameter}><td>{gateLabels[row.parameter] || row.parameter}</td><td>{show(row.actual)}</td><td>{show(row.threshold)}</td><td>{row.passed ? '通过' : row.reason}</td></tr>)}</tbody></table></div></details>
  </div>
}
