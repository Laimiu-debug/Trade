import { useState } from 'react'
import { api } from './api'

type Preview = { account_name: string; expected_revision: number; preview_sha256: string; can_import: boolean;
  notes: string[]; errors: Array<{ section: string; source_id: string; message: string }>;
  summary: { fill_count: number; open_lot_count: number; closed_allocation_count: number; archived_terminal_order_count: number; cash: string; cost_basis: string; as_of_date: string } | null;
  records: unknown; source_sha256: string; logical_sha256: string }

export function LegacySimPromotion({ importId, revision, accountId, onChanged, onImported, onBusy }: {
  importId: string; revision: number; accountId?: string; onChanged: () => Promise<unknown>; onImported?: (id: string) => void; onBusy?: (busy: boolean) => void;
}) {
  const [name, setName] = useState('旧模拟独立副本')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState(accountId || '')
  async function work(action: () => Promise<void>) {
    setBusy(true); onBusy?.(true); setError('')
    try { await action() } catch (err) { setError(err instanceof Error ? err.message : '模拟迁入失败') } finally { setBusy(false); onBusy?.(false) }
  }
  return <section className="market-detail"><h3>完整旧模拟账本 → 独立新模拟账户</h3>
    <p className="subtle">逐笔验证现金、成交费用、FIFO 批次、闭合盈亏与 T+1。数据不完整时显示具体问题，原档案保留。旧未成交委托需要在旧资料副本中处理后重新导出。</p>
    {error && <p role="alert" className="alert error">{error}</p>}
    {created ? <p role="status">已迁入独立模拟账户 {created}；同一档案不会再次创建账户。<button className="button secondary" onClick={() => onImported?.(created)}>查看迁入的模拟账户</button></p> : <>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}><label className="field"><span>新模拟账户名称</span><input value={name} maxLength={80} onChange={event => { setName(event.target.value); setPreview(null); setAck(false) }} /></label>
        <button className="button secondary" disabled={!name.trim()} onClick={() => void work(async () => { setPreview(await api<Preview>(`/legacy-imports/${importId}/simulation/preview`, 'POST', { expected_revision: revision, account_name: name })); setAck(false) })}>核验旧模拟账本并预览</button></fieldset>
      {preview && <div><h4>模拟迁入核验结果</h4>{preview.summary && <div className="period-summary"><span>模拟时钟：{preview.summary.as_of_date}</span><span>成交 {preview.summary.fill_count} 笔 · 剩余批次 {preview.summary.open_lot_count}</span><span>闭合分配 {preview.summary.closed_allocation_count} 条 · 取消/拒绝 {preview.summary.archived_terminal_order_count} 条只读留档</span><span>现金 ¥ {preview.summary.cash} · 剩余成本 ¥ {preview.summary.cost_basis}</span></div>}
        {preview.notes.map(note => <p key={note} className="subtle">{note}</p>)}
        {!!preview.errors.length && <div role="alert" className="alert error">{preview.errors.map((item, index) => <p key={index}>{item.section} / {item.source_id}：{item.message}</p>)}</div>}
        {preview.records !== null && <details><summary>逐笔成交与批次映射</summary><pre style={{ maxHeight: '24rem', overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(preview.records, null, 2)}</pre></details>}
        <p className="subtle" style={{ overflowWrap: 'anywhere' }}>原文件 SHA256：{preview.source_sha256}<br />逻辑内容 SHA256：{preview.logical_sha256}</p>
        <label className="check-field"><input type="checkbox" checked={ack} disabled={busy || !preview.can_import} onChange={event => setAck(event.target.checked)} />我已核对逐笔结果，接受初始日期与历史委托费率未知的标记</label>
        <button className="button primary" disabled={busy || !ack || !preview.can_import} onClick={() => void work(async () => {
          const result = await api<{ account_id: string }>(`/legacy-imports/${importId}/simulation/apply`, 'POST', { expected_revision: preview.expected_revision, account_name: preview.account_name, expected_preview_sha256: preview.preview_sha256, acknowledge_limitations: ack })
          setCreated(result.account_id); setPreview(null); await onChanged(); onImported?.(result.account_id)
        })}>确认创建独立模拟账户</button>
      </div>}
    </>}
  </section>
}
