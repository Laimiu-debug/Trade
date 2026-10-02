import { useCallback, useEffect, useRef, useState } from 'react'
import { api, apiUpload } from './api'
import { LegacyReportLibrary } from './legacy-report-library'

type Summary = { initial_capital: string; ending_assets: string; total_return: string; max_drawdown: string; trade_count: number; win_rate: string | null; quality_flags: string[] }
type Report = { id: string; title: string; source_run_id: string; origin: 'local' | 'import'; content_sha256: string; created_at: string; strategy_id: string; strategy_version: string; symbol: string; first_date: string; last_date: string; summary: Summary; payload?: {
  title: string; created_at: string; bars_sha256: string; dataset: { bar_count: number; provider: string; availability_quality: string };
  run: { params: Record<string, unknown>; config: Record<string, unknown>; code_sha256: string; execution_version: string; calculation_version: string; result_sha256: string; result: Summary & { limitations: string[]; equity: Array<{ date: string; total_assets: string }>; trades: Array<Record<string, unknown>>; decisions: Array<Record<string, unknown>> } }
} }
const percentage = (value: string | null) => value == null ? '—' : `${(Number(value) * 100).toFixed(2)}%`
const show = (value: unknown): string => value == null ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value)

function ReportChart({ points }: { points: Array<{ date: string; total_assets: string }> }) {
  if (points.length < 2) return null
  const values = points.map(point => Number(point.total_assets))
  const low = Math.min(...values), high = Math.max(...values), spread = Math.max(high - low, 1)
  const path = values.map((value, index) => `${index ? 'L' : 'M'} ${index * 760 / (values.length - 1)} ${150 - (value - low) / spread * 140}`).join(' ')
  return <div className="backtest-chart"><svg viewBox="0 0 760 160" role="img" aria-label="报告冻结资产曲线"><path d={path} fill="none" stroke="currentColor" strokeWidth="2.5" /></svg><div className="period-summary"><span>{points[0].date}</span><span>{points.at(-1)?.date}</span></div></div>
}

export function ReportLibrary({ sourceRunId, onCreated }: { sourceRunId?: string; onCreated?: (reportId: string) => void }) {
  const [reports, setReports] = useState<Report[]>([])
  const [selected, setSelected] = useState<Report | null>(null)
  const [title, setTitle] = useState('单股回测报告')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [deleteId, setDeleteId] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const live = useRef(true)
  const detail = selected?.payload
  const refresh = useCallback(async () => {
    const items = await api<Report[]>('/research/reports')
    if (live.current) setReports(items)
  }, [])
  useEffect(() => {
    live.current = true
    refresh().catch(caught => { if (live.current) setError(caught.message) })
    return () => { live.current = false }
  }, [refresh])
  useEffect(() => { if (sourceRunId) setTitle(`单股回测报告 · ${sourceRunId.slice(0, 8)}`) }, [sourceRunId])

  async function perform(operation: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try { await operation() }
    catch (caught) { if (live.current) setError(caught instanceof Error ? caught.message : '报告操作失败') }
    finally { if (live.current) setBusy(false) }
  }

  return <section className="card span-all" aria-label="研究报告库" style={{ marginTop: 16 }}>
    <h2>研究报告库</h2>
    <p className="muted">保存已完成单股回测的行情、参数、事件模板和结果快照。支持离线 HTML、Excel 与完整 ZIP 报告包；下方可单独导入旧报告与平原档案。</p>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    {sourceRunId ? <div className="form-grid"><label className="field"><span>冻结报告标题</span><input maxLength={120} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label><div className="form-actions"><button type="button" className="button primary" disabled={busy || !title.trim()} onClick={() => perform(async () => {
      const saved = await api<Report>('/research/reports', 'POST', { backtest_run_id: sourceRunId, title: title.trim() })
      await refresh()
      if (live.current) { setSelected(saved); setNotice('完整报告已冻结保存。'); onCreated?.(saved.id) }
    })}>将当前回测保存为报告</button></div></div> : <p className="muted">选择一条已完成的单股回测后，可将其保存为报告。</p>}
    <details style={{ marginTop: 16 }}><summary>导入完整报告包</summary><p className="muted">仅接受本应用导出的完整 ZIP 包，最大 16 MiB。导入结果保留为历史快照，不触发回测或交易。</p><label className="field"><span>研究报告 ZIP 文件</span><input ref={fileInput} type="file" accept=".zip,application/zip" disabled={busy} onChange={event => setFile(event.target.files?.[0] || null)} /></label><button type="button" className="button secondary" disabled={busy || !file} onClick={() => perform(async () => {
      if (!file) return
      if (file.size > 16 * 1024 * 1024) throw new Error('报告包不能超过 16 MiB。')
      const imported = await apiUpload<Report>('/research/reports/import', file)
      await refresh()
      if (live.current) { setSelected(imported); setFile(null); if (fileInput.current) fileInput.current.value = ''; setNotice('报告包已校验并保存。导入报告未重新计算；相同内容会复用已有报告。') }
    })}>校验并导入报告包</button></details>
    <div className="toolbar" style={{ margin: '16px 0' }}><button type="button" className="button secondary" disabled={busy} onClick={() => perform(refresh)}>刷新报告库</button><span className="muted">{reports.length} 份报告</span></div>
    <div className="table-wrap"><table><thead><tr><th>报告</th><th>证券 / 策略</th><th>样本区间</th><th>收益 / 回撤</th><th>来源</th><th>操作</th></tr></thead><tbody>{reports.map(item => <tr key={item.id}><td>{item.title}<br /><small className="muted">{new Date(item.created_at).toLocaleString()}</small></td><td>{item.symbol}<br /><small>{item.strategy_id === 'metadata_unavailable' ? '目录摘要缺失，请查看详情' : item.strategy_id}</small></td><td>{item.first_date}<br />{item.last_date}</td><td>{percentage(item.summary.total_return)} / {percentage(item.summary.max_drawdown)}</td><td>{item.origin === 'import' ? '导入，未重新计算' : '本地冻结'}</td><td><button type="button" className="link-button" disabled={busy} onClick={() => perform(async () => { const report = await api<Report>(`/research/reports/${encodeURIComponent(item.id)}`); if (live.current) { setSelected(report); setDeleteId('') } })}>查看报告</button><button type="button" className="link-button danger" disabled={busy} onClick={() => setDeleteId(item.id)}>删除报告</button></td></tr>)}</tbody></table></div>
    {!reports.length && <p className="muted">暂无已保存报告。</p>}
    {deleteId && <div className="alert error" style={{ marginTop: 12 }}><p>删除“{reports.find(item => item.id === deleteId)?.title}”？来源回测仍可查看。</p><div className="toolbar"><button type="button" className="button secondary danger" disabled={busy} onClick={() => perform(async () => {
      const removed = deleteId
      await api(`/research/reports/${encodeURIComponent(removed)}`, 'DELETE')
      await refresh()
      if (live.current) { setDeleteId(''); if (selected?.id === removed) setSelected(null); setNotice('报告已从列表删除。') }
    })}>确认删除报告</button><button type="button" className="button secondary" disabled={busy} onClick={() => setDeleteId('')}>取消删除报告</button></div></div>}
    {selected && detail && <section aria-label="冻结报告详情" style={{ marginTop: 24 }}><h3>{selected.title}</h3><p className="muted">{selected.origin === 'import' ? '导入报告，未重新计算' : '本地已完成回测快照'} · {selected.symbol} · {selected.strategy_version}</p>
      <div className="toolbar" style={{ flexWrap: 'wrap' }}>{[['export.zip', '下载完整 ZIP 报告包'], ['report.html', '下载离线 HTML 报告'], ['export.xlsx', '下载报告 Excel']].map(([suffix, label]) => <a key={suffix} className="button secondary" href={`/api/v1/research/reports/${encodeURIComponent(selected.id)}/${suffix}`} download>{label}</a>)}</div>
      <div className="period-summary" style={{ marginTop: 16 }}><span>期初 ¥ {selected.summary.initial_capital}</span><span>期末 ¥ {selected.summary.ending_assets}</span><span>收益 {percentage(selected.summary.total_return)}</span><span>最大回撤 {percentage(selected.summary.max_drawdown)}</span><span>完成交易 {selected.summary.trade_count}</span><span>胜率 {percentage(selected.summary.win_rate)}</span></div>
      <ReportChart points={detail.run.result.equity} />
      <p className="muted">{detail.run.result.limitations.join('；')}</p><p className="muted">质量标记：{selected.summary.quality_flags.join('、') || '无额外标记'}</p>
      <details><summary>参数、执行配置与版本</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ params: detail.run.params, config: detail.run.config, execution_version: detail.run.execution_version, calculation_version: detail.run.calculation_version, code_sha256: detail.run.code_sha256, result_sha256: detail.run.result_sha256, bars_sha256: detail.bars_sha256 }, null, 2)}</pre></details>
      <details style={{ marginTop: 12 }}><summary>冻结成交明细 · {detail.run.result.trades.length} 条</summary><div className="table-wrap"><table><thead><tr><th>日期</th><th>方向</th><th>数量</th><th>参考价 / 成交价</th><th>滑点</th><th>费用</th><th>已知时间</th><th>原因</th></tr></thead><tbody>{detail.run.result.trades.map((trade, index) => <tr key={index}><td>{show(trade.date)}</td><td>{trade.side === 'buy' ? '买入' : '卖出'}</td><td>{show(trade.quantity)}</td><td>{show(trade.reference_price)} / {show(trade.price)}</td><td>{show(trade.slippage_rate)}</td><td>{show(trade.fees)}</td><td>{show(trade.known_at)}</td><td>{show(trade.reason)}</td></tr>)}</tbody></table></div></details>
      <p className="muted">报告摘要 {selected.content_sha256.slice(0, 16)} · {detail.dataset.bar_count} 根冻结日线 · 数据来源 {detail.dataset.provider}。包内校验用于内容完整性核对。</p>
    </section>}
    <LegacyReportLibrary />
  </section>
}
