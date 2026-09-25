import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { printWeeklyReview } from '../printPage';
import { useToast } from '../components';
import { useAiBusy } from '../AiBusy';
import { confirmDiscard, useAutosave, useDirtyGuard } from '../hooks/usePersist';
import {
  AutoStats, PeriodicField, PeriodRounds, WeeklyPrintBody, isoWeek, type PeriodAuto,
} from './periodicShared';
import type { LinkedRoundRow } from '../api';

interface WeeklyData {
  year: number;
  week: number;
  right_things: string;
  wrong_things: string;
  market_review: string;
  next_strategy: string;
  auto: PeriodAuto;
  period_rounds: LinkedRoundRow[];
}

function weeklySaveSnapshot(w: Pick<WeeklyData, 'right_things' | 'wrong_things' | 'market_review' | 'next_strategy'>): string {
  return JSON.stringify({
    right_things: w.right_things,
    wrong_things: w.wrong_things,
    market_review: w.market_review,
    next_strategy: w.next_strategy,
  });
}

export default function WeeklyReview() {
  const toast = useToast();
  const { setBusy } = useAiBusy();
  const now = new Date();
  const [defYear, defWeek] = isoWeek(now);
  const [wk, setWk] = useState({ year: defYear, week: defWeek });
  const [weekly, setWeekly] = useState<WeeklyData | null>(null);
  const [savedSnapshot, setSavedSnapshot] = useState('');
  const [username, setUsername] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [autoSavedAt, setAutoSavedAt] = useState<number | null>(null);

  const currentSnapshot = useMemo(
    () => (weekly ? weeklySaveSnapshot(weekly) : ''),
    [weekly],
  );
  const dirty = savedSnapshot !== '' && currentSnapshot !== savedSnapshot && !!weekly;
  useDirtyGuard(dirty);

  useEffect(() => {
    api.get<{ pdf_username?: string }>('/api/settings').then(v => setUsername(v.pdf_username ?? '')).catch(e => toast(String(e)));
  }, [toast]);

  const loadWeekly = useCallback(() => {
    api.get<WeeklyData>(`/api/reviews/weekly/${wk.year}/${wk.week}`).then(res => {
      setWeekly({ ...res, period_rounds: res.period_rounds ?? [] });
      setSavedSnapshot(weeklySaveSnapshot(res));
      setAutoSavedAt(null);
    }).catch(e => toast(String(e)));
  }, [wk, toast]);

  useEffect(loadWeekly, [loadWeekly]);

  const requestWeekChange = (next: { year: number; week: number }) => {
    if (next.year === wk.year && next.week === wk.week) return;
    if (!confirmDiscard(dirty)) return;
    setSavedSnapshot('');
    setWk(next);
  };

  const shiftWeek = (delta: number) => {
    const monday = new Date(Date.UTC(wk.year, 0, 4));
    monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() || 7) + 1 + (wk.week - 1 + delta) * 7);
    const [y, w] = isoWeek(new Date(monday.getUTCFullYear(), monday.getUTCMonth(), monday.getUTCDate()));
    requestWeekChange({ year: y, week: w });
  };

  const save = async (silent = false) => {
    if (!weekly) return;
    setSaving(true);
    try {
      await api.put(`/api/reviews/weekly/${wk.year}/${wk.week}`, {
        right_things: weekly.right_things,
        wrong_things: weekly.wrong_things,
        market_review: weekly.market_review,
        next_strategy: weekly.next_strategy,
      });
      setSavedSnapshot(weeklySaveSnapshot(weekly));
      if (silent) setAutoSavedAt(Date.now());
      else toast('周复盘已保存');
    } catch (e) { toast(String(e)); } finally { setSaving(false); }
  };

  useAutosave(
    !!weekly && dirty && !saving && !reviewing,
    async () => { await save(true); },
    [currentSnapshot],
  );

  const aiReview = async () => {
    await save(true);
    setReviewing(true);
    setBusy(true, 'AI 周复盘生成中…');
    try {
      const result = await api.post<Partial<WeeklyData>>(`/api/reviews/weekly/${wk.year}/${wk.week}/ai-review`);
      setWeekly(prev => prev ? {
        ...prev,
        market_review: result.market_review ?? prev.market_review,
        right_things: result.right_things ?? prev.right_things,
        wrong_things: result.wrong_things ?? prev.wrong_things,
        next_strategy: result.next_strategy ?? prev.next_strategy,
      } : prev);
      toast('AI 周复盘草稿已生成，请核对后保存');
    } catch (e) { toast(String(e)); } finally {
      setReviewing(false);
      setBusy(false);
    }
  };

  const handlePrint = async () => {
    if (!weekly) return;
    if (dirty) await save(true);
    printWeeklyReview(username, wk.year, wk.week, msg => toast(msg));
  };

  const periodLabel = weekly ? `${weekly.auto.start} ~ ${weekly.auto.end}` : '';

  return (
    <div className="fade-in periodic-page">
      <div className="page-head no-print">
        <div>
          <h2 className="page-title">周复盘</h2>
          <div className="page-sub">拉远镜头，检视一周节奏与纪律</div>
        </div>
        <div className="row">
          {(dirty || saving || autoSavedAt) && (
            <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
              {saving ? '保存中…' : dirty ? '有未保存修改' : `已自动保存 ${new Date(autoSavedAt!).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`}
            </span>
          )}
          <button onClick={handlePrint} disabled={!weekly}>打印</button>
          <button onClick={aiReview} disabled={reviewing}>{reviewing ? 'AI 复盘中…' : '✦ AI 复盘'}</button>
          <button className="primary" onClick={() => save()} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </div>

      <div className="periodic-shell no-print">
        <div className="periodic-toolbar no-print">
          <h3 className="periodic-period-title">
            {wk.year} 第 {wk.week} 周
            {weekly && <span className="muted">（{periodLabel}）</span>}
          </h3>
          <span className="row">
            <button className="ghost" onClick={() => shiftWeek(-1)}>← 上一周</button>
            <button className="ghost" onClick={() => shiftWeek(1)}>下一周 →</button>
          </span>
        </div>

        {weekly && (
          <>
            <div className="no-print">
              <AutoStats auto={weekly.auto} />
            </div>
            {weekly.period_rounds.length > 0 && (
              <div className="card no-print" style={{ marginBottom: 16 }}>
                <PeriodRounds rounds={weekly.period_rounds} title="本周清仓回合" onRoundsChange={loadWeekly} />
              </div>
            )}
            <div className="periodic-grid periodic-grid-4 no-print">
              <PeriodicField label="本周盘面回顾" value={weekly.market_review}
                onChange={v => setWeekly({ ...weekly, market_review: v })}
                placeholder="资金去了哪些板块？整体热度如何？大盘向上还是向下？" />
              <PeriodicField label="本周做对的事" value={weekly.right_things}
                onChange={v => setWeekly({ ...weekly, right_things: v })}
                placeholder="坚持了什么原则？哪些操作值得复制？" />
              <PeriodicField label="本周做错的事" value={weekly.wrong_things}
                onChange={v => setWeekly({ ...weekly, wrong_things: v })}
                placeholder="违背了什么纪律？亏损的根源？" />
              <PeriodicField label="下周策略" value={weekly.next_strategy}
                onChange={v => setWeekly({ ...weekly, next_strategy: v })}
                placeholder="下周的仓位基调与主攻方向" />
            </div>
          </>
        )}
      </div>

      {weekly && (
        <div className="print-only">
          <WeeklyPrintBody
            username={username}
            year={wk.year}
            week={wk.week}
            periodLabel={periodLabel}
            auto={weekly.auto}
            market_review={weekly.market_review}
            right_things={weekly.right_things}
            wrong_things={weekly.wrong_things}
            next_strategy={weekly.next_strategy}
            period_rounds={weekly.period_rounds}
          />
        </div>
      )}
    </div>
  );
}
