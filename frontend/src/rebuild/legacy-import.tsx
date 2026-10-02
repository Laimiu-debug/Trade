import { LegacySupplementEditor } from './legacy-supplements'
import { LegacySimPromotion } from './legacy-sim-promotion'
import { LegacyAttachmentEditor } from './legacy-attachments'
import { useEffect, useState } from 'react'
import { api } from './api'

type Inventory = { section: string; count: number; target: string; field_map: Record<string, string>; note: string }
type Preview = {
  source: string; filename: string; source_sha256: string; source_bytes: number; logical_sha256: string;
  redacted_paths: string[]; mode: 'archive_only' | 'new_real_account'; account_name: string | null;
  inventory: Inventory[]; records: Array<{ section: string; source_id: string; data: Record<string, unknown> }>;
  errors: Array<{ section: string; source_id: string; message: string }>; notes: string[];
  mapped_record_count: number; can_import: boolean; preview_sha256: string;
  promotion_of?: string; expected_revision?: number;
}
type Summary = { id: string; source_kind: string; filename: string; source_sha256: string; logical_sha256: string; account_id: string | null; account_name: string | null; mode: string; created_at: string; revision: number; promoted: boolean; redaction_count: number; mapped_record_count: number; sim_promotion?: { account_id: string } | null }
type Detail = Summary & { preview: Preview; archive: Record<string, unknown>; mappings: Array<{ section: string; source_id: string; target_table: string; target_id: string }> }
type Upload = { filename: string; content_base64: string }
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
const codeStyle = { whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, maxHeight: '26rem', overflow: 'auto' }
const sourceNames: Record<string, string> = { laimiu_json: 'LaimiuTrade JSON 备份', laimiu_sqlite: 'LaimiuTrade SQLite 账本', final_app_json: 'final-trade 应用资料', final_sim_json: 'final-trade 模拟资料', final_event_sqlite: 'final-trade 事件仓' }

function ArchiveBrowser({ data }: { data: Record<string, unknown> }) {
  const [section, setSection] = useState(Object.keys(data)[0] || '')
  const [page, setPage] = useState(0)
  const value = data[section]
  const entries = Array.isArray(value) ? value.map((item, index) => [String(index + 1), item] as const) : value !== null && typeof value === 'object' ? Object.entries(value) : [['值', value] as const]
  return <div><label className="field"><span>档案资料分组</span><select value={section} onChange={event => { setSection(event.target.value); setPage(0) }}>{Object.keys(data).map(key => <option key={key}>{key}</option>)}</select></label><p className="subtle">{entries.length} 项 · 只读逻辑内容，未写入当前运行配置。JSON 字符串保留旧存储结构。</p>
    {entries.slice(page * 25, (page + 1) * 25).map(([key, item]) => <details key={key}><summary>{section} / {key}</summary><pre style={codeStyle}>{pretty(item)}</pre></details>)}
    {entries.length > 25 && <div className="toolbar"><button className="button secondary" disabled={page === 0} onClick={() => setPage(previous => previous - 1)}>上一页</button><span>{page + 1} / {Math.ceil(entries.length / 25)}</span><button className="button secondary" disabled={(page + 1) * 25 >= entries.length} onClick={() => setPage(previous => previous + 1)}>下一页</button></div>}
  </div>
}

export function LegacyImportWorkspace({ onImported, onBusy }: { onImported?: (accountId: string) => void; onBusy?: (busy: boolean) => void }) {
  const [upload, setUpload] = useState<Upload | null>(null)
  const [mode, setMode] = useState<Preview['mode']>('archive_only')
  const [name, setName] = useState('旧实盘账本副本')
  const [promotionName, setPromotionName] = useState('旧档案转换账户')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [history, setHistory] = useState<Summary[]>([])
  const [selected, setSelected] = useState<Detail | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => { let alive = true; api<Summary[]>('/legacy-imports').then(value => { if (alive) setHistory(value) }).catch(err => { if (alive) setError(err.message) }); return () => { alive = false } }, [])
  function invalidate() { setPreview(null); setAcknowledged(false); setError(''); setNotice('') }
  async function work(action: () => Promise<void>) {
    setBusy(true); onBusy?.(true); setError('')
    try { await action() } catch (err) { setError(err instanceof Error ? err.message : '旧资料处理失败') } finally { setBusy(false); onBusy?.(false) }
  }
  async function read(file: File) {
    invalidate(); setUpload(null)
    if (file.size > 16 * 1024 * 1024 || file.size === 0) throw new Error('请选择 0–16 MiB 范围内的 JSON / SQLite 文件')
    const bytes = new Uint8Array(await file.arrayBuffer())
    let binary = ''
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768))
    setUpload({ filename: file.name, content_base64: btoa(binary) })
  }
  function body() { return { ...upload, mode, account_name: mode === 'new_real_account' ? name : '' } }
  async function confirm() {
    if (!preview || (!upload && !preview.promotion_of) || !acknowledged) return
    const result = preview.promotion_of
      ? await api<Summary>(`/legacy-imports/${preview.promotion_of}/promote`, 'POST', { expected_revision: preview.expected_revision, account_name: preview.account_name, expected_preview_sha256: preview.preview_sha256, acknowledge_limitations: acknowledged })
      : await api<Summary>('/legacy-imports', 'POST', { ...body(), expected_preview_sha256: preview.preview_sha256, acknowledge_limitations: acknowledged })
    // Original file bytes only stay in memory until the explicit operation completes.
    setUpload(null); setPreview(null); setAcknowledged(false)
    setHistory(await api<Summary[]>('/legacy-imports'))
    setSelected(await api<Detail>(`/legacy-imports/${result.id}`))
    setNotice(result.account_id ? `已新建账户「${result.account_name}」。请在账户选择器切换查看；原账户保持原状。` : '已保存独立只读旧档案；没有建立实盘账户或可交易模拟状态。')
    if (result.account_id) onImported?.(result.account_id)
  }
  return <div className="two-col wide-left"><section className="card span-all"><h2>旧资料导入</h2><p>LaimiuTrade JSON / SQLite、final-trade app_state.json / sim_state.json / 事件仓 SQLite。请先退出旧软件并导出一致性文件，SQLite 的未合并 WAL 不包含在单个数据库文件中。</p><p className="subtle">上传只发给本地服务，不调用 AI 或行情网络。原文件不改动；最多 16 MiB、5000 条顶层记录。原始字节不存入新库，只存脱敏逻辑内容与原 SHA256。</p>
    {error && <p role="alert" className="alert error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <fieldset disabled={busy} style={{ minWidth: 0, border: 0, padding: 0 }}><legend>选择与映射</legend><div className="form-grid"><label className="field"><span>旧资料文件</span><input type="file" accept=".json,.sqlite,.sqlite3,.db" onChange={event => { const file = event.target.files?.[0]; if (file) void work(() => read(file)); event.target.value = '' }} /></label><label className="field"><span>导入方式</span><select value={mode} onChange={event => { setMode(event.target.value as Preview['mode']); invalidate() }}><option value="archive_only">仅保存只读档案</option><option value="new_real_account">Laimiu 实盘核心 → 新账户</option></select></label>{mode === 'new_real_account' && <label className="field"><span>新账户名称</span><input value={name} maxLength={80} onChange={event => { setName(event.target.value); invalidate() }} /></label>}</div>
      {upload && <p>已选择：{upload.filename}，尚未保存。</p>}<button className="button primary" disabled={!upload || (mode === 'new_real_account' && !name.trim())} onClick={() => void work(async () => { setPreview(await api<Preview>('/legacy-imports/preview', 'POST', body())); setAcknowledged(false) })}>预览字段与来源映射</button>
    </fieldset>
    {preview && <div className="market-detail"><h3>{preview.promotion_of ? '档案转换预览' : '导入预览'} · {sourceNames[preview.source] || preview.source}</h3><p>目标：{preview.mode === 'new_real_account' ? `独立新实盘账户「${preview.account_name}」` : '独立只读旧档案'} · 核心映射 {preview.mapped_record_count} 条{preview.promotion_of && ` · 档案版本 ${preview.expected_revision}`}</p><p className="subtle" style={{ overflowWrap: 'anywhere' }}>原文件 SHA256：{preview.source_sha256}<br />脱敏逻辑 SHA256：{preview.logical_sha256}</p>
      <div className="table-wrap"><table><thead><tr><th>来源分组</th><th>数量</th><th>目标与字段映射</th></tr></thead><tbody>{preview.inventory.map(item => <tr key={item.section}><td>{item.section}</td><td>{item.count}</td><td>{item.target === 'archive_only' ? '只读档案' : '新账户核心记录'}<p className="subtle">{item.note}</p>{Object.keys(item.field_map).length > 0 && <details><summary>字段对应</summary><pre style={codeStyle}>{pretty(item.field_map)}</pre></details>}</td></tr>)}</tbody></table></div>
      <details><summary>映射后的具体记录（核对金额、证券身份、日期与评分）</summary><ArchiveBrowser key={preview.preview_sha256} data={Object.fromEntries(preview.inventory.filter(item => item.target !== 'archive_only').map(item => [item.section, preview.records.filter(row => row.section === item.section).map(row => row.data)]))} /></details>
      <h4>导入边界与脱敏</h4>{preview.notes.map(note => <p className="muted" key={note}>{note}</p>)}<p>检测并脱敏 {preview.redacted_paths.length} 处 credential 字段。</p>{preview.redacted_paths.length > 0 && <details><summary>脱敏字段路径</summary><pre style={codeStyle}>{pretty(preview.redacted_paths)}</pre></details>}
      {preview.errors.length > 0 && <div role="alert" className="alert error"><strong>以下问题阻止核心事实导入。可以修正源文件副本，或改选只读档案重新预览。</strong>{preview.errors.map((item, index) => <p key={index}>{item.section} / {item.source_id}：{item.message}</p>)}</div>}
      <label className="field"><span><input type="checkbox" checked={acknowledged} disabled={busy || !preview.can_import} onChange={event => setAcknowledged(event.target.checked)} /> 我已核对映射与未承接内容，确认保存脱敏逻辑档案；它不是原始文件备份</span></label><button className="button primary" disabled={busy || !acknowledged || !preview.can_import} onClick={() => void work(confirm)}>{preview.promotion_of ? '确认从档案建立新账户' : '确认导入此预览'}</button>
    </div>}
  </section><section className="card span-all"><h2>旧资料档案历史</h2><div className="toolbar"><button className="button secondary" disabled={busy} onClick={() => void work(async () => setHistory(await api<Summary[]>('/legacy-imports')))}>刷新档案历史</button><span className="subtle">最多显示最近 200 份；同一原 SHA256 不重复导入，逻辑档案不覆盖。Laimiu 核心可显式转换新实盘账户；完整 final 模拟账本可核验后建立独立新模拟账户，每份各只转换一次。研究资料保持只读。</span></div>
    <div className="table-wrap"><table><thead><tr><th>文件 / 来源</th><th>时间 / 目标</th><th>记录</th><th>操作</th></tr></thead><tbody>{history.map(item => <tr key={item.id}><td>{item.filename}<br /><small>{sourceNames[item.source_kind]}</small></td><td>{item.created_at}<br />{item.account_name || '只读旧档案'}</td><td>{item.mapped_record_count} 条核心映射 · {item.redaction_count} 处脱敏</td><td><button className="button secondary" disabled={busy} onClick={() => void work(async () => setSelected(await api<Detail>(`/legacy-imports/${item.id}`)))}>查看档案</button></td></tr>)}</tbody></table></div>{!history.length && <p className="muted">尚未导入旧资料。</p>}
    {selected && <div className="market-detail"><h3>{selected.filename} · 只读导入凭证</h3><p>档案版本 {selected.revision} · {selected.promoted ? '已显式转换' : selected.account_id ? '导入时已创建新账户' : '尚未转换'}{selected.account_id && ` · 关联账户 ${selected.account_name}（${selected.account_id}）`}</p><p style={{ overflowWrap: 'anywhere' }}>原 SHA256：{selected.source_sha256}<br />逻辑 SHA256：{selected.logical_sha256}</p><p>原字节未留存；下载仅包含脱敏逻辑数据、来源摘要与新旧记录 ID 对应。请自行保留旧软件原文件和附件。</p><a className="button secondary" href={`/api/v1/legacy-imports/${selected.id}/export.json`} download>下载脱敏逻辑档案 JSON</a>
      {!selected.account_id && selected.source_kind.startsWith('laimiu_') && <fieldset disabled={busy} style={{ minWidth: 0 }}><legend>从此档案转换实盘核心</legend><p>重新校验已保存的脱敏逻辑内容。原档案保持，新增独立账户与版本审计；不会恢复密钥、图片或旧运行设置。</p><label className="field"><span>档案转换的新账户名称</span><input value={promotionName} maxLength={80} onChange={event => { setPromotionName(event.target.value); invalidate() }} /></label><button className="button secondary" disabled={!promotionName.trim()} onClick={() => void work(async () => { const value = await api<Preview>(`/legacy-imports/${selected.id}/promotion-preview`, 'POST', { expected_revision: selected.revision, account_name: promotionName }); setUpload(null); setPreview(value); setAcknowledged(false) })}>预览此档案转换</button></fieldset>}
      {selected.source_kind === 'final_sim_json' && <LegacySimPromotion key={'sim:' + selected.id} importId={selected.id} revision={selected.revision} accountId={selected.sim_promotion?.account_id} onImported={onImported} onBusy={value => { setBusy(value); onBusy?.(value) }} onChanged={() => work(async () => { setHistory(await api<Summary[]>('/legacy-imports')); setSelected(await api<Detail>(`/legacy-imports/${selected.id}`)) })} />}
      <LegacySupplementEditor key={'supplement:' + selected.id} importId={selected.id} onBusy={value => { setBusy(value); onBusy?.(value) }} onChanged={() => work(async () => { setHistory(await api<Summary[]>('/legacy-imports')); setSelected(await api<Detail>(`/legacy-imports/${selected.id}`)) })} />
      {selected.source_kind.startsWith('laimiu_') && selected.account_id && <LegacyAttachmentEditor key={'attachments:' + selected.id} importId={selected.id} onBusy={value => { setBusy(value); onBusy?.(value) }} onChanged={() => work(async () => { setHistory(await api<Summary[]>('/legacy-imports')); setSelected(await api<Detail>(`/legacy-imports/${selected.id}`)) })} />}
      <ArchiveBrowser key={selected.id} data={selected.archive} /><details><summary>新旧记录 ID 对应</summary><pre style={codeStyle}>{pretty(selected.mappings)}</pre></details></div>}
  </section></div>
}
