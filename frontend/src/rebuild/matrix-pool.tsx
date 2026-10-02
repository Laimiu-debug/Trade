import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { StrategyPresets } from './strategy-presets'
import { api } from './api'

type Source = { id: string; as_of_date: string; summary: { input: number } }
type Spec = { title: string; type: string; minimum?: number; maximum?: number }
type Strategy = { enabled_in_rebuild?: boolean; id: string; pool_params: Record<string, string> | null; params_schema: Record<string, Spec> }
type Row = { symbol: string; name: string; dataset_id: string; rank: number | null; score: number; in_pool: boolean; signal: boolean; pool_score: number; rank_pool_score: number; ret40_rank: number; components: Record<string, boolean>; reasons: string[] }
type Run = { id: string; as_of_date: string; source_run_id: string; code_sha256: string; summary: { input_count: number; pool_count: number; signal_count: number; excluded_count: number; source_count: number }; request?: { source_run_id: string; strict: boolean; params: Record<string, string> }; result?: { rows: Row[]; excluded: Array<{ symbol: string; reasons: string[] }>; calculation_version: string; limitations: string[]; component_rules: Record<string, string>; quality_flags: string[] } }
const reasonText: Record<string, string> = { CANDIDATE_DATE_MISMATCH: '末根行情与筛选日期不同', HISTORICAL_AVAILABLE_AT_UNKNOWN: '历史可得时间未知', POOL_SCORE_BELOW_MIN: '入池分不足', NO_ENTRY_TRIGGER: '未触发买点', NOT_IN_POOL: '未入池' }

export function MatrixPoolPanel({ onOpenMarket, onPromoted }: { onOpenMarket: (id: string) => void; onPromoted: (id: string) => void }) {
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState('')
  const [strategy, setStrategy] = useState<Strategy | null>(null)
  const [params, setParams] = useState<Record<string, string>>({})
  const [strict, setStrict] = useState(true)
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<Run | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const refresh = useCallback(async () => {
    const [nextSources, nextRuns, catalog] = await Promise.all([
      api<Source[]>('/research/screener-runs'), api<Run[]>('/research/matrix-runs'),
      api<Strategy[]>('/research/strategies'),
    ])
    const descriptor = catalog.find(item => item.id === 'matrix_signal_v1') || null
    setSources(nextSources); setRuns(nextRuns); setStrategy(descriptor)
    setSourceId(current => current || nextSources[0]?.id || '')
    setParams(current => Object.keys(current).length ? current : descriptor?.pool_params || {})
  }, [])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true)
    try {
      const run = await api<Run>('/research/matrix-runs', 'POST', { source_run_id: sourceId, strict, params })
      setSelected(run); await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : '矩阵运行失败') }
    finally { setBusy(false) }
  }
  async function open(id: string) {
    setError('')
    try { setSelected(await api<Run>('/research/matrix-runs/' + id)) }
    catch (err) { setError(err instanceof Error ? err.message : '记录读取失败') }
  }
  async function promote(datasetId: string) {
    if (!selected) return
    setBusy(true); setError('')
    try {
      const signal = await api<{ id: string }>(`/research/matrix-runs/${selected.id}/signals/${datasetId}`, 'POST', {})
      onPromoted(signal.id)
    } catch (err) { setError(err instanceof Error ? err.message : '观察信号保存失败') }
    finally { setBusy(false) }
  }

  return <div><h3>矩阵信号 · 冻结股票池</h3>
    <p className="muted">使用已保存筛选记录的完整输入池，要求收益窗口为 40 日。Top N 仅相对于该输入池；此处沿用旧策略插件的近似指标与评分，向量化矩阵回测尚未接入。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    <form className="form" onSubmit={submit}>
      <label className="field"><span>冻结输入池</span><select aria-label="冻结输入池" required value={sourceId} onChange={event => setSourceId(event.target.value)}>
        {!sources.length && <option value="">请先在四步漏斗保存筛选记录</option>}
        {sources.map(item => <option key={item.id} value={item.id}>{item.as_of_date} · {item.summary.input} 只 · {item.id.slice(0, 12)}</option>)}
      </select></label>
      <label className="check-field"><input type="checkbox" checked={strict} onChange={event => setStrict(event.target.checked)} />严格可得时间：在排名前排除历史可得时间未知的股票</label>
      <p className="muted">末根行情日期与筛选日期不同的股票会单独列出，不参与当日排名。</p>
      {strategy && <StrategyPresets strategyId={strategy.id} params={params} onApply={setParams} />}
      <details><summary>矩阵参数 · {Object.keys(params).length} 项</summary><div className="form-grid">
        {Object.entries(params).map(([key, value]) => <label key={key} className="field"><span>{strategy?.params_schema[key]?.title || key}</span><input type="number" required value={value} min={strategy?.params_schema[key]?.minimum} max={strategy?.params_schema[key]?.maximum} step={strategy?.params_schema[key]?.type === 'integer' ? '1' : 'any'} onChange={event => setParams(current => ({ ...current, [key]: event.target.value }))} /></label>)}
      </div></details>
      {strategy?.enabled_in_rebuild === false && <p className="muted">矩阵策略已停用，新运行请先在系统设置启用；历史仍可查看。</p>}<div className="form-actions"><button className="button primary" disabled={busy || !sourceId || strategy?.enabled_in_rebuild === false}>运行矩阵入池与信号</button><button type="button" className="button secondary" onClick={() => refresh().catch(err => setError(err.message))}>刷新输入池</button></div>
    </form>
    <div className="field"><span>历史矩阵运行</span><select aria-label="历史矩阵运行" value={selected?.id || ''} onChange={event => { if (event.target.value) open(event.target.value) }}><option value="">选择已保存记录</option>{runs.map(item => <option key={item.id} value={item.id}>{item.as_of_date} · {item.summary.signal_count} 个信号 · {item.id.slice(0, 12)}</option>)}</select></div>
    {selected?.result && <div className="market-detail"><h4>矩阵结果 · {selected.as_of_date}</h4>
      <div className="period-summary"><span>源池：{selected.summary.source_count}</span><span>参与排名：{selected.summary.input_count}</span><span>日期 / 可得时间排除：{selected.summary.excluded_count}</span><span>入池：{selected.summary.pool_count}</span><span>买点：{selected.summary.signal_count}</span></div>
      <p className="muted">{selected.result.calculation_version} · 代码摘要 {selected.code_sha256.slice(0, 12)} · 严格模式 {selected.request?.strict ? '启用' : '关闭'}</p>
      <button className="button secondary" type="button" onClick={() => { if (selected.request) { setSourceId(selected.request.source_run_id); setParams(selected.request.params); setStrict(selected.request.strict) } }}>复制这次运行参数</button>
      <p className="muted">S1 使用回撤，S2 使用量能斜率；S3 入池按 Top N，评分沿用旧插件的正收益判断。S5/S6 为旧插件近似买点。评分不等于事件质量分。</p>
      <div className="table-wrap"><table><thead><tr><th>排名 / 代码</th><th>收益排名</th><th>入池 / 买点</th><th>插件评分</th><th>S1 S2 S3 S4 S5 S6 S7</th><th>操作</th></tr></thead><tbody>
        {[...selected.result.rows].sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity)).map(row => <tr key={row.dataset_id}><td>{row.rank ?? '—'} · {row.symbol}<br />{row.name}</td><td>{row.ret40_rank}</td><td>{row.in_pool ? '入池' : '未入池'} / {row.signal ? '触发' : '未触发'}<br /><small>入池条件：{row.pool_score} / 4</small><br /><small>{row.reasons.map(reason => reasonText[reason] || reason).join('、')}</small></td><td>{row.score.toFixed(2)}<br /><small>评分 S3：{row.components.s3_rank ? '通过' : '未通过'}</small></td><td>{['s1', 's2', 's3', 's4', 's5', 's6', 's7'].map(key => row.components[key] ? '✓' : '·').join('　')}</td><td><button className="link-button" onClick={() => onOpenMarket(row.dataset_id)}>查看 K 线</button>{row.signal && <button className="link-button" disabled={busy} onClick={() => promote(row.dataset_id)}>提升为观察信号</button>}</td></tr>)}
      </tbody></table></div>
      {!selected.result.rows.length && <p className="muted">没有可参与同日排名的股票，请查看排除原因或更换输入池。</p>}
      {!!selected.result.excluded.length && <details><summary>查看排除的股票</summary><ul>{selected.result.excluded.map(item => <li key={item.symbol}>{item.symbol}：{item.reasons.map(reason => reasonText[reason] || reason).join('、')}</li>)}</ul></details>}
      <p className="muted">{selected.result.limitations.join('；')}</p>
    </div>}
  </div>
}
