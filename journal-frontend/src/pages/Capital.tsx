import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, fmtMoney, today } from '../api';
import { Empty, useToast, DateInput, NumberInput, StockPicker } from '../components';

import { fetchCloseOnDay } from '../marketClose';

interface SnapPosition { code?: string; name?: string; qty?: number; price?: number; market_value?: number }
type EditPosition = SnapPosition & { _key: string };

interface SnapRow {
  id: number;
  snap_date: string;
  total_assets: number;
  available_cash?: number | null;
  position_value?: number | null;
  positions: SnapPosition[];
  note: string;
}
interface SnapshotsPage {
  items: SnapRow[];
  total: number;
  offset: number;
  limit: number;
}
interface AccountImportPreview {
  snap_date: string;
  total_assets: number | null;
  available_cash: number | null;
  position_value?: number | null;
  positions: EditPosition[];
}

function newPositionKey() {
  return `p-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function toEditPositions(positions: SnapPosition[]): EditPosition[] {
  return positions.map(p => ({ ...p, _key: newPositionKey() }));
}

function emptyPosition(): EditPosition {
  return { code: '', name: '', qty: 0, price: undefined, market_value: undefined, _key: newPositionKey() };
}

function recalcPosition(p: EditPosition): EditPosition {
  const qty = p.qty ?? 0;
  const price = p.price;
  if (price != null && qty > 0) {
    return { ...p, market_value: Math.round(qty * price * 100) / 100 };
  }
  return p;
}

function sumPositionValue(positions: EditPosition[]): number {
  return Math.round(
    positions.reduce((s, p) => s + (p.market_value ?? 0), 0) * 100,
  ) / 100;
}

function recalcPreview(preview: AccountImportPreview): AccountImportPreview {
  const positions = preview.positions.map(recalcPosition);
  const position_value = sumPositionValue(positions);
  const cash = preview.available_cash ?? 0;
  const computedTotal = position_value + cash;
  return {
    ...preview,
    positions,
    position_value: position_value > 0 ? position_value : null,
    total_assets: computedTotal > 0 ? computedTotal : preview.total_assets,
  };
}

function stripKeys(positions: EditPosition[]): SnapPosition[] {
  return positions.map(({ _key, ...p }) => p);
}

function isValidCode(code: string | undefined): boolean {
  return /^\d{6}$/.test(code ?? '');
}

const PAGE_SIZE = 20;

export default function Capital() {
  const toast = useToast();
  const [snaps, setSnaps] = useState<SnapRow[]>([]);
  const [snapTotal, setSnapTotal] = useState(0);
  const [snapOffset, setSnapOffset] = useState(0);
  const [hasInitial, setHasInitial] = useState(true);
  const [estimating, setEstimating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [importPreview, setImportPreview] = useState<AccountImportPreview | null>(null);
  const [editingSnapId, setEditingSnapId] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLDivElement>(null);

  const [snapForm, setSnapForm] = useState({ snap_date: today(), total_assets: '', note: '' });
  const [expandedSnapId, setExpandedSnapId] = useState<number | null>(null);

  const reload = useCallback(() => {
    api.get<SnapshotsPage>(`/api/capital/snapshots?offset=${snapOffset}&limit=${PAGE_SIZE}`)
      .then(r => {
        if (r.items.length === 0 && r.offset > 0) {
          setSnapOffset(Math.max(0, r.offset - PAGE_SIZE));
          return;
        }
        setSnaps(r.items);
        setSnapTotal(r.total);
      })
      .catch(e => toast(String(e)));
    api.get<{ has_initial: boolean }>('/api/capital/status').then(r => setHasInitial(r.has_initial)).catch(e => toast(String(e)));
  }, [snapOffset, toast]);

  useEffect(reload, [reload]);


  const setPositionRow = (index: number, patch: Partial<EditPosition>) => {
    setImportPreview(prev => {
      if (!prev) return prev;
      const positions = [...prev.positions];
      positions[index] = recalcPosition({ ...positions[index], ...patch });
      return recalcPreview({ ...prev, positions });
    });
  };

  const pickPositionStock = async (index: number, code: string, name: string) => {
    setPositionRow(index, { code, name });
    const close = await fetchCloseOnDay(code, snapForm.snap_date);
    if (close != null) setPositionRow(index, { price: close });
  };

  const addPositionRow = () => {
    setImportPreview(prev => {
      const base: AccountImportPreview = prev ?? {
        snap_date: snapForm.snap_date,
        total_assets: null,
        available_cash: null,
        position_value: null,
        positions: [],
      };
      return recalcPreview({ ...base, positions: [...base.positions, emptyPosition()] });
    });
  };

  const removePositionRow = (index: number) => {
    setImportPreview(prev => {
      if (!prev) return prev;
      const positions = prev.positions.filter((_, i) => i !== index);
      return recalcPreview({ ...prev, positions });
    });
  };

  const loadSnapForEdit = (s: SnapRow) => {
    setEditingSnapId(s.id);
    setSnapForm({
      snap_date: s.snap_date,
      total_assets: String(s.total_assets),
      note: s.note,
    });
    setImportPreview(recalcPreview({
      snap_date: s.snap_date,
      total_assets: s.total_assets,
      available_cash: s.available_cash ?? null,
      position_value: s.position_value ?? null,
      positions: toEditPositions(s.positions ?? []),
    }));
    formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const addSnap = async () => {
    const assets = parseFloat(snapForm.total_assets);
    if (Number.isNaN(assets) || assets < 0) { toast('请输入有效总资产'); return; }
    const positions = importPreview?.positions ?? [];
    const active = positions.filter(p => (p.qty ?? 0) > 0 || p.code || p.name);
    const missingCode = active.filter(p => !isValidCode(p.code));
    if (missingCode.length > 0) {
      toast('请为每条持仓选择正确的 6 位股票代码');
      return;
    }
    try {
      const wasEdit = editingSnapId != null;
      const preview = importPreview ? recalcPreview(importPreview) : null;
      await api.post('/api/capital/snapshots', {
        snap_date: snapForm.snap_date,
        total_assets: assets,
        note: snapForm.note,
        positions: stripKeys(preview?.positions ?? []),
        available_cash: preview?.available_cash ?? null,
        position_value: preview?.position_value ?? null,
      });
      setSnapForm({ ...snapForm, total_assets: '', note: '' });
      setImportPreview(null);
      setEditingSnapId(null);
      toast(wasEdit ? '快照已更新' : '快照已保存');
      if (wasEdit) reload();
      else setSnapOffset(0);
    } catch (e) { toast(String(e)); }
  };

  const estimateFromTrades = async () => {
    setEstimating(true);
    try {
      const est = await api.get<{
        ok: boolean;
        message?: string;
        snap_date?: string;
        total_assets?: number;
        cash?: number;
        position_value?: number;
        positions?: SnapPosition[];
        missing_quotes?: string[];
        reason?: string;
      }>(`/api/capital/estimate?date=${snapForm.snap_date}`);
      if (!est.ok) {
        toast(est.message ?? '无法推算，请先录入初始资金');
        return;
      }
      setEditingSnapId(null);
      setSnapForm({
        ...snapForm,
        snap_date: est.snap_date ?? snapForm.snap_date,
        total_assets: String(est.total_assets ?? ''),
        note: est.message ?? '',
      });
      setImportPreview(recalcPreview({
        snap_date: est.snap_date ?? snapForm.snap_date,
        total_assets: est.total_assets ?? null,
        available_cash: est.cash ?? null,
        position_value: est.position_value ?? null,
        positions: toEditPositions(est.positions ?? []),
      }));
      toast(`已填入推算值：现金 ¥${fmtMoney(est.cash ?? 0)} + 持仓 ¥${fmtMoney(est.position_value ?? 0)}`);
      if (est.missing_quotes?.length) {
        toast(`以下标的无行情，市值可能不准：${est.missing_quotes.join('、')}`);
      }
    } catch (e) { toast(String(e)); } finally {
      setEstimating(false);
    }
  };

  const uploadAccountScreenshot = async (file: File) => {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const result = await api.post<{
        snap_date: string;
        total_assets: number | null;
        available_cash: number | null;
        position_value?: number | null;
        positions: SnapPosition[];
      }>('/api/capital/import/screenshot', fd);
      setEditingSnapId(null);
      const preview = recalcPreview({
        snap_date: result.snap_date,
        total_assets: result.total_assets,
        available_cash: result.available_cash,
        position_value: result.position_value ?? null,
        positions: toEditPositions(result.positions ?? []),
      });
      setImportPreview(preview);
      const computedTotal = (preview.position_value ?? 0) + (preview.available_cash ?? 0);
      const totalHint = preview.total_assets ?? (computedTotal > 0 ? computedTotal : null);
      setSnapForm({
        ...snapForm,
        snap_date: result.snap_date,
        total_assets: totalHint != null ? String(totalHint) : snapForm.total_assets,
        note: '持仓截图识别',
      });
      const badCode = preview.positions.filter(p => (p.qty ?? 0) > 0 && !isValidCode(p.code));
      if (badCode.length > 0) {
        toast(`识别到 ${preview.positions.length} 条持仓，${badCode.length} 条缺少代码，请手动补全后保存`);
      } else if (totalHint != null) {
        toast(`识别到总资产 ¥${fmtMoney(totalHint)}，请核对后保存`);
      } else if (preview.positions.length > 0) {
        toast(`识别到 ${preview.positions.length} 条持仓，请核对后保存`);
      } else {
        toast('未能识别有效数据，请手动填写');
      }
    } catch (e) { toast(String(e)); } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h2 className="page-title">资金账本</h2>
          <div className="page-sub">记录每日收盘快照与持仓；出入金请在「设置」中管理</div>
        </div>
      </div>

      {!hasInitial && (
        <div className="alert" style={{ marginBottom: 18 }}>
          尚未录入初始资金。请先到 <Link to="/settings">设置 → 出入金流水</Link> 添加「初始资金」；之后可在此上传持仓截图或从交易推算总资产。
        </div>
      )}

      <div className="card" ref={formRef}>
        <div className="page-head" style={{ marginBottom: 12 }}>
          <h3 className="card-title" style={{ margin: 0 }}>
            {editingSnapId != null ? '编辑收盘快照' : '每日收盘快照'}
          </h3>
          <div className="row">
            <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
              onChange={e => e.target.files?.[0] && uploadAccountScreenshot(e.target.files[0])} />
            <button onClick={() => fileRef.current?.click()} disabled={uploading}>
              {uploading ? '识别中…' : '上传持仓截图'}
            </button>
            <button onClick={estimateFromTrades} disabled={estimating || !hasInitial}>
              {estimating ? '推算中…' : '从交易推算'}
            </button>
            <button className="ghost" onClick={addPositionRow}>+ 手动添加持仓</button>
          </div>
        </div>
        <div className="row" style={{ marginBottom: 16 }}>
          <DateInput value={snapForm.snap_date} onChange={v => setSnapForm({ ...snapForm, snap_date: v })} />
          <NumberInput placeholder="收盘总资产（现金+持仓市值）" style={{ flex: 1, minWidth: 140 }}
            value={snapForm.total_assets}
            onChange={v => setSnapForm({ ...snapForm, total_assets: v })} />
          <button className="primary" onClick={addSnap}>
            {editingSnapId != null ? '更新' : '保存'}
          </button>
          {editingSnapId != null && (
            <button className="ghost" onClick={() => {
              setEditingSnapId(null);
              setImportPreview(null);
              setSnapForm({ snap_date: today(), total_assets: '', note: '' });
            }}>取消编辑</button>
          )}
        </div>

        {importPreview && (
          <div style={{ marginBottom: 16, padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)' }}>
            <div className="row" style={{ marginBottom: 12, gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              {importPreview.total_assets != null && (
                <span className="mono">总资产 ¥{fmtMoney(importPreview.total_assets)}</span>
              )}
              <label className="field" style={{ margin: 0, flex: '0 0 140px' }}>
                <span className="muted" style={{ fontSize: 11 }}>可用现金</span>
                <NumberInput
                  value={importPreview.available_cash != null ? String(importPreview.available_cash) : ''}
                  onChange={v => {
                    setImportPreview(prev => {
                      if (!prev) return prev;
                      const cash = v === '' ? null : parseFloat(v);
                      const next = recalcPreview({
                        ...prev,
                        available_cash: cash != null && !Number.isNaN(cash) ? cash : null,
                      });
                      if (next.total_assets != null) {
                        setSnapForm(f => ({ ...f, total_assets: String(next.total_assets) }));
                      }
                      return next;
                    });
                  }}
                />
              </label>
              {importPreview.position_value != null && (
                <span className="muted mono">持仓 ¥{fmtMoney(importPreview.position_value)}</span>
              )}
            </div>

            {importPreview.positions.length > 0 ? (
              <>
                <div className="muted" style={{ marginBottom: 8, fontSize: 12 }}>
                  持仓明细（可修改代码、数量、价格；保存前须补全 6 位代码）
                </div>
                <div className="rehearsal-edit-list">
                  {importPreview.positions.map((p, i) => {
                    const mv = p.market_value;
                    const codeBad = (p.qty ?? 0) > 0 && !isValidCode(p.code);
                    return (
                      <div className="row rehearsal-edit-row" key={p._key} style={{ marginBottom: 8, alignItems: 'flex-end' }}>
                        <StockPicker
                          code={p.code ?? ''}
                          name={p.name ?? ''}
                          onSelect={(code, name) => { void pickPositionStock(i, code, name); }}
                          style={{ flex: 2, minWidth: 160 }}
                        />
                        <label className="field" style={{ flex: '0 0 90px', margin: 0 }}>
                          <span>股数</span>
                          <NumberInput
                            value={String(p.qty ?? 0)}
                            onChange={v => setPositionRow(i, { qty: parseInt(v, 10) || 0 })}
                          />
                        </label>
                        <label className="field" style={{ flex: '0 0 100px', margin: 0 }}>
                          <span>收盘价</span>
                          <NumberInput
                            value={p.price != null ? String(p.price) : ''}
                            onChange={v => {
                              const price = v === '' ? undefined : parseFloat(v);
                              setPositionRow(i, { price: price != null && !Number.isNaN(price) ? price : undefined });
                            }}
                          />
                        </label>
                        <div className="field" style={{ flex: '0 0 96px', margin: 0 }}>
                          <span>市值</span>
                          <div className="mono muted" style={{ fontSize: 13, padding: '8px 0' }}>
                            {mv != null ? `¥${fmtMoney(mv)}` : '—'}
                          </div>
                        </div>
                        {codeBad && (
                          <span className="tag" style={{ alignSelf: 'center', fontSize: 11 }}>缺代码</span>
                        )}
                        <button className="danger-ghost" style={{ alignSelf: 'center' }}
                          onClick={() => removePositionRow(i)}>×</button>
                      </div>
                    );
                  })}
                </div>
                <button className="ghost" style={{ marginTop: 8, fontSize: 12 }} onClick={addPositionRow}>
                  + 添加一行
                </button>
              </>
            ) : (
              <Empty text="点击「+ 手动添加持仓」录入标的，或上传截图 / 从交易推算" />
            )}
          </div>
        )}
        {snaps.length === 0 ? <Empty text="收盘后上传持仓截图，或从交易推算总资产" /> : (
          <>
          <div className="snap-card-list">
            {snaps.map(s => {
              const expanded = expandedSnapId === s.id;
              const positions = s.positions ?? [];
              return (
                <div className="snap-card" key={s.id}>
                  <div className="snap-card-head">
                    <div>
                      <div className="snap-card-date">{s.snap_date}</div>
                      <div className="snap-card-total mono">¥{fmtMoney(s.total_assets)}</div>
                    </div>
                    <div className="snap-card-actions">
                      <button className="ghost" style={{ fontSize: 12 }} onClick={() => loadSnapForEdit(s)}>
                        编辑
                      </button>
                      {positions.length > 0 && (
                        <button className="ghost" style={{ fontSize: 12 }}
                          onClick={() => setExpandedSnapId(expanded ? null : s.id)}>
                          {expanded ? '收起' : `${positions.length} 只持仓`}
                        </button>
                      )}
                      <button className="danger-ghost" onClick={async () => {
                        await api.del(`/api/capital/snapshots/${s.id}`);
                        if (editingSnapId === s.id) {
                          setEditingSnapId(null);
                          setImportPreview(null);
                        }
                        reload();
                      }}>删除</button>
                    </div>
                  </div>
                  <div className="snap-card-meta">
                    {s.available_cash != null && (
                      <span className="muted mono">现金 ¥{fmtMoney(s.available_cash)}</span>
                    )}
                    {s.position_value != null && (
                      <span className="muted mono">持仓 ¥{fmtMoney(s.position_value)}</span>
                    )}
                    {positions.length > 0 && s.position_value == null && (
                      <span className="muted">{positions.length} 只标的</span>
                    )}
                    {s.note && <span className="muted">{s.note}</span>}
                  </div>
                  {(expanded || positions.length <= 3) && positions.length > 0 && (
                    <div className="snap-card-positions">
                      {positions.map((p, i) => (
                        <div className="snap-pos-row" key={`${p.code ?? i}-${i}`}>
                          <span className="mono">{p.code ?? '—'}</span>
                          <span>{p.name ?? '—'}</span>
                          <span className="mono muted">{p.qty != null ? `${p.qty}股` : '—'}</span>
                          <span className="mono muted">
                            {p.market_value != null ? `¥${fmtMoney(p.market_value)}` : p.price != null ? p.price.toFixed(3) : '—'}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {snapTotal > PAGE_SIZE && (
            <div className="row" style={{ marginTop: 12, justifyContent: 'space-between' }}>
              <button className="ghost" disabled={snapOffset <= 0}
                onClick={() => setSnapOffset(o => Math.max(0, o - PAGE_SIZE))}>
                ← 上一页
              </button>
              <span className="muted" style={{ fontSize: 12 }}>
                {snapOffset + 1}–{Math.min(snapOffset + PAGE_SIZE, snapTotal)} / {snapTotal}
              </span>
              <button className="ghost" disabled={snapOffset + PAGE_SIZE >= snapTotal}
                onClick={() => setSnapOffset(o => o + PAGE_SIZE)}>
                下一页 →
              </button>
            </div>
          )}
          </>
        )}
      </div>
    </div>
  );
}
