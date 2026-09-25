import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { printMonthlyReview } from '../printPage';
import { useToast } from '../components';
import { useAiBusy } from '../AiBusy';
import { confirmDiscard, useAutosave, useDirtyGuard } from '../hooks/usePersist';
import {
  AutoStats, MonthlyPrintBody, PeriodicField, PeriodRounds, type PeriodAuto,
} from './periodicShared';
import type { LinkedRoundRow } from '../api';

interface MonthlyData {
  year: number;
  month: number;
  system_iteration: string;
  next_goal: string;
  auto: PeriodAuto;
  node_state: { lit_count: number; node_count: number; nav: number };
  period_rounds: LinkedRoundRow[];
}

function monthlySaveSnapshot(m: Pick<MonthlyData, 'system_iteration' | 'next_goal'>): string {
  return JSON.stringify({
    system_iteration: m.system_iteration,
    next_goal: m.next_goal,
  });
}

export default function MonthlyReview() {
  const toast = useToast();
  const { setBusy } = useAiBusy();
  const now = new Date();
  const [mo, setMo] = useState({ year: now.getFullYear(), month: now.getMonth() + 1 });
  const [monthly, setMonthly] = useState<MonthlyData | null>(null);
  const [savedSnapshot, setSavedSnapshot] = useState('');
  const [username, setUsername] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [autoSavedAt, setAutoSavedAt] = useState<number | null>(null);

  const currentSnapshot = useMemo(
    () => (monthly ? monthlySaveSnapshot(monthly) : ''),
    [monthly],
  );
  const dirty = savedSnapshot !== '' && currentSnapshot !== savedSnapshot && !!monthly;
  useDirtyGuard(dirty);

  useEffect(() => {
    api.get<{ pdf_username?: string }>('/api/settings').then(v => setUsername(v.pdf_username ?? '')).catch(e => toast(String(e)));
  }, [toast]);

  const loadMonthly = useCallback(() => {
    api.get<MonthlyData>(`/api/reviews/monthly/${mo.year}/${mo.month}`).then(res => {
      setMonthly({ ...res, period_rounds: res.period_rounds ?? [] });
      setSavedSnapshot(monthlySaveSnapshot(res));
      setAutoSavedAt(null);
    }).catch(e => toast(e instanceof Error ? e.message : String(e)));
  }, [mo, toast]);

  useEffect(loadMonthly, [loadMonthly]);

  const requestMonthChange = (next: { year: number; month: number }) => {
    if (next.year === mo.year && next.month === mo.month) return;
    if (!confirmDiscard(dirty)) return;
    setSavedSnapshot('');
    setMo(next);
  };

  const shiftMonth = (delta: number) => {
    const m = mo.month + delta;
    if (m < 1) requestMonthChange({ year: mo.year - 1, month: 12 });
    else if (m > 12) requestMonthChange({ year: mo.year + 1, month: 1 });
    else requestMonthChange({ ...mo, month: m });
  };

  const save = async (silent = false) => {
    if (!monthly) return;
    setSaving(true);
    try {
      await api.put(`/api/reviews/monthly/${mo.year}/${mo.month}`, {
        system_iteration: monthly.system_iteration,
        next_goal: monthly.next_goal,
      });
      setSavedSnapshot(monthlySaveSnapshot(monthly));
      if (silent) setAutoSavedAt(Date.now());
      else toast('月复盘已保存');
    } catch (e) { toast(String(e)); } finally { setSaving(false); }
  };

  useAutosave(
    !!monthly && dirty && !saving && !reviewing,
    async () => { await save(true); },
    [currentSnapshot],
  );

  const aiReview = async () => {
    await save(true);
    setReviewing(true);
    setBusy(true, 'AI 月复盘生成中…');
    try {
      const result = await api.post<Partial<MonthlyData>>(`/api/reviews/monthly/${mo.year}/${mo.month}/ai-review`);
      setMonthly(prev => prev ? {
        ...prev,
        system_iteration: result.system_iteration ?? prev.system_iteration,
        next_goal: result.next_goal ?? prev.next_goal,
      } : prev);
      toast('AI 月复盘草稿已生成，请核对后保存');
    } catch (e) { toast(String(e)); } finally {
      setReviewing(false);
      setBusy(false);
    }
  };

  const handlePrint = async () => {
    if (!monthly) return;
    if (dirty) await save(true);
    printMonthlyReview(username, mo.year, mo.month, msg => toast(msg));
  };

  const periodLabel = monthly ? `${monthly.auto.start} ~ ${monthly.auto.end}` : '';

  return (
    <div className="fade-in periodic-page">
      <div className="page-head no-print">
        <div>
          <h2 className="page-title">月复盘</h2>
          <div className="page-sub">审视体系进化与节点征途</div>
        </div>
        <div className="row">
          {(dirty || saving || autoSavedAt) && (
            <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
              {saving ? '保存中…' : dirty ? '有未保存修改' : `已自动保存 ${new Date(autoSavedAt!).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`}
            </span>
          )}
          <button onClick={handlePrint} disabled={!monthly}>打印</button>
          <button onClick={aiReview} disabled={reviewing}>{reviewing ? 'AI 复盘中…' : '✦ AI 复盘'}</button>
          <button className="primary" onClick={() => save()} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </div>

      <div className="periodic-shell no-print">
        <div className="periodic-toolbar no-print">
          <h3 className="periodic-period-title">{mo.year} 年 {mo.month} 月</h3>
          <span className="row">
            <button className="ghost" onClick={() => shiftMonth(-1)}>← 上一月</button>
            <button className="ghost" onClick={() => shiftMonth(1)}>下一月 →</button>
          </span>
        </div>

        {monthly && (
          <>
            <div className="no-print">
              <AutoStats auto={monthly.auto} />
            </div>
            {monthly.period_rounds.length > 0 && (
              <div className="card no-print" style={{ marginBottom: 16 }}>
                <PeriodRounds rounds={monthly.period_rounds} title="本月清仓回合" onRoundsChange={loadMonthly} />
              </div>
            )}
            <div className="periodic-grid periodic-grid-2 no-print">
              <PeriodicField label="体系迭代" value={monthly.system_iteration}
                onChange={v => setMonthly({ ...monthly, system_iteration: v })}
                placeholder="本月交易体系有哪些调整？规则、仓位、选股逻辑的变化？" />
              <PeriodicField label="下月目标" value={monthly.next_goal}
                onChange={v => setMonthly({ ...monthly, next_goal: v })}
                placeholder="下月要攻克什么？节点目标、纪律重点、学习方向" />
            </div>
          </>
        )}
      </div>

      {monthly && (
        <div className="print-only">
          <MonthlyPrintBody
            username={username}
            year={mo.year}
            month={mo.month}
            periodLabel={periodLabel}
            auto={monthly.auto}
            node_state={monthly.node_state}
            system_iteration={monthly.system_iteration}
            next_goal={monthly.next_goal}
            period_rounds={monthly.period_rounds}
          />
        </div>
      )}
    </div>
  );
}
