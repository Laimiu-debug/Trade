import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Analysis = { id: string; status: string; kind: string; created_at: string; target: { dataset_id?: string; decision_at?: string }; output?: { summary: string; confidence: number; conclusion: string; breakout_date: string | null; rise_reasons: string[] } }

export function StockAIOverlay({ datasetId, accountId, manualDate, onDate }: { datasetId: string; accountId: string; manualDate: string | null; onDate: (day: string | null) => void }) {
  const [scope, setScope] = useState(accountId)
  const requestId = useRef(0)
  const [rows, setRows] = useState<Analysis[]>([])
  const [selected, setSelected] = useState<Analysis | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const suffix = scope ? '?account_id=' + encodeURIComponent(scope) : ''
  useEffect(() => {
    let live = true
    requestId.current += 1; setRows([]); setSelected(null); setError(''); onDate(null)
    api<Analysis[]>('/ai/generations' + suffix).then(items => {
      if (live) setRows(items.filter(row => row.kind === 'stock_analysis' && row.target.dataset_id === datasetId && ['draft', 'accepted'].includes(row.status)))
    }).catch(err => { if (live) setError(err instanceof Error ? err.message : 'AI 分析记录读取失败') })
    return () => { live = false; requestId.current += 1 }
  }, [datasetId, suffix, refresh, onDate])
  useEffect(() => { onDate(selected?.output?.breakout_date || null) }, [selected, onDate])

  return <details className="market-detail"><summary>人工与 AI 日期对照</summary><p className="muted">选择同一冻结样本的已校验分析，在图表叠加其候选起爆日。人工标注单独保存。</p>
    {error && <p className="danger" role="alert">{error}</p>}
    <div className="form-grid"><label className="field"><span>分析记录范围</span><select aria-label="图表 AI 分析范围" value={scope} onChange={event => setScope(event.target.value)}><option value={accountId}>当前账户</option><option value="">不绑定账户</option></select></label><label className="field"><span>选择 AI 分析记录</span><select aria-label="图表 AI 分析记录" value={selected?.id || ''} onChange={async event => {
      const id = event.target.value; const stamp = ++requestId.current; setError(''); setSelected(null)
      if (!id) return
      try { const result = await api<Analysis>('/ai/generations/' + id + suffix); if (stamp === requestId.current) setSelected(result) }
      catch (err) { if (stamp === requestId.current) setError(err instanceof Error ? err.message : '分析读取失败') }
    }}><option value="">不叠加 AI 日期</option>{rows.map(row => <option key={row.id} value={row.id}>{row.created_at.slice(0, 19)} · {row.status === 'accepted' ? '已保留' : '待核对'} · {row.id.slice(0, 8)}</option>)}</select></label></div>
    <button type="button" className="button secondary" onClick={() => setRefresh(value => value + 1)}><Icon name="refresh" />刷新分析记录</button>
    {selected?.output ? <div><p>人工启动日：{manualDate || '未标注'} · AI 候选日：{selected.output.breakout_date || '未给出'}{manualDate && selected.output.breakout_date && ` · ${manualDate === selected.output.breakout_date ? '日期一致' : '日期不同，请结合依据核对'}`}</p><p>{selected.output.summary}</p><p className="muted">结论：{selected.output.conclusion} · 模型自述置信度 {(selected.output.confidence * 100).toFixed(0)}% · 决策时刻 {selected.target.decision_at}</p><ul>{selected.output.rise_reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></div> : <p className="muted">{rows.length ? '请选择一条分析查看依据。' : '当前范围尚无此样本的已校验分析，可在 AI 工作台“识别与复盘”生成。'}</p>}
  </details>
}
