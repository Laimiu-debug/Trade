import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type Settings = { version: number; config: { commission_rate: string; minimum_commission: string; sell_stamp_rate: string; transfer_rate: string } }

export function RealFeeEditor({ accountId, trade }: { accountId: string; trade: { side: string; quantity: number; price: string; fee: string; fee_mode: 'auto' | 'manual' } }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [editing, setEditing] = useState(false)
  const [preview, setPreview] = useState<{ fee: string; calculated_fee: string; fee_source: string; breakdown: { commission: string; stamp: string; transfer: string } } | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let current = true
    api<Settings>(`/accounts/${accountId}/fee-settings`).then(value => { if (current) setSettings(value) }).catch(err => { if (current) setError(err.message) })
    return () => { current = false }
  }, [accountId])
  useEffect(() => {
    if (!(Number(trade.price) > 0) || !(trade.quantity > 0)) { setPreview(null); return }
    let current = true
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams({ price: trade.price, quantity: String(trade.quantity),
        side: trade.side, fee_mode: trade.fee_mode, fee: trade.fee || '0' })
      api<typeof preview>(`/accounts/${accountId}/fee-preview?${query}`).then(value => { if (current) setPreview(value) }).catch(() => { if (current) setPreview(null) })
    }, 250)
    return () => { current = false; window.clearTimeout(timer) }
  }, [accountId, trade.price, trade.quantity, trade.side, trade.fee, trade.fee_mode, settings?.version])

  async function save() {
    if (!settings) return
    setBusy(true); setError('')
    try { setSettings(await api<Settings>(`/accounts/${accountId}/fee-settings`, 'PUT',
      { expected_version: settings.version, config: settings.config })); setEditing(false) }
    catch (err) { setError(err instanceof Error ? err.message : '保存费用规则失败') }
    finally { setBusy(false) }
  }

  const fields: Array<[keyof Settings['config'], string]> = [
    ['commission_rate', '佣金率'], ['minimum_commission', '最低佣金（元）'],
    ['sell_stamp_rate', '卖出印花税率'], ['transfer_rate', '过户费率'],
  ]
  return <section className="card span-all"><div className="section-heading"><div><h2 className="title-with-icon"><Icon name="calculator" />交易费用规则</h2><p>实盘与模拟交易共用同一计算公式。规则调整只影响此后新增或修改的交易。</p></div><button type="button" className="button secondary" onClick={() => setEditing(!editing)}>{editing ? '收起设置' : '设置费率'}</button></div>
    {preview && <p className="muted">当前输入预计费用：¥ {preview.calculated_fee}（佣金 {preview.breakdown.commission}、印花税 {preview.breakdown.stamp}、过户费 {preview.breakdown.transfer}）；{trade.fee_mode === 'manual' ? `实付覆盖 ¥ ${preview.fee}` : '按计算值入账'}</p>}
    {error && <p className="danger" role="alert">{error}</p>}
    {editing && settings && <div className="form"><p className="muted">规则版本 {settings.version}。费率填写小数，例如 0.0003 表示万分之三。</p><div className="form-grid">{fields.map(([key, label]) => <label className="field" key={key}>{label}<input type="number" min="0" max={key === 'minimum_commission' ? undefined : '0.01'} step={key === 'minimum_commission' ? '0.01' : '0.000001'} value={settings.config[key]} onChange={event => setSettings({ ...settings, config: { ...settings.config, [key]: event.target.value } })} /></label>)}</div><div className="form-actions"><button type="button" className="button primary" disabled={busy} onClick={save}><Icon name="save" />保存费用规则</button></div></div>}
  </section>
}
