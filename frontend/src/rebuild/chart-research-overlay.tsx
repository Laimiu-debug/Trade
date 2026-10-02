import { useEffect, useState } from 'react'
import { api } from './api'
import type { ChartResearchRun } from './chart-indicators'

export function ChartResearchOverlay({ datasetId, onChange }: { datasetId: string; onChange: (runs: ChartResearchRun[]) => void }) {
  const [runs, setRuns] = useState<ChartResearchRun[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    setSelected([]); onChange([]); setRuns([]); setError('')
  }, [datasetId, onChange])
  async function read() {
    setBusy(true); setError('')
    try {
      const values = (await api<ChartResearchRun[]>('/research/runs')).filter(run => run.dataset_id === datasetId && run.result.status === 'computed')
      setRuns(values); setSelected([]); onChange([])
    } catch (err) { setError(err instanceof Error ? err.message : '图层来源读取失败') }
    finally { setBusy(false) }
  }
  function choose(id: string) {
    const next = selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id]
    setSelected(next); onChange(runs.filter(run => next.includes(run.id)))
  }
  return <details><summary>已保存研究的事件与策略图层</summary><p className="muted">只读取此冻结数据集的现有运行；不会创建研究或调用模型。事件的历史发生日与所选决策时的确认状态分开显示，不能把事后确认视为当日已知。</p><button className="button secondary" type="button" disabled={busy} onClick={read}>{busy ? '读取中…' : '读取可叠加研究'}</button>{error && <p className="alert error">{error}</p>}{runs.length > 0 && <div className="table-wrap"><table><thead><tr><th>叠加（最多 8 个）</th><th>策略</th><th>研究决策时间</th><th>源日期 / 质量</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td><input type="checkbox" aria-label={`叠加研究 ${run.id}`} checked={selected.includes(run.id)} disabled={!selected.includes(run.id) && selected.length >= 8} onChange={() => choose(run.id)} /></td><td>{run.strategy_id}<br /><small>{run.strategy_version}</small></td><td>{run.decision_at}</td><td>{run.result.source_date}<br />{run.strict ? '严格可得时间' : '历史可得性可能未知'}<br /><small>{run.result.quality_flags?.join('、')}</small></td></tr>)}</tbody></table></div>}<p className="muted">已读取 {runs.length} 个可用研究。原 A/B/C 标记仅在记录明确提供时显示；未提供时显示“观察”，不推断为买卖指令。</p></details>
}
