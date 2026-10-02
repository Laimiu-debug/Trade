import { useState } from 'react'
import { apiDownload } from './api'

const funnelColumns: Record<string, string> = {
  dataset_id: '冻结行情 SHA256', as_of_date: '指标日期', score: '分数', ret40: '窗口涨幅（比例）',
  turnover20: '平均换手（比例）', amount20: '平均成交额（元）', amplitude20: '平均振幅（比例）', retrace20: '回撤（比例）',
  pullback_days: '回撤天数', ma10_above_ma20_days: 'MA10 高于 MA20 天数', ma5_above_ma10_days: 'MA5 高于 MA10 天数',
  price_vs_ma20: '偏离 MA20（比例）', vol_slope20: '量能斜率', up_down_volume_ratio: '涨跌量比', pullback_volume_ratio: '回撤量比',
  has_blowoff_top: '放量滞涨', has_divergence_5d: '量价背离', has_upper_shadow_risk: '长上影', ai_confidence: '公式代理置信度',
  theme_stage: '题材阶段', trend_class: '趋势分类', degraded: '质量降级', quality_flags: '质量标记', rejection_stage: '未通过阶段', rejection_reasons: '未通过原因',
}
const b1Columns: Record<string, string> = { dataset_id: '冻结行情 SHA256', close: '收盘价（元）', change_pct: '涨跌幅（百分数）',
  amplitude_pct: '振幅（百分数）', volume_ratio: '量比', kdj_j: 'KDJ J', weekly_macd: '周 MACD', monthly_macd: '月 MACD' }

export function ScreeningExports({ kind, runId, stage, stageLabel, rows }: {
  kind: 'funnel' | 'b1'; runId: string; stage: string; stageLabel: string;
  rows: Array<{ dataset_id: string; symbol: string; name?: string }>;
}) {
  const available = kind === 'funnel' ? funnelColumns : b1Columns
  const [subset, setSubset] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [columns, setColumns] = useState(Object.keys(available))
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  async function download(format: 'pdf' | 'xlsx' | 'csv') {
    setBusy(format); setError('')
    try {
      await apiDownload(`/research/${kind === 'funnel' ? 'screener' : 'b1'}-runs/${runId}/exports/${format}`,
        { stage, dataset_ids: subset ? selected : null, columns: ['symbol', 'name', ...columns] }, `trade-${kind}-${stage}.${format}`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '导出失败') }
    finally { setBusy('') }
  }
  return <section aria-label={`${kind === 'b1' ? 'B1' : '漏斗'}结果导出`}>
    <h4>导出{stageLabel}</h4>
    <p className="muted">冻结记录 {runId.slice(0, 12)} · {subset ? selected.length : rows.length} 只；三种格式使用同一证券与列范围。金额为元；漏斗比例 0.1 = 10%，B1 百分数 10 = 10%。保留已保存精度，空值为未知。</p>
    <label className="check-field"><input type="checkbox" checked={subset} onChange={event => setSubset(event.target.checked)} />手动选择导出证券</label>
    {subset && <details open><summary>所选阶段证券（{selected.length}/{rows.length}）</summary>
      <button type="button" className="link-button" onClick={() => setSelected(rows.map(row => row.dataset_id))}>全选当前阶段</button>{' '}
      <button type="button" className="link-button" onClick={() => setSelected([])}>清空导出选择</button>
      <div className="form-grid">{rows.map(row => <label key={row.dataset_id} className="check-field"><input type="checkbox" aria-label={`导出 ${row.symbol}`}
        checked={selected.includes(row.dataset_id)} onChange={event => setSelected(current => event.target.checked ? [...current, row.dataset_id] : current.filter(id => id !== row.dataset_id))} />{row.symbol} {row.name || ''}</label>)}</div>
    </details>}
    <details><summary>导出列（代码与名称始终保留）</summary><div className="form-grid">{Object.entries(available).map(([key, label]) => <label className="check-field" key={key}><input type="checkbox" checked={columns.includes(key)} onChange={event => setColumns(current => event.target.checked ? [...current, key] : current.filter(item => item !== key))} />{label}</label>)}</div></details>
    <div className="toolbar">{(['pdf', 'xlsx', 'csv'] as const).map(format => <button key={format} type="button" className="button secondary" disabled={Boolean(busy) || subset && !selected.length}
      onClick={() => download(format)}>{busy === format ? '正在导出…' : `导出 ${format === 'xlsx' ? 'Excel' : format.toUpperCase()}`}</button>)}</div>
    {error && <p className="alert error" role="alert">{error}</p>}
  </section>
}
