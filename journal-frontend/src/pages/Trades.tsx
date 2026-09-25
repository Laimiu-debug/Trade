import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, fmtMoney, today, type LinkedRoundRow, type RoundRow, type RoundStats, type TradeRow } from '../api';
import { Empty, SideTag, Stat, useToast, DateInput, NumberInput, StockPicker, Select } from '../components';
import { PeriodRounds } from './periodicShared';

const SIDE_OPTIONS = [
  { value: 'buy', label: '买入' },
  { value: 'sell', label: '卖出' },
];

interface PendingRow { id: number; trade_date: string; code: string; name: string; side: string; price: number; qty: number }
interface PendingEdit {
  trade_date: string;
  code: string;
  name: string;
  side: string;
  price: string;
  qty: string;
}
interface SnapshotSuggestion {
  snap_date: string;
  total_assets: number;
  cash: number;
  position_value: number;
  positions?: { code?: string; name?: string; qty?: number; price?: number; market_value?: number }[];
  message?: string;
}

type ListMode = 'day' | 'stock';

interface DayTradeGroup {
  date: string;
  trades: TradeRow[];
  buyCount: number;
  sellCount: number;
  buyAmount: number;
  sellAmount: number;
}

interface StockTradeGroup {
  code: string;
  name: string;
  trades: TradeRow[];
  buyQty: number;
  sellQty: number;
  firstDate: string;
  lastDate: string;
}

function groupTradesByDay(trades: TradeRow[]): DayTradeGroup[] {
  const map = new Map<string, TradeRow[]>();
  for (const t of trades) {
    const list = map.get(t.trade_date) ?? [];
    list.push(t);
    map.set(t.trade_date, list);
  }
  return [...map.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([date, items]) => {
      const sorted = [...items].sort((a, b) => a.id - b.id);
      let buyCount = 0;
      let sellCount = 0;
      let buyAmount = 0;
      let sellAmount = 0;
      for (const t of sorted) {
        if (t.side === 'buy') {
          buyCount += 1;
          buyAmount += t.amount;
        } else {
          sellCount += 1;
          sellAmount += t.amount;
        }
      }
      return { date, trades: sorted, buyCount, sellCount, buyAmount, sellAmount };
    });
}

function groupTradesByStock(trades: TradeRow[]): StockTradeGroup[] {
  const map = new Map<string, StockTradeGroup>();
  for (const t of trades) {
    const existing = map.get(t.code);
    if (existing) {
      existing.trades.push(t);
      if (!existing.name && t.name) existing.name = t.name;
    } else {
      map.set(t.code, { code: t.code, name: t.name, trades: [t], buyQty: 0, sellQty: 0, firstDate: t.trade_date, lastDate: t.trade_date });
    }
  }
  return [...map.values()]
    .map(group => {
      const sorted = [...group.trades].sort((a, b) => {
        const byDate = a.trade_date.localeCompare(b.trade_date);
        return byDate !== 0 ? byDate : a.id - b.id;
      });
      let buyQty = 0;
      let sellQty = 0;
      for (const t of sorted) {
        if (t.side === 'buy') buyQty += t.qty;
        else sellQty += t.qty;
      }
      return {
        ...group,
        trades: sorted,
        buyQty,
        sellQty,
        firstDate: sorted[0]?.trade_date ?? '',
        lastDate: sorted[sorted.length - 1]?.trade_date ?? '',
      };
    })
    .sort((a, b) => b.lastDate.localeCompare(a.lastDate) || a.code.localeCompare(b.code));
}

function TradeLine({
  trade,
  showDate,
  onDelete,
}: {
  trade: TradeRow;
  showDate?: boolean;
  onDelete: () => void;
}) {
  return (
    <div className="trade-group-row">
      {showDate && <span className="trade-group-row-date mono muted">{trade.trade_date}</span>}
      <SideTag side={trade.side} />
      <span className="trade-group-row-main">
        {!showDate && (
          <>
            <span className="trade-group-row-name">{trade.name}</span>
            <span className="muted mono">{trade.code}</span>
          </>
        )}
        <span className="mono muted">{trade.price.toFixed(3)} × {trade.qty}</span>
        {trade.source === 'import' && <span className="tag" style={{ fontSize: 10 }}>导入</span>}
      </span>
      <span className="trade-group-row-amount mono">¥{fmtMoney(trade.amount)}</span>
      <span className="trade-group-row-fees mono muted">费 {trade.fees.toFixed(2)}</span>
      <button className="danger-ghost" onClick={onDelete}>删除</button>
    </div>
  );
}

export default function Trades() {
  const toast = useToast();
  const [tab, setTab] = useState<'list' | 'rounds'>('list');
  const [listMode, setListMode] = useState<ListMode>('day');
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [rounds, setRounds] = useState<{ rounds: RoundRow[]; stats: RoundStats } | null>(null);
  const [pending, setPending] = useState<PendingRow[]>([]);
  const [pendingEdits, setPendingEdits] = useState<Record<number, PendingEdit>>({});
  const [uploading, setUploading] = useState(false);
  const [confirmingAll, setConfirmingAll] = useState(false);
  const [snapshotPrompt, setSnapshotPrompt] = useState<SnapshotSuggestion | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState({
    trade_date: today(), code: '', name: '', side: 'buy', price: '', qty: '', note: '',
  });

  const reloadRounds = useCallback(() => {
    api.get<{ rounds: RoundRow[]; stats: RoundStats }>('/api/trades/rounds').then(setRounds).catch(e => toast(String(e)));
  }, [toast]);

  const linkedRounds = useMemo<LinkedRoundRow[]>(() => {
    if (!rounds) return [];
    return rounds.rounds.map(r => ({
      code: r.code,
      name: r.name,
      start_date: r.start_date,
      end_date: r.end_date,
      status: r.status,
      position: r.position,
      pnl: r.pnl,
      pnl_pct: r.pnl_pct,
      buy_amount: r.buy_amount,
      sell_amount: r.sell_amount,
      fees: r.fees,
      trade_count: r.trade_count ?? r.trades.length,
      review_snippet: r.review_snippet ?? '',
      review_summary: r.review_summary ?? '',
      review_dates: r.review_dates ?? [],
    }));
  }, [rounds]);

  const reload = useCallback(() => {
    api.get<TradeRow[]>('/api/trades').then(setTrades).catch(e => toast(String(e)));
    api.get<{ rounds: RoundRow[]; stats: RoundStats }>('/api/trades/rounds').then(setRounds).catch(e => toast(String(e)));
    api.get<PendingRow[]>('/api/trades/pending').then(setPending).catch(e => toast(String(e)));
  }, [toast]);

  useEffect(reload, [reload]);

  const dayGroups = useMemo(() => groupTradesByDay(trades), [trades]);
  const stockGroups = useMemo(() => groupTradesByStock(trades), [trades]);

  const deleteTrade = async (id: number) => {
    try {
      await api.del(`/api/trades/${id}`);
      reload();
    } catch (e) { toast(String(e)); }
  };

  const getPendingEdit = (p: PendingRow): PendingEdit =>
    pendingEdits[p.id] ?? {
      trade_date: p.trade_date,
      code: p.code,
      name: p.name,
      side: p.side,
      price: String(p.price),
      qty: String(p.qty),
    };

  const setPendingEdit = (id: number, patch: Partial<PendingEdit>) => {
    const base = pending.find(p => p.id === id);
    if (!base) return;
    setPendingEdits(prev => {
      const fallback: PendingEdit = {
        trade_date: base.trade_date,
        code: base.code,
        name: base.name,
        side: base.side,
        price: String(base.price),
        qty: String(base.qty),
      };
      return {
        ...prev,
        [id]: { ...fallback, ...prev[id], ...patch },
      };
    });
  };

  const savePendingEdit = async (p: PendingRow) => {
    const edit = getPendingEdit(p);
    const price = parseFloat(edit.price);
    const qty = parseInt(edit.qty, 10);
    if (!edit.code.trim() || !price || !qty) {
      toast('请填写代码、价格、数量');
      return;
    }
    try {
      await api.put(`/api/trades/pending/${p.id}`, {
        trade_date: edit.trade_date,
        code: edit.code,
        name: edit.name,
        side: edit.side,
        price,
        qty,
      });
      setPendingEdits(prev => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
      reload();
    } catch (e) { toast(String(e)); }
  };

  const pendingPayload = (p: PendingRow) => {
    const edit = getPendingEdit(p);
    return {
      trade_date: edit.trade_date,
      code: edit.code,
      name: edit.name,
      side: edit.side,
      price: parseFloat(edit.price),
      qty: parseInt(edit.qty, 10),
    };
  };

  const addTrade = async () => {
    const price = parseFloat(form.price);
    const qty = parseInt(form.qty, 10);
    if (!form.code.trim() || !price || !qty) { toast('请填写代码、价格、数量'); return; }
    try {
      await api.post('/api/trades', { ...form, price, qty });
      setForm({ ...form, price: '', qty: '', note: '' });
      toast('交易已记录，费用已自动计算');
      reload();
    } catch (e) { toast(String(e)); }
  };

  const uploadScreenshot = async (file: File) => {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const result = await api.post<{ recognized: number; pending_created: number }>('/api/trades/import/screenshot', fd);
      toast(`识别出 ${result.recognized} 条，${result.pending_created} 条进入待确认`);
      reload();
    } catch (e) { toast(String(e)); } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const confirmPending = async (p: PendingRow) => {
    const payload = pendingPayload(p);
    if (!payload.code.trim() || !payload.price || !payload.qty) {
      toast('请填写代码、价格、数量');
      return;
    }
    try {
      if (pendingEdits[p.id]) {
        await api.put(`/api/trades/pending/${p.id}`, payload);
      }
      await api.post(`/api/trades/pending/${p.id}/confirm`, payload);
      setPendingEdits(prev => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
      toast('已入库');
      reload();
    } catch (e) { toast(String(e)); }
  };

  const confirmAllPending = async () => {
    setConfirmingAll(true);
    try {
      const dirty = pending.filter(p => pendingEdits[p.id]);
      for (const p of dirty) {
        const payload = pendingPayload(p);
        if (payload.code.trim() && payload.price > 0 && payload.qty > 0) {
          await api.put(`/api/trades/pending/${p.id}`, payload);
        }
      }
      const result = await api.post<{
        confirmed: number;
        skipped: number;
        skipped_details?: { id: number; name: string; reason: string }[];
        snapshot_suggestion: SnapshotSuggestion | null;
      }>('/api/trades/pending/confirm-all');
      let msg = `已确认入库 ${result.confirmed} 条`;
      if (result.skipped) {
        const reasons = result.skipped_details?.map(s => `${s.name}: ${s.reason}`).join('；') ?? '';
        msg += `，跳过 ${result.skipped} 条${reasons ? `（${reasons}）` : ''}`;
      }
      toast(msg);
      setPendingEdits({});
      reload();
      if (result.snapshot_suggestion) {
        setSnapshotPrompt(result.snapshot_suggestion);
      }
    } catch (e) { toast(String(e)); } finally {
      setConfirmingAll(false);
    }
  };

  const saveSuggestedSnapshot = async () => {
    if (!snapshotPrompt) return;
    try {
      await api.post('/api/capital/snapshots', {
        snap_date: snapshotPrompt.snap_date,
        total_assets: snapshotPrompt.total_assets,
        available_cash: snapshotPrompt.cash,
        position_value: snapshotPrompt.position_value,
        positions: snapshotPrompt.positions ?? [],
        note: '交易确认后自动推算',
      });
      toast('今日快照已保存到资金账本');
      setSnapshotPrompt(null);
    } catch (e) { toast(String(e)); }
  };

  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h2 className="page-title">交易记录</h2>
          <div className="page-sub">同一标的建仓到清仓自动归组为回合</div>
        </div>
        <div className="row">
          <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
            onChange={e => e.target.files?.[0] && uploadScreenshot(e.target.files[0])} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading}>
            {uploading ? '识别中…' : '上传截图识别'}
          </button>
        </div>
      </div>

      {pending.length > 0 && (
        <div className="card" style={{ marginBottom: 18, borderColor: 'rgba(212,175,106,0.4)' }}>
          <div className="page-head" style={{ marginBottom: 12 }}>
            <h3 className="card-title" style={{ margin: 0 }}>待确认（截图识别结果，请核对后入库）</h3>
            <div className="row">
              <button className="primary" onClick={confirmAllPending} disabled={confirmingAll}>
                {confirmingAll ? '确认中…' : `全部确认入库（${pending.length}）`}
              </button>
            </div>
          </div>
          <table>
            <thead><tr><th>日期</th><th>代码/名称</th><th>方向</th><th>价格</th><th>数量</th><th /></tr></thead>
            <tbody>
              {pending.map(p => {
                const edit = getPendingEdit(p);
                return (
                  <tr key={p.id}>
                    <td>
                      <DateInput
                        value={edit.trade_date}
                        onChange={v => setPendingEdit(p.id, { trade_date: v })}
                      />
                    </td>
                    <td>
                      <StockPicker
                        code={edit.code}
                        name={edit.name}
                        onSelect={(code, name) => setPendingEdit(p.id, { code, name })}
                        style={{ width: 180 }}
                      />
                    </td>
                    <td>
                      <Select
                        value={edit.side}
                        onChange={v => setPendingEdit(p.id, { side: v })}
                        options={SIDE_OPTIONS}
                        style={{ width: 88 }}
                      />
                    </td>
                    <td>
                      <NumberInput
                        style={{ width: 90 }}
                        value={edit.price}
                        onChange={v => setPendingEdit(p.id, { price: v })}
                      />
                    </td>
                    <td>
                      <NumberInput
                        style={{ width: 90 }}
                        value={edit.qty}
                        onChange={v => setPendingEdit(p.id, { qty: v })}
                      />
                    </td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {pendingEdits[p.id] && (
                        <button className="ghost" onClick={() => savePendingEdit(p)}>保存修改</button>
                      )}
                      <button className="ghost" onClick={() => confirmPending(p)}>确认入库</button>
                      <button className="danger-ghost" onClick={async () => {
                        await api.del(`/api/trades/pending/${p.id}`);
                        setPendingEdits(prev => {
                          const next = { ...prev };
                          delete next[p.id];
                          return next;
                        });
                        reload();
                      }}>丢弃</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {snapshotPrompt && (
        <div className="card" style={{ marginBottom: 18, borderColor: 'rgba(212,175,106,0.55)' }}>
          <h3 className="card-title">同步资金账本快照？</h3>
          <p className="muted" style={{ margin: '0 0 12px' }}>
            {snapshotPrompt.message ?? '根据已确认交易推算'} · {snapshotPrompt.snap_date}
          </p>
          <div className="row" style={{ marginBottom: 12 }}>
            <span className="mono">总资产 ¥{fmtMoney(snapshotPrompt.total_assets)}</span>
            <span className="muted">现金 ¥{fmtMoney(snapshotPrompt.cash)} · 持仓 ¥{fmtMoney(snapshotPrompt.position_value)}</span>
          </div>
          <div className="row">
            <button className="primary" onClick={saveSuggestedSnapshot}>保存到资金账本</button>
            <button className="ghost" onClick={() => setSnapshotPrompt(null)}>稍后手动填写</button>
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 18 }}>
        <h3 className="card-title">手动录入</h3>
        <div className="row">
          <DateInput value={form.trade_date} onChange={v => setForm({ ...form, trade_date: v })} />
          <StockPicker
            code={form.code}
            name={form.name}
            onSelect={(code, name) => setForm({ ...form, code, name })}
            style={{ width: 200 }}
          />
          <Select
            value={form.side}
            onChange={v => setForm({ ...form, side: v })}
            options={SIDE_OPTIONS}
            style={{ width: 88 }}
          />
          <NumberInput placeholder="价格" style={{ width: 90 }} value={form.price} onChange={v => setForm({ ...form, price: v })} />
          <NumberInput placeholder="数量(股)" style={{ width: 100 }} value={form.qty} onChange={v => setForm({ ...form, qty: v })} />
          <button className="primary" onClick={addTrade}>记录</button>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 14, flexWrap: 'wrap', gap: 8 }}>
        <button className={tab === 'list' ? 'primary' : ''} onClick={() => setTab('list')}>流水</button>
        <button className={tab === 'rounds' ? 'primary' : ''} onClick={() => setTab('rounds')}>回合</button>
        {tab === 'list' && trades.length > 0 && (
          <span className="row" style={{ marginLeft: 4, gap: 6 }}>
            <button className={listMode === 'day' ? 'primary' : 'ghost'} onClick={() => setListMode('day')}>按日</button>
            <button className={listMode === 'stock' ? 'primary' : 'ghost'} onClick={() => setListMode('stock')}>按标的</button>
          </span>
        )}
        {rounds && rounds.stats.total_rounds > 0 && (
          <span className="muted" style={{ marginLeft: 8 }}>
            已完成 {rounds.stats.total_rounds} 回合 · 胜率 {rounds.stats.win_rate ?? '—'}% · 盈亏比 {rounds.stats.profit_loss_ratio ?? '—'}
          </span>
        )}
      </div>

      {tab === 'list' && (
        <div className="card">
          {trades.length === 0 ? <Empty text="暂无交易记录" /> : listMode === 'day' ? (
            <div className="trade-group-list">
              {dayGroups.map(group => (
                <div className="trade-group-card" key={group.date}>
                  <div className="trade-group-head">
                    <div>
                      <div className="trade-group-title">
                        <Link to={`/journal?day=${group.date}`}>{group.date}</Link>
                      </div>
                      <div className="trade-group-meta muted">
                        {group.trades.length} 笔
                        {group.buyCount > 0 && <span> · 买 {group.buyCount} 笔 ¥{fmtMoney(group.buyAmount)}</span>}
                        {group.sellCount > 0 && <span> · 卖 {group.sellCount} 笔 ¥{fmtMoney(group.sellAmount)}</span>}
                      </div>
                    </div>
                    <Link className="ghost" style={{ fontSize: 12 }} to={`/journal?day=${group.date}`}>查看复盘 →</Link>
                  </div>
                  <div className="trade-group-body">
                    {group.trades.map(t => (
                      <TradeLine key={t.id} trade={t} onDelete={() => void deleteTrade(t.id)} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="trade-group-list">
              {stockGroups.map(group => (
                <div className="trade-group-card" key={group.code}>
                  <div className="trade-group-head">
                    <div>
                      <div className="trade-group-title">
                        {group.name} <span className="muted mono">{group.code}</span>
                      </div>
                      <div className="trade-group-meta muted">
                        {group.trades.length} 笔 · {group.firstDate} ~ {group.lastDate}
                        {group.buyQty > 0 && <span> · 累计买 {group.buyQty} 股</span>}
                        {group.sellQty > 0 && <span> · 累计卖 {group.sellQty} 股</span>}
                      </div>
                    </div>
                  </div>
                  <div className="trade-group-body">
                    {group.trades.map(t => (
                      <TradeLine key={t.id} trade={t} showDate onDelete={() => void deleteTrade(t.id)} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'rounds' && rounds && (
        <>
          {rounds.stats.total_rounds > 0 && (
            <div className="grid grid-4" style={{ marginBottom: 18 }}>
              <div className="card"><Stat small label="胜率" gold value={rounds.stats.win_rate != null ? `${rounds.stats.win_rate}%` : '—'} note={`${rounds.stats.win_count}胜 ${rounds.stats.lose_count}负`} /></div>
              <div className="card"><Stat small label="盈亏比" value={rounds.stats.profit_loss_ratio ?? '—'} note={`均盈 ${fmtMoney(rounds.stats.avg_win)} / 均亏 ${fmtMoney(rounds.stats.avg_loss)}`} /></div>
              <div className="card"><Stat small label="最长连胜 / 连亏" value={`${rounds.stats.max_win_streak} / ${rounds.stats.max_lose_streak}`} /></div>
              <div className="card"><Stat small label="累计回合盈亏" tone={rounds.stats.total_pnl >= 0 ? 'pos' : 'neg'} value={`¥${fmtMoney(rounds.stats.total_pnl)}`} /></div>
            </div>
          )}
          <div className="card">
            {rounds.rounds.length === 0 ? <Empty text="暂无回合" /> : (
              <PeriodRounds rounds={linkedRounds} title="全部回合" onRoundsChange={reloadRounds} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
