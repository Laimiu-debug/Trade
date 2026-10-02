import { useCallback, useEffect, useRef, useState } from 'react'
import { api, apiUpload } from './api'
import { PlateauTableDownload, ResearchTradeDownloads } from './research-table-downloads'

type Facts = Record<string, unknown>
type Summary = { strategy_id: string | null; date_from: string | null; date_to: string | null; initial_capital: number | string | null; total_return: number | string | null; max_drawdown: number | string | null; win_rate: number | string | null; reported_trade_count: number | null; trade_rows: number | null; equity_points: number | null; point_count: number | null; point_detail_count: number }
type Preview = { source_format: string; source_sha256: string; source_bytes: number; filename: string; title: string; can_convert: boolean; missing_fields: string[]; quality_flags: string[]; summary: Summary; attachments: Array<{ name: string; bytes: number; sha256: string; untrusted: boolean }>; limitations: string[]; preview_sha256: string }
type LegacyReport = Pick<Preview, 'title' | 'source_format' | 'source_sha256' | 'source_bytes' | 'filename' | 'can_convert' | 'summary' | 'quality_flags'> & { id: string; mode: 'legacy_readonly' | 'archive_only'; created_at: string; content_sha256: string; preview?: Preview; payload?: { run_request: Facts | null; run_result: Facts | null; plateau_result: Facts | null; point_details: Record<string, Facts>; limitations: string[] } }
const ROOT = '/research/legacy-reports'
const text = (value: unknown): string => value == null ? '未知' : typeof value === 'object' ? JSON.stringify(value).slice(0, 2000) : String(value)
const object = (value: unknown): Facts => value && typeof value === 'object' && !Array.isArray(value) ? value as Facts : {}
const rows = (value: unknown): Facts[] => Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)) as Facts[] : []
const percent = (value: unknown) => value == null || !Number.isFinite(Number(value)) ? '未知' : `${(Number(value) * 100).toFixed(2)}%`
const formats: Record<string, string> = { 'final_trade_ftbt_1.0': 'Final Trade · FTBT 1.0', final_trade_plateau_json: 'Final Trade · 平原 JSON', laimiu_print_json: 'LaimiuTrade · 复盘打印 JSON', opaque_html: 'HTML 原件', unrecognized_json: '未识别 JSON 原件' }

function SummaryFacts({ summary }: { summary: Summary }) {
  return <div className="period-summary" style={{ margin: '12px 0' }}><span>原策略 {text(summary.strategy_id)}</span><span>原区间 {text(summary.date_from)} 至 {text(summary.date_to)}</span><span>原初始资金 {text(summary.initial_capital)}</span><span>源收益 {percent(summary.total_return)}</span><span>源回撤 {percent(summary.max_drawdown)}</span><span>成交行 {text(summary.trade_rows)}</span><span>平原点 {text(summary.point_count)} · 点详情 {summary.point_detail_count}</span></div>
}

function JsonFacts({ value, label }: { value: unknown; label: string }) {
  const [expanded, setExpanded] = useState(false)
  return <details onToggle={event => setExpanded(event.currentTarget.open)}><summary>{label}</summary>{expanded && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 400, overflow: 'auto' }}>{JSON.stringify(value, null, 2)?.slice(0, 100000)}{JSON.stringify(value)?.length > 100000 ? '\n显示已截断；完整内容可下载 JSON。' : ''}</pre>}</details>
}

function SourceRun({ result }: { result: Facts }) {
  const [page, setPage] = useState(0)
  const trades = rows(result.trades), equity = rows(result.equity_curve)
  const values = equity.map(point => Number(point.equity))
  const low = Math.min(...values), high = Math.max(...values), spread = Math.max(high - low, 1)
  const path = values.map((value, index) => `${index ? 'L' : 'M'} ${index * 760 / Math.max(1, values.length - 1)} ${150 - (value - low) / spread * 140}`).join(' ')
  return <section aria-label="旧来源回测明细">
    {equity.length > 1 && <div className="backtest-chart"><svg role="img" aria-label="旧报告原资产曲线，未重新计算" viewBox="0 0 760 160"><path d={path} fill="none" stroke="currentColor" strokeWidth="2.5" /></svg><div className="period-summary"><span>{text(equity[0].date)}</span><span>{text(equity.at(-1)?.date)}</span></div></div>}
    <h4>原成交记录 · {trades.length} 条</h4><p className="muted">以下日期、费用及成交价按原报告展示，未重新验证可执行性。</p>
    {!!trades.length && <><div className="table-wrap"><table><thead><tr><th>证券</th><th>信号 / 入场 / 离场</th><th>数量</th><th>入场 / 离场价</th><th>源盈亏</th><th>源收益率</th><th>退出原因</th></tr></thead><tbody>{trades.slice(page * 25, page * 25 + 25).map((trade, index) => <tr key={index}><td>{text(trade.symbol)}<br />{text(trade.name)}</td><td>{text(trade.signal_date)}<br />{text(trade.entry_date)}<br />{text(trade.exit_date)}</td><td>{text(trade.quantity)}</td><td>{text(trade.entry_price)} / {text(trade.exit_price)}</td><td>{text(trade.pnl_amount)}</td><td>{percent(trade.pnl_ratio)}</td><td>{text(trade.exit_reason)}</td></tr>)}</tbody></table></div><div className="toolbar"><button type="button" className="button secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页成交</button><span>{page + 1} / {Math.ceil(trades.length / 25)}</span><button type="button" className="button secondary" disabled={(page + 1) * 25 >= trades.length} onClick={() => setPage(page + 1)}>下一页成交</button></div></>}
    <JsonFacts value={{ stats: result.stats, cost_snapshot: result.cost_snapshot, notes: result.notes, risk_metrics: result.risk_metrics }} label="原统计、费用与说明" />
  </section>
}

function PlateauFacts({ plateau, details, reportId }: { plateau: Facts; details: Record<string, Facts>; reportId: string }) {
  const [page, setPage] = useState(0), [selected, setSelected] = useState<number | null>(null)
  const points = rows(plateau.points)
  const point = selected == null ? null : points[selected]
  const detail = point && typeof point.detail_key === 'string' ? details[point.detail_key] : null
  return <section aria-label="旧平原实验"><h4>原平原实验 · {points.length} 个点</h4><p className="muted">保留原顺序、原得分及失败记录；没有重排、重新评分或续跑。源报告自述完成 {text(plateau.evaluated_combinations)} / {text(plateau.total_combinations)}。</p>
    <div className="table-wrap"><table><thead><tr><th>原序号</th><th>原得分 / 平原分</th><th>源收益 / 回撤</th><th>原硬筛选</th><th>原失败信息</th><th>参数与详情</th></tr></thead><tbody>{points.slice(page * 20, page * 20 + 20).map((item, index) => <tr key={page * 20 + index}><td>{page * 20 + index + 1}</td><td>{text(item.score)} / {text(item.plateau_score)}</td><td>{percent(object(item.stats).total_return)} / {percent(object(item.stats).max_drawdown)}</td><td>{item.passes_hard_filters === true ? '源记录通过' : item.passes_hard_filters === false ? '源记录未通过' : '未知'}</td><td style={{ maxWidth: 300, overflowWrap: 'anywhere' }}>{text(item.error)}</td><td><button type="button" className="link-button" onClick={() => setSelected(page * 20 + index)}>查看旧参数点 {page * 20 + index + 1}</button></td></tr>)}</tbody></table></div>
    {points.length > 20 && <div className="toolbar"><button type="button" className="button secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页参数点</button><span>{page + 1} / {Math.ceil(points.length / 20)}</span><button type="button" className="button secondary" disabled={(page + 1) * 20 >= points.length} onClick={() => setPage(page + 1)}>下一页参数点</button></div>}
    {point && <section aria-label="旧参数点详情" style={{ marginTop: 16 }}><h4>原参数点 {(selected ?? 0) + 1}</h4><div className="table-wrap"><table><thead><tr><th>原参数</th><th>值</th></tr></thead><tbody>{Object.entries(object(point.params)).map(([key, value]) => <tr key={key}><td>{key}</td><td>{text(value)}</td></tr>)}</tbody></table></div><JsonFacts value={point} label="原参数点全部字段" />{detail ? <><p className="muted">点详情已通过包内 SHA、明细键及参数关联校验；不表示行情和执行逻辑已经核验。</p><ResearchTradeDownloads kind="legacy" id={reportId} detailKey={String(point.detail_key)} /><SourceRun key={String(point.detail_key)} result={object(detail.run_result)} /><JsonFacts value={detail.run_request} label="此点原请求" /></> : <p className="muted">原包未提供此点完整回测详情；未生成替代结果。</p>}</section>}
    <JsonFacts value={{ best_point: plateau.best_point, recommended_point: plateau.recommended_point, peak_point: plateau.peak_point, regions: plateau.regions, correlations: plateau.correlations, notes: plateau.notes }} label="原推荐点、区域与相关性记录" />
  </section>
}

export function LegacyReportLibrary() {
  const [reports, setReports] = useState<LegacyReport[]>([]), [selected, setSelected] = useState<LegacyReport | null>(null)
  const [file, setFile] = useState<File | null>(null), [preview, setPreview] = useState<Preview | null>(null), [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [deleteId, setDeleteId] = useState('')
  const live = useRef(true), fileInput = useRef<HTMLInputElement>(null)
  const refresh = useCallback(async () => { const data = await api<LegacyReport[]>(ROOT); if (live.current) setReports(data) }, [])
  useEffect(() => { live.current = true; refresh().catch(caught => { if (live.current) setError(caught.message) }); return () => { live.current = false } }, [refresh])
  async function perform(action: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (caught) { if (live.current) setError(caught instanceof Error ? caught.message : '旧报告操作失败') }
    finally { if (live.current) setBusy(false) }
  }
  const payload = selected?.payload, originalPreview = selected?.preview
  const href = (suffix: string) => `/api/v1${ROOT}/${encodeURIComponent(selected?.id || '')}/${suffix}`
  return <section aria-label="旧报告与平原档案" style={{ marginTop: 28, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
    <h3>旧报告与平原档案</h3><p className="muted">导入 Final Trade 的 .ftbt 报告包或完整平原 JSON。LaimiuTrade 打印资料与单独 HTML 可存原件。旧统计仅供查阅，缺少冻结行情与代码证据时不能续跑。</p>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert success" role="status">{notice}</div>}
    <label className="field"><span>旧 FTBT / 平原 JSON / HTML 文件</span><input ref={fileInput} type="file" accept=".ftbt,.zip,.json,.html,.htm" disabled={busy} onChange={event => { setFile(event.target.files?.[0] || null); setPreview(null); setAck(false); setError(''); setNotice('') }} /></label>
    <div className="toolbar" style={{ marginTop: 12 }}><button type="button" className="button secondary" disabled={busy || !file} onClick={() => perform(async () => { if (!file) return; if (file.size > 16 * 1024 * 1024) throw new Error('旧报告不能超过 16 MiB。'); const data = await apiUpload<Preview>(ROOT + '/preview', file); if (live.current) { setPreview(data); setAck(false) } })}>预览旧报告</button><button type="button" className="button secondary" disabled={busy} onClick={() => perform(refresh)}>刷新旧档案</button></div>
    {preview && <section aria-label="旧报告导入预览" style={{ marginTop: 16 }}><h4>{preview.title}</h4><p>{formats[preview.source_format] || preview.source_format} · {preview.source_bytes.toLocaleString()} 字节 · {preview.can_convert ? '可转为只读结构报告' : '结构信息不足，仅可保存原件'}</p><SummaryFacts summary={preview.summary} /><ul>{preview.limitations.map(item => <li key={item}>{item}</li>)}</ul>{!!preview.missing_fields.length && <div className="alert error"><strong>缺失字段</strong><ul>{preview.missing_fields.map(item => <li key={item} style={{ overflowWrap: 'anywhere' }}>{item}</li>)}</ul></div>}<p className="muted" style={{ overflowWrap: 'anywhere' }}>源 SHA-256 {preview.source_sha256}</p><label className="field"><span><input type="checkbox" checked={ack} disabled={busy} onChange={event => setAck(event.target.checked)} /> 我已核对预览，接受来源指标未重新计算及原件仅供存档的限制</span></label><button type="button" className="button primary" disabled={busy || !ack} onClick={() => perform(async () => { if (!file) return; const data = await apiUpload<LegacyReport>(ROOT + '/import', file, { expected_preview_sha256: preview.preview_sha256, mode: preview.can_convert ? 'legacy_readonly' : 'archive_only', acknowledge_limitations: 'true' }); await refresh(); if (live.current) { setSelected(data); setPreview(null); setAck(false); setFile(null); if (fileInput.current) fileInput.current.value = ''; setNotice('旧档案已保存；同一原件复用已有记录及首次保存方式。未创建回测任务。') } })}>{preview.can_convert ? '确认保存只读旧报告' : '确认仅保存原件'}</button></section>}
    <p className="muted">最近 {reports.length} 份旧档案；读取历史不会运行回测。</p><div className="table-wrap"><table><thead><tr><th>档案</th><th>原来源 / 保存方式</th><th>原区间</th><th>源收益 / 平原点数</th><th>操作</th></tr></thead><tbody>{reports.map(item => <tr key={item.id}><td>{item.title}</td><td>{formats[item.source_format] || item.source_format}<br />{item.mode === 'legacy_readonly' ? '只读结构报告' : '仅存原件'}</td><td>{text(item.summary.date_from)}<br />{text(item.summary.date_to)}</td><td>{percent(item.summary.total_return)} / {text(item.summary.point_count)}</td><td><button type="button" className="link-button" disabled={busy} onClick={() => perform(async () => { const data = await api<LegacyReport>(ROOT + '/' + encodeURIComponent(item.id)); if (live.current) { setSelected(data); setDeleteId('') } })}>查看旧档案</button><button type="button" className="link-button danger" disabled={busy} onClick={() => setDeleteId(item.id)}>删除旧档案</button></td></tr>)}</tbody></table></div>
    {deleteId && <div className="alert error"><p>从列表删除“{reports.find(item => item.id === deleteId)?.title}”？再次导入同一原件可恢复此记录。</p><div className="toolbar"><button type="button" className="button secondary danger" disabled={busy} onClick={() => perform(async () => { await api(ROOT + '/' + encodeURIComponent(deleteId), 'DELETE'); await refresh(); if (live.current) { if (selected?.id === deleteId) setSelected(null); setDeleteId(''); setNotice('旧档案已从列表删除。') } })}>确认删除旧档案</button><button type="button" className="button secondary" disabled={busy} onClick={() => setDeleteId('')}>取消删除旧档案</button></div></div>}
    {selected && payload && originalPreview && <section key={selected.id} aria-label="旧档案详情" style={{ marginTop: 24 }}><h3>{selected.title}</h3><p className="muted">{selected.mode === 'legacy_readonly' ? 'legacy-readonly · 只读结构报告' : 'archive-only · 原件存档'} · {new Date(selected.created_at).toLocaleString()}</p><SummaryFacts summary={selected.summary} /><ul>{payload.limitations.map(item => <li key={item}>{item}</li>)}</ul><div className="toolbar" style={{ flexWrap: 'wrap' }}><a className="button secondary" href={href('export.json')} download>下载转换 JSON</a><a className="button secondary" href={href('report.html')} download>下载安全摘要 HTML</a><a className="button secondary" href={href('original.bin')} download>下载旧原包（不在应用内打开）</a></div>
      {selected.mode === 'legacy_readonly' && selected.can_convert && <>{payload.run_result && <><ResearchTradeDownloads kind="legacy" id={selected.id} /><SourceRun result={payload.run_result} /></>}{payload.run_request && <JsonFacts value={payload.run_request} label="原报告请求参数" />}{payload.plateau_result && <><PlateauTableDownload legacy id={selected.id} /><PlateauFacts plateau={payload.plateau_result} details={payload.point_details} reportId={selected.id} /></>}</>}
      {!!originalPreview.missing_fields.length && <div className="alert error"><strong>无法转换的缺失字段</strong><ul>{originalPreview.missing_fields.map(item => <li key={item} style={{ overflowWrap: 'anywhere' }}>{item}</li>)}</ul></div>}
      <details style={{ marginTop: 16 }}><summary>原附件下载与完整性摘要 · {originalPreview.attachments.length} 个</summary><p className="muted">原 HTML、Excel 均按二进制附件下载。应用不会打开原 HTML，也不会执行公式或外链。</p><div className="table-wrap"><table><thead><tr><th>名称</th><th>字节</th><th>SHA-256</th><th>操作</th></tr></thead><tbody>{originalPreview.attachments.map(item => <tr key={item.name}><td>{item.name}</td><td>{item.bytes.toLocaleString()}</td><td style={{ maxWidth: 220, overflowWrap: 'anywhere' }}>{item.sha256}</td><td><a href={href('original.bin?name=' + encodeURIComponent(item.name))} download>下载原件 {item.name}</a></td></tr>)}</tbody></table></div></details><p className="muted" style={{ overflowWrap: 'anywhere' }}>源 SHA-256 {selected.source_sha256}<br />转换快照 SHA-256 {selected.content_sha256}<br />质量标记：{selected.quality_flags.join('、')}</p>
    </section>}
  </section>
}
