import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fmtMoney, fmtPct } from '../api';
import type { LinkedRoundRow } from '../api';
import { aiRoundReview, roundDisplaySummary, roundKey, saveRoundSummary } from '../roundReview';
import { useToast } from '../components';
import { useAiBusy } from '../AiBusy';
import {
  PrintDocument,
  PrintDocHeader,
  PrintSection,
  PrintStatCard,
  PrintStatGrid,
  PrintTextBlock,
} from '../printLayout';

export interface PeriodAuto {
  start: string;
  end: string;
  return_pct: number;
  max_drawdown_pct: number;
  end_nav: number;
  trade_count: number;
  closed_rounds: number;
  round_pnl: number;
  win_rounds: number;
}

export function isoWeek(d: Date): [number, number] {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dayNum = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return [date.getUTCFullYear(), week];
}

export function AutoStats({ auto }: { auto: PeriodAuto }) {
  const winNote = auto.closed_rounds > 0
    ? `盈 ${auto.win_rounds} / ${auto.closed_rounds}`
    : '';
  return (
    <div className="periodic-stats">
      <StatItem label="区间收益" tone={auto.return_pct >= 0 ? 'pos' : 'neg'} value={fmtPct(auto.return_pct)} />
      <StatItem label="区间最大回撤" value={fmtPct(auto.max_drawdown_pct, false)} />
      <StatItem label="期末净值" value={auto.end_nav.toFixed(4)} />
      <StatItem label="交易 / 清仓回合" value={`${auto.trade_count} / ${auto.closed_rounds}`} note={winNote} />
      <StatItem label="回合盈亏" tone={auto.round_pnl >= 0 ? 'pos' : 'neg'} value={`¥${fmtMoney(auto.round_pnl)}`} />
    </div>
  );
}

function StatItem({
  label, value, tone, note,
}: {
  label: string;
  value: string;
  tone?: 'pos' | 'neg';
  note?: string;
}) {
  return (
    <div className="periodic-stat">
      <div className="periodic-stat-label">{label}</div>
      <div className={`periodic-stat-value${tone ? ` ${tone}` : ''}`}>{value}</div>
      {note && <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{note}</div>}
    </div>
  );
}

export function PeriodRounds({
  rounds,
  title = '清仓回合',
  compact = false,
  onRoundsChange,
}: {
  rounds: LinkedRoundRow[];
  title?: string;
  compact?: boolean;
  onRoundsChange?: () => void;
}) {
  const toast = useToast();
  const { setBusy } = useAiBusy();
  const [summaries, setSummaries] = useState<Record<string, string>>({});
  const [analyzingKey, setAnalyzingKey] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  useEffect(() => {
    const next: Record<string, string> = {};
    for (const r of rounds) {
      next[roundKey(r.code, r.start_date)] = r.review_summary ?? '';
    }
    setSummaries(next);
  }, [rounds]);

  const persistSummary = useCallback(async (r: LinkedRoundRow, value: string) => {
    const key = roundKey(r.code, r.start_date);
    if ((r.review_summary ?? '') === value.trim()) return;
    setSavingKey(key);
    try {
      await saveRoundSummary(r.code, r.start_date, value);
      onRoundsChange?.();
    } catch (e) {
      toast(String(e));
    } finally {
      setSavingKey(null);
    }
  }, [onRoundsChange, toast]);

  const runAiReview = useCallback(async (r: LinkedRoundRow) => {
    const key = roundKey(r.code, r.start_date);
    setAnalyzingKey(key);
    setBusy(true, `AI 分析 ${r.name || r.code} 回合…`);
    try {
      const summary = await aiRoundReview(r.code, r.start_date);
      setSummaries(prev => ({ ...prev, [key]: summary }));
      toast('回合 AI 摘要已生成');
      onRoundsChange?.();
    } catch (e) {
      toast(String(e));
    } finally {
      setAnalyzingKey(null);
      setBusy(false);
    }
  }, [onRoundsChange, setBusy, toast]);

  if (!rounds.length) return null;
  const closed = rounds.filter(r => r.status === 'closed');
  const wins = closed.filter(r => (r.pnl ?? 0) > 0);
  const losses = closed.filter(r => (r.pnl ?? 0) <= 0);
  const openCount = rounds.length - closed.length;
  const totalPnl = closed.reduce((s, r) => s + (r.pnl ?? 0), 0);
  return (
    <div className={`period-rounds${compact ? ' compact' : ''}`}>
      <div className="period-rounds-head">
        <h4 className="card-title" style={{ margin: 0 }}>{title}</h4>
        <span className="muted" style={{ fontSize: 12 }}>
          {closed.length > 0
            ? `${wins.length} 盈 · ${losses.length} 亏 · 合计 ¥${fmtMoney(totalPnl)}`
            : `${rounds.length} 笔`}
          {openCount > 0 ? ` · ${openCount} 持仓中` : ''}
        </span>
      </div>
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>周期</th>
            <th style={{ textAlign: 'right' }}>盈亏</th>
            <th style={{ textAlign: 'right' }}>收益率</th>
            {!compact && <th>回合复盘摘要</th>}
            {!compact && <th style={{ width: 92 }}>操作</th>}
          </tr>
        </thead>
        <tbody>
          {rounds.map(r => {
            const key = roundKey(r.code, r.start_date);
            const summaryVal = summaries[key] ?? '';
            const placeholder = r.review_snippet || '填写本回合复盘摘要，或点 AI 分析生成';
            const analyzing = analyzingKey === key;
            const saving = savingKey === key;
            return (
            <tr key={key}>
              <td>
                {r.name} <span className="muted mono">{r.code}</span>
                {r.closed_today && <span className="tag gold" style={{ marginLeft: 6, fontSize: 10 }}>今日清仓</span>}
              </td>
              <td className="muted" style={{ fontSize: 12 }}>
                <Link to={`/journal?day=${r.start_date}`}>{r.start_date}</Link>
                {' → '}
                {r.end_date
                  ? <Link to={`/journal?day=${r.end_date}`}>{r.end_date}</Link>
                  : '持仓中'}
              </td>
              <td style={{ textAlign: 'right' }} className={`mono ${r.pnl != null ? (r.pnl >= 0 ? 'pos' : 'neg') : ''}`}>
                {r.pnl != null ? `¥${fmtMoney(r.pnl)}` : '—'}
              </td>
              <td style={{ textAlign: 'right' }} className={`mono ${r.pnl_pct != null ? (r.pnl_pct >= 0 ? 'pos' : 'neg') : ''}`}>
                {fmtPct(r.pnl_pct)}
              </td>
              {!compact && (
                <td>
                  <textarea
                    className="period-round-summary-input"
                    value={summaryVal}
                    placeholder={placeholder}
                    rows={3}
                    onChange={e => setSummaries(prev => ({ ...prev, [key]: e.target.value }))}
                    onBlur={e => void persistSummary(r, e.target.value)}
                  />
                  {!summaryVal.trim() && r.review_snippet && (
                    <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                      日复盘摘录：{r.review_snippet}
                    </div>
                  )}
                </td>
              )}
              {!compact && (
                <td>
                  <button
                    className="ghost"
                    style={{ fontSize: 12, whiteSpace: 'nowrap' }}
                    disabled={analyzing || analyzingKey !== null}
                    onClick={() => void runAiReview(r)}
                  >
                    {analyzing ? '分析中…' : saving ? '保存中…' : '✦ AI 分析'}
                  </button>
                </td>
              )}
            </tr>
          )})}
        </tbody>
      </table>
    </div>
  );
}

export function PeriodRoundsPrint({ rounds }: { rounds: LinkedRoundRow[]; title?: string }) {
  if (!rounds.length) return null;
  const closed = rounds.filter(r => r.status === 'closed');
  const wins = closed.filter(r => (r.pnl ?? 0) > 0);
  const losses = closed.filter(r => (r.pnl ?? 0) <= 0);
  const totalPnl = closed.reduce((s, r) => s + (r.pnl ?? 0), 0);
  const summary = closed.length > 0
    ? `${wins.length} 盈 · ${losses.length} 亏 · 合计 ¥${fmtMoney(totalPnl)}`
    : `${rounds.length} 笔`;
  return (
    <div className="print-rounds-block">
      <div className="print-rounds-summary">{summary}</div>
      <div className="print-rounds-list">
        {rounds.map(r => {
          const text = roundDisplaySummary(r.review_summary, r.review_snippet);
          const win = r.pnl != null && r.pnl >= 0;
          const lose = r.pnl != null && r.pnl < 0;
          return (
            <div className="print-round-card" key={`${r.code}-${r.start_date}`}>
              <div className="print-round-card-top">
                <div className="print-round-card-main">
                  <div className="print-round-card-name">{r.name}</div>
                  <div className="print-round-card-meta mono">
                    {r.code} · {r.start_date} → {r.end_date ?? '持仓中'}
                  </div>
                </div>
                {r.pnl != null ? (
                  <div className={`print-round-pnl-badge${win ? ' win' : lose ? ' lose' : ''}`}>
                    <span className="print-round-pnl-amt mono">¥{fmtMoney(r.pnl)}</span>
                    <span className="print-round-pnl-pct mono">{fmtPct(r.pnl_pct)}</span>
                  </div>
                ) : (
                  <span className="print-round-status-tag">持仓中</span>
                )}
              </div>
              {text ? (
                <p className="print-round-snippet">{text}</p>
              ) : (
                <p className="print-round-snippet is-empty">（未填写回合复盘摘要）</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function PeriodicField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="periodic-field">
      <span className="periodic-field-label">{label}</span>
      <textarea
        className="periodic-textarea"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

export function AutoStatsPrint({ auto }: { auto: PeriodAuto }) {
  const winNote = auto.closed_rounds > 0 ? `盈 ${auto.win_rounds} / ${auto.closed_rounds}` : '';
  return (
    <PrintStatGrid>
      <PrintStatCard label="区间收益" tone={auto.return_pct >= 0 ? 'pos' : 'neg'} value={fmtPct(auto.return_pct)} />
      <PrintStatCard label="区间最大回撤" value={fmtPct(auto.max_drawdown_pct, false)} />
      <PrintStatCard label="期末净值" value={auto.end_nav.toFixed(4)} />
      <PrintStatCard
        label="交易 / 清仓回合"
        value={`${auto.trade_count} / ${auto.closed_rounds}`}
        note={winNote}
      />
      <PrintStatCard label="回合盈亏" tone={auto.round_pnl >= 0 ? 'pos' : 'neg'} value={`¥${fmtMoney(auto.round_pnl)}`} />
    </PrintStatGrid>
  );
}

export { PrintDocHeader, PrintTextBlock } from '../printLayout';

export function WeeklyPrintBody({
  username, year, week, periodLabel, auto,
  market_review, right_things, wrong_things, next_strategy,
  period_rounds = [],
}: {
  username: string;
  year: number;
  week: number;
  periodLabel: string;
  auto: PeriodAuto;
  market_review: string;
  right_things: string;
  wrong_things: string;
  next_strategy: string;
  period_rounds?: LinkedRoundRow[];
}) {
  return (
    <PrintDocument>
      <PrintDocHeader
        username={username}
        title="周复盘"
        subtitle={`${year} 第 ${week} 周 · ${periodLabel}`}
      />
      <PrintSection title="区间统计">
        <AutoStatsPrint auto={auto} />
      </PrintSection>
      {period_rounds.length > 0 && (
        <PrintSection title="本周清仓回合">
          <PeriodRoundsPrint rounds={period_rounds} />
        </PrintSection>
      )}
      <PrintSection title="复盘正文">
        <PrintTextBlock plain label="本周盘面回顾" text={market_review} />
        <PrintTextBlock plain label="本周做对的事" text={right_things} />
        <PrintTextBlock plain label="本周做错的事" text={wrong_things} />
        <PrintTextBlock plain label="下周策略" text={next_strategy} />
      </PrintSection>
    </PrintDocument>
  );
}

export function MonthlyPrintBody({
  username, year, month, periodLabel, auto, node_state,
  system_iteration, next_goal, period_rounds = [],
}: {
  username: string;
  year: number;
  month: number;
  periodLabel: string;
  auto: PeriodAuto;
  node_state: { lit_count: number; node_count: number; nav: number };
  system_iteration: string;
  next_goal: string;
  period_rounds?: LinkedRoundRow[];
}) {
  return (
    <PrintDocument>
      <PrintDocHeader
        username={username}
        title="月复盘"
        subtitle={`${year} 年 ${month} 月 · ${periodLabel}`}
      />
      <PrintSection title="区间统计">
        <AutoStatsPrint auto={auto} />
      </PrintSection>
      {period_rounds.length > 0 && (
        <PrintSection title="本月清仓回合">
          <PeriodRoundsPrint rounds={period_rounds} />
        </PrintSection>
      )}
      <PrintSection title="节点征途">
        <div className="print-node-banner">
          <span>已点亮 <strong>{node_state.lit_count}</strong> / {node_state.node_count} 节点</span>
          <span>当前净值 <strong className="mono">{node_state.nav.toFixed(4)}</strong></span>
        </div>
      </PrintSection>
      <PrintSection title="体系与目标">
        <PrintTextBlock plain label="体系迭代" text={system_iteration} />
        <PrintTextBlock plain label="下月目标" text={next_goal} />
      </PrintSection>
    </PrintDocument>
  );
}
