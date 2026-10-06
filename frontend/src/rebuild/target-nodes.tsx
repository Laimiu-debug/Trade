import { useCallback, useEffect, useState } from 'react'
import { api, type Analytics } from './api'
import { Icon } from './workspace-icons'

type Config = { version: number; multiplier: string; node_count: number }
type Nav = NonNullable<Analytics['result']>['nav']

export function TargetNodes({ accountId, nav, onChanged }: { accountId: string; nav: Nav | null; onChanged: () => Promise<void> }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(async () => setConfig(await api<Config>(`/accounts/${accountId}/target-config`)), [accountId])
  useEffect(() => { load().catch(err => setError(err.message)) }, [load])
  const visibleNav = nav?.target_config ? nav : null
  const currentSegment = visibleNav?.node_events?.at(-1)?.segment ?? visibleNav?.first_achievements?.at(-1)?.segment ?? 1
  const first = visibleNav?.first_achievements?.filter(row => row.segment === currentSegment) || []
  async function save() {
    if (!config) return
    setBusy(true); setError('')
    try {
      setConfig(await api<Config>(`/accounts/${accountId}/target-config`, 'PUT',
        { expected_version: config.version, multiplier: config.multiplier, node_count: config.node_count }))
      setEditing(false); await onChanged()
    } catch (err) { setError(err instanceof Error ? err.message : '保存目标节点失败') }
    finally { setBusy(false) }
  }
  return <section className="card span-all"><div className="section-heading"><div><h2 className="title-with-icon"><Icon name="target" />目标节点</h2><p>点亮和熄灭由确认的净值计算。</p></div><button className="button secondary" onClick={() => setEditing(!editing)}>{editing ? '收起配置' : '配置节点'}</button></div>
    {error && <p className="danger" role="alert">{error}</p>}
    {visibleNav && <><div className="period-summary"><span>计算版本 {visibleNav.target_config.version}</span><span>已点亮 {visibleNav.current.lit_count} / {visibleNav.target_config.node_count}</span><span>下一节点净值 {visibleNav.current.next_threshold || '—'}</span><span>目标金额 ¥ {visibleNav.current.next_target_assets || '—'}</span><span>还差 ¥ {visibleNav.current.next_gap_amount || '—'}</span></div><div className="node-grid" aria-label="目标节点进度">{Array.from({ length: visibleNav.target_config.node_count }, (_, i) => <span key={i} className={i < visibleNav.current.lit_count ? 'node lit' : 'node'} title={`第 ${i + 1} 级 · 净值 ${Number(visibleNav.target_config.multiplier) ** (i + 1)}`}>{i + 1}</span>)}</div>{first.length > 0 && <details><summary>首次达成记录（{first.length}）</summary><div className="table-wrap"><table><thead><tr><th>节点</th><th>首次达成</th><th>距起始日</th></tr></thead><tbody>{first.map(row => <tr key={`${row.segment}:${row.level}`}><td>第 {row.level} 级</td><td>{row.first_lit_date}</td><td>{row.days_from_start} 天</td></tr>)}</tbody></table></div></details>}</>}
    {editing && config && <div className="form"><p className="muted">保存后以新版本重新计算所有历史节点事件；旧计算结果保留为历史版本。</p><div className="form-grid"><label className="field">每级倍率<input type="number" min="1.01" max="10" step="0.01" value={config.multiplier} onChange={event => setConfig({ ...config, multiplier: event.target.value })} /></label><label className="field">节点数量<input type="number" min="1" max="100" step="1" value={config.node_count} onChange={event => setConfig({ ...config, node_count: Number(event.target.value) })} /></label></div><div className="form-actions"><button className="button primary" disabled={busy} onClick={save}><Icon name="save" />保存节点配置</button></div></div>}
  </section>
}
