import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Choices = { enabled_ids: string[]; default_strategy_id: string | null }
type Strategy = { id: string; name: string; version: string; enabled_in_legacy: boolean; is_legacy_default: boolean; description: string; playbook: unknown; capabilities: Record<string, boolean>; execution_entry: string; params_schema: Record<string, { title?: string; readonly?: boolean; readonly_reason?: string }> }
type Registry = Choices & { revision: number; catalog_sha256: string; defaults: Choices; strategies: Strategy[]; notes: string[] }
type Preview = { revision: number; preview_sha256: string; before: Choices; after: Choices; diff: Array<{ strategy_id: string; name: string; before_enabled: boolean; after_enabled: boolean }>; default_changed: boolean; notes: string[] }
const entryNames: Record<string, string> = { single_symbol: '固定样本 / 单股与组合研究', b1_scanner: 'B1 多周期扫描', matrix_pool: '冻结池矩阵信号' }
const capabilityNames: Record<string, string> = { supports_matrix: '旧矩阵能力', supports_signal_age_filter: '信号年龄过滤', supports_entry_delay: '延迟入场' }

export function StrategyRegistry({ onBusy }: { onBusy?: (busy: boolean) => void }) {
  const [saved, setSaved] = useState<Registry | null>(null), [choices, setChoices] = useState<Choices>({ enabled_ids: [], default_strategy_id: null })
  const [preview, setPreview] = useState<Preview | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [history, setHistory] = useState<Array<{ revision: number; created_at: string; snapshot: Preview }> | null>(null)
  function adopt(value: Registry) { setSaved(value); setChoices({ enabled_ids: value.enabled_ids, default_strategy_id: value.default_strategy_id }); setPreview(null) }
  useEffect(() => { let live = true; api<Registry>('/research/registry').then(value => { if (live) adopt(value) }).catch(err => { if (live) setError(err.message) }); return () => { live = false } }, [])
  async function action(operation: () => Promise<void>) {
    setBusy(true); onBusy?.(true); setError(''); setNotice('')
    try { await operation() } catch (err) { setError(err instanceof Error ? err.message : '策略设置失败'); setPreview(null) } finally { setBusy(false); onBusy?.(false) }
  }
  const name = (id: string | null) => id === null ? '无默认策略' : saved?.strategies.find(row => row.id === id)?.name || id
  const validDefault = choices.enabled_ids.length ? choices.enabled_ids.includes(choices.default_strategy_id || '') : choices.default_strategy_id === null
  return <section className="card"><h2 className="title-with-icon"><Icon name="settings" />策略启用与默认</h2><p className="muted">范围：当前数据目录。启用状态控制新任务提交；默认策略用于新建表单。已冻结任务、历史结果及参数预设继续保留。</p>
    {error && <p role="alert" className="alert error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {saved && <><p>设置版本 {saved.revision} · {saved.strategies.length} 个策略</p><label className="field"><span>新建默认策略</span><select disabled={busy} value={choices.default_strategy_id || ''} onChange={event => { setChoices(current => ({ ...current, default_strategy_id: event.target.value || null })); setPreview(null) }}><option value="">无默认 / 请选择</option>{saved.strategies.filter(row => choices.enabled_ids.includes(row.id)).map(row => <option key={row.id} value={row.id}>{row.name} · {entryNames[row.execution_entry]}</option>)}</select></label>
      {!validDefault && <p className="danger">请为当前启用集合选择一个默认策略；若全部停用，请选择“无默认”。</p>}
      <div className="table-wrap"><table><thead><tr><th>启用新任务</th><th>策略 / 版本</th><th>执行入口</th><th>冻结能力与说明</th></tr></thead><tbody>{saved.strategies.map(row => <tr key={row.id}><td><input type="checkbox" aria-label={`启用策略 ${row.id}`} disabled={busy} checked={choices.enabled_ids.includes(row.id)} onChange={event => { setChoices(current => ({ ...current, enabled_ids: event.target.checked ? [...current.enabled_ids, row.id] : current.enabled_ids.filter(id => id !== row.id) })); setPreview(null) }} /></td><td>{row.name}<br /><small>{row.id} · {row.version}</small></td><td>{entryNames[row.execution_entry]}{row.is_legacy_default && <small> · 旧默认</small>}</td><td><details><summary>元数据、能力和 AI playbook</summary><p>{row.description}</p><p>{Object.entries(row.capabilities).map(([key, value]) => `${capabilityNames[key] || key}：${value ? '有' : '无'}`).join('；')}</p><p>旧启用：{row.enabled_in_legacy ? '是' : '否'}；当前能力仍受各运行器入口限制。</p><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(row.playbook, null, 2)}</pre>{Object.entries(row.params_schema).filter(([, spec]) => spec.readonly).map(([key, spec]) => <p key={key}>{spec.title || key} · 只读：{spec.readonly_reason || '未实现运行参数'}</p>)}</details></td></tr>)}</tbody></table></div>
      <div className="form-actions"><button type="button" disabled={busy || !validDefault} onClick={() => action(async () => setPreview(await api('/research/registry/preview', 'POST', { ...choices, expected_revision: saved.revision })))}>预览策略设置差异</button><button type="button" disabled={busy} onClick={() => { setChoices(saved.defaults); setPreview(null); setNotice('已填入冻结目录默认值，请预览并确认后保存。') }}>填入策略默认值</button><button type="button" disabled={busy} onClick={() => action(async () => adopt(await api('/research/registry')))}>重新读取策略设置</button></div>
      {preview && <div className="market-detail"><h3>策略设置差异 · 基于版本 {preview.revision}</h3><p>默认：{name(preview.before.default_strategy_id)} → {name(preview.after.default_strategy_id)}</p>{preview.diff.length ? <ul>{preview.diff.map(row => <li key={row.strategy_id}>{row.name}：{row.before_enabled ? '启用' : '停用'} → {row.after_enabled ? '启用' : '停用'}</li>)}</ul> : <p>启用集合没有变化。</p>}{preview.notes.map(note => <p key={note} className="muted">{note}</p>)}<button type="button" className="button primary" disabled={busy} onClick={() => action(async () => { const next = await api<Registry>('/research/registry', 'PUT', { ...choices, expected_revision: preview.revision, expected_preview_sha256: preview.preview_sha256 }); adopt(next); setHistory(null); setNotice(`策略设置已保存 · 版本 ${next.revision}`) })}><Icon name="check" />确认保存策略设置</button></div>}
      <button type="button" className="button ghost" disabled={busy} onClick={() => action(async () => setHistory(await api('/research/registry/history')))}>读取策略设置历史</button>{history && <div className="table-wrap"><table><thead><tr><th>版本</th><th>保存时间</th><th>默认策略</th><th>启用数量</th></tr></thead><tbody>{history.map(row => <tr key={row.revision}><td>{row.revision}</td><td>{row.created_at}</td><td>{name(row.snapshot.after.default_strategy_id)}</td><td>{row.snapshot.after.enabled_ids.length}</td></tr>)}</tbody></table></div>}
    </>}
  </section>
}
