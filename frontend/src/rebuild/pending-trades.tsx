import { useCallback, useEffect, useState } from 'react'
import { api, type PendingTrade } from './api'
import { Icon } from './workspace-icons'

type Form = Pick<PendingTrade, 'trade_date' | 'symbol' | 'name' | 'side' | 'quantity' | 'price' | 'fee' | 'note'> & { fee_mode: 'auto' | 'manual' }

export function PendingTradesEditor({ accountId, onConfirmed }: { accountId: string; onConfirmed: () => Promise<void> }) {
  const [rows, setRows] = useState<PendingTrade[]>([])
  const [editing, setEditing] = useState<PendingTrade | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const base = `/accounts/${accountId}/pending-trades`
  const refresh = useCallback(async () => setRows(await api<PendingTrade[]>(base)), [base])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])

  async function act(task: () => Promise<unknown>, message: string, confirmed = false) {
    setBusy(true); setError(''); setNotice('')
    try { await task(); await refresh(); if (confirmed) await onConfirmed(); setNotice(message); setEditing(null); setForm(null) }
    catch (err) { setError(err instanceof Error ? err.message : '操作失败') }
    finally { setBusy(false) }
  }

  function startEdit(row: PendingTrade) {
    setEditing(row)
    setForm({ trade_date: row.trade_date, symbol: row.symbol, name: row.name, side: row.side,
      quantity: row.quantity, price: row.price, fee: row.fee, fee_mode: row.fee_source, note: row.note })
  }

  async function confirmOne(row: PendingTrade) {
    const acknowledge_duplicate = row.duplicate_trade_ids.length > 0
    if (acknowledge_duplicate && !window.confirm(`发现 ${row.duplicate_trade_ids.length} 笔疑似重复成交。确认核对后仍要录入？`)) return
    await act(() => api(base + `/${row.id}/confirm`, 'POST',
      { expected_revision: row.revision, acknowledge_duplicate }), '已确认并写入正式交易', true)
  }

  async function confirmAll() {
    const safe = rows.filter(row => !row.duplicate_trade_ids.length)
    if (!safe.length) { setNotice('所有待确认项均需先核对疑似重复'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<{ confirmed_count: number; results: Array<{ status: string; message?: string }> }>(
        base + '/confirm-batch', 'POST', { items: safe.map(row => ({ id: row.id, expected_revision: row.revision })) })
      await refresh(); if (result.confirmed_count) await onConfirmed()
      const failures = result.results.filter(row => row.status === 'failed')
      setNotice(`已确认 ${result.confirmed_count} 笔；${failures.length} 笔校验失败，保留待修改；${rows.length - safe.length} 笔疑似重复待核对`)
      if (failures.length) setError(failures.map(row => row.message).join('；'))
    } catch (err) { setError(err instanceof Error ? err.message : '批量确认失败') }
    finally { setBusy(false) }
  }

  return <section className="card span-all"><div className="section-heading"><div><h2 className="title-with-icon"><Icon name="check" />待确认交易</h2><p>此处记录尚未进入正式账本；疑似重复成交需逐条核对。</p></div><button className="button secondary" disabled={busy || !rows.length} onClick={confirmAll}><Icon name="check" />确认全部无重复项</button></div>
    {error && <p className="danger" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="table-wrap"><table><thead><tr><th>日期</th><th>方向</th><th>代码 / 名称</th><th>数量</th><th>价格</th><th>费用</th><th>核对</th><th>操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td>{row.trade_date}</td><td>{row.side === 'buy' ? '买入' : '卖出'}</td><td><strong>{row.symbol}</strong><br /><span className="muted">{row.name}</span></td><td>{row.quantity}</td><td>{row.price}</td><td>{row.fee}</td><td>{row.duplicate_trade_ids.length ? <span className="danger">疑似重复 {row.duplicate_trade_ids.length} 笔</span> : '待确认'}</td><td><button className="link-button" disabled={busy} onClick={() => startEdit(row)}>编辑</button><button className="link-button" disabled={busy} onClick={() => confirmOne(row)}>确认</button><button className="link-button danger" disabled={busy} onClick={() => { if (window.confirm('删除这条待确认交易？')) act(() => api(base + `/${row.id}?expected_revision=${row.revision}`, 'DELETE'), '待确认交易已删除') }}>删除</button></td></tr>)}</tbody></table></div>{!rows.length && <p className="muted">暂无待确认交易</p>}
    {editing && form && <form className="form" onSubmit={event => { event.preventDefault(); act(() => api(base + `/${editing.id}`, 'PUT', { ...form, expected_revision: editing.revision }), '待确认交易已修改') }}><h3>编辑待确认交易 · {editing.symbol}</h3><div className="form-grid"><label className="field">交易日期<input type="date" required value={form.trade_date} onChange={event => setForm({ ...form, trade_date: event.target.value })} /></label><label className="field">方向<select value={form.side} onChange={event => setForm({ ...form, side: event.target.value as 'buy' | 'sell' })}><option value="buy">买入</option><option value="sell">卖出</option></select></label><label className="field">证券代码<input required value={form.symbol} onChange={event => setForm({ ...form, symbol: event.target.value })} /></label><label className="field">名称<input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label><label className="field">数量<input type="number" min="1" required value={form.quantity} onChange={event => setForm({ ...form, quantity: Number(event.target.value) })} /></label><label className="field">价格<input type="number" min="0.0001" step="0.0001" required value={form.price} onChange={event => setForm({ ...form, price: event.target.value })} /></label><label className="field">费用方式<select value={form.fee_mode} onChange={event => setForm({ ...form, fee_mode: event.target.value as 'auto' | 'manual' })}><option value="auto">自动计算</option><option value="manual">手动实付覆盖</option></select></label>{form.fee_mode === 'manual' && <label className="field">实付费用<input type="number" min="0" step="0.01" value={form.fee} onChange={event => setForm({ ...form, fee: event.target.value })} /></label>}</div><label className="field">备注<textarea value={form.note} onChange={event => setForm({ ...form, note: event.target.value })} /></label><div className="form-actions"><button className="button primary" disabled={busy}><Icon name="save" />保存修改</button><button type="button" className="button ghost" onClick={() => { setEditing(null); setForm(null) }}><Icon name="close" />取消</button></div></form>}
  </section>
}
