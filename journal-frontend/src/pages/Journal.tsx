import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  api, fmtMoney, today, SCORE_DIMS,
  type DailyReview, type PositionRehearsal, type ScoreEntry, type SnapshotPosition,
  type TGroup, type TradeScoreBundle, type WatchItem,
} from '../api';
import { Empty, QtyStepper, SideTag, useToast, DateInput, StockPicker } from '../components';
import { printDailyReview } from '../printPage';
import { fmtCnDate } from '../printCss';
import {
  PrintDocument,
  PrintDocHeader,
  PrintScoreDimRows,
  PrintSection,
  PrintSummaryBox,
  PrintTextBlock,
  PrintTradeSide,
} from '../printLayout';
import { PrintMarkdownBody } from '../printMarkdown';
import { fetchCloseOnDay } from '../marketClose';
import { PeriodRounds } from './periodicShared';
import { useAiBusy } from '../AiBusy';
import { confirmDiscard, useAutosave, useDirtyGuard } from '../hooks/usePersist';

function journalSaveSnapshot(d: DailyReview): string {
  return JSON.stringify({
    market_observation: d.market_observation,
    decision_review: d.decision_review,
    mistakes: d.mistakes,
    scores: d.scores,
    trade_scores: d.trade_scores ?? {},
    next_market_forecast: d.next_market_forecast,
    next_watchlist: d.next_watchlist,
    next_position_plan: d.next_position_plan,
    next_risk_plan: d.next_risk_plan,
    next_position_rehearsal: d.next_position_rehearsal ?? [],
    rehearsal_ai_analysis: d.rehearsal_ai_analysis ?? '',
  });
}

type DayTrade = DailyReview['trades'][number];

function tradeSummary(ts: TradeScoreBundle): string {
  const raw = ts._summary;
  return raw && typeof raw === 'object' && 'comment' in raw ? (raw.comment ?? '') : '';
}

function isScoreEntry(entry: unknown): entry is ScoreEntry {
  return !!entry && typeof entry === 'object' && ('ai' in entry || 'final' in entry || 'comment' in entry);
}

function tradeDimEntry(ts: TradeScoreBundle, dim: string): ScoreEntry | undefined {
  const entry = ts[dim];
  return isScoreEntry(entry) ? entry : undefined;
}

function tradeAvgScore(ts: TradeScoreBundle): number | null {
  const vals = Object.keys(SCORE_DIMS)
    .map(dim => {
      const entry = tradeDimEntry(ts, dim);
      return entry?.ai ?? entry?.final ?? null;
    })
    .filter((v): v is number => v != null);
  if (vals.length === 0) return null;
  return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
}

function tradeHasAnalysis(ts: TradeScoreBundle) {
  if (tradeSummary(ts)) return true;
  return Object.keys(SCORE_DIMS).some(dim => {
    const entry = tradeDimEntry(ts, dim);
    return entry?.ai != null || entry?.final != null || Boolean(entry?.comment);
  });
}

function groupScoreKey(code: string) {
  return `g:${code}`;
}

function organizeSections(trades: DayTrade[], tGroups: TGroup[]) {
  const seen = new Set<string>();
  const sections: Array<{ kind: 't'; group: TGroup } | { kind: 'single'; trade: DayTrade }> = [];
  for (const t of trades) {
    const group = tGroups.find(g => g.trade_ids.includes(t.id));
    if (group) {
      if (!seen.has(group.id)) {
        seen.add(group.id);
        sections.push({ kind: 't', group });
      }
    } else {
      sections.push({ kind: 'single', trade: t });
    }
  }
  return sections;
}

function normalizeScore(value: unknown): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** 圆点显示分：默认跟 AI；仅当用户手动改分（final ≠ ai）时才显示 final。 */
function scoreForDots(entry: ScoreEntry): number {
  const ai = normalizeScore(entry.ai);
  const final = normalizeScore(entry.final);
  if (ai != null && final != null && final !== ai) {
    return final;
  }
  return ai ?? final ?? 0;
}

function scoreDisplayValue(entry: ScoreEntry): string | number {
  const dots = scoreForDots(entry);
  return dots > 0 ? dots : '—';
}

function ScoreDimRows({
  scores,
  onSetScore,
  side,
}: {
  scores: Record<string, ScoreEntry | undefined>;
  onSetScore?: (dim: string, value: number) => void;
  side?: string;
}) {
  return (
    <>
      {Object.entries(SCORE_DIMS).map(([dim, label]) => {
        const entry = scores[dim] ?? {};
        const dotScore = scoreForDots(entry);
        const dimLabel = dim === 'entry' && side === 'sell' ? '买点质量'
          : dim === 'exit' && side === 'buy' ? '卖点质量'
          : label;
        const inactive = (dim === 'entry' && side === 'sell') || (dim === 'exit' && side === 'buy');
        if (inactive && entry.ai == null && entry.final == null && !entry.comment) {
          return null;
        }
        return (
          <div className="score-row score-row-wrap" key={`${dim}-${entry.ai ?? ''}-${entry.final ?? ''}`}>
            <span className="score-dim">{dimLabel}</span>
            <div className="score-dots">
              {Array.from({ length: 10 }, (_, i) => i + 1).map(v => (
                <button
                  key={v}
                  type="button"
                  className={`score-dot no-print${dotScore >= v ? ' on' : ''}`}
                  title={`${v}分`}
                  disabled={!onSetScore}
                  onClick={() => onSetScore?.(dim, v)}
                />
              ))}
              <span className="print-only score-print-value">{scoreDisplayValue(entry)}</span>
            </div>
            {entry.ai != null && <span className="tag gold no-print">AI {entry.ai}</span>}
            {entry.comment && <span className="score-comment-wrap">{entry.comment}</span>}
          </div>
        );
      })}
    </>
  );
}

function AnalysisDetailPanel({
  data,
  focusedTradeId,
  focusedGroupId,
  scoringId,
  onAnalyzeTrade,
  onAnalyzeGroup,
  onFocusGroup,
  onSetTradeScore,
}: {
  data: DailyReview;
  focusedTradeId: number | null;
  focusedGroupId: string | null;
  scoringId: number | 'all' | 'batch' | 't-group' | null;
  onAnalyzeTrade: (id: number) => void;
  onAnalyzeGroup: (group: TGroup) => void;
  onFocusGroup: (groupId: string) => void;
  onSetTradeScore: (tradeId: number, dim: string, value: number) => void;
}) {
  const tGroups = data.t_groups ?? [];

  if (focusedGroupId) {
    const group = tGroups.find(g => g.id === focusedGroupId);
    if (!group) return <Empty text="做T组合不存在" />;
    const gs = data.trade_scores?.[groupScoreKey(group.code)] ?? {};
    const summary = tradeSummary(gs);
    const avg = tradeAvgScore(gs);
    const groupTrades = data.trades.filter(t => group.trade_ids.includes(t.id));
    const analyzing = scoringId === 't-group';
    return (
      <div className="analysis-detail">
        <div className="analysis-detail-head">
          <span className="tag gold">做T</span>
          <span className="analysis-detail-title">{group.name}</span>
          <span className="muted mono">{group.code}</span>
          {avg != null && <span className="tag gold">均分 {avg}</span>}
        </div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          含 {groupTrades.length} 笔：{groupTrades.map(t => `${t.side === 'buy' ? '买' : '卖'}${t.qty}@${t.price}`).join(' · ')}
        </div>
        {!tradeHasAnalysis(gs) ? (
          <>
            <Empty text="尚未分析此做T组合" />
            <button className="primary no-print" style={{ marginTop: 12 }} disabled={analyzing || scoringId !== null}
              onClick={() => onAnalyzeGroup(group)}>
              {analyzing ? '分析中…' : '✦ AI 分析做T'}
            </button>
          </>
        ) : (
          <>
            {summary && <div className="trade-score-summary">{summary}</div>}
            <ScoreDimRows scores={gs as Record<string, ScoreEntry | undefined>} />
            <button className="ghost no-print" style={{ marginTop: 12 }} disabled={analyzing || scoringId !== null}
              onClick={() => onAnalyzeGroup(group)}>
              {analyzing ? '分析中…' : '重新分析做T'}
            </button>
            <div className="analysis-detail-sub" style={{ marginTop: 16 }}>
              <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>组合内逐笔</div>
              {groupTrades.map(t => {
                const ts = data.trade_scores?.[String(t.id)] ?? {};
                const legSummary = tradeSummary(ts);
                return (
                  <div key={t.id} className="analysis-leg-item">
                    <SideTag side={t.side} />
                    <span className="mono muted">{t.price} × {t.qty}</span>
                    {legSummary && <span className="analysis-leg-summary">{legSummary}</span>}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    );
  }

  if (focusedTradeId != null) {
    const trade = data.trades.find(t => t.id === focusedTradeId);
    if (!trade) return <Empty text="交易不存在" />;
    const ts = data.trade_scores?.[String(trade.id)] ?? {};
    const summary = tradeSummary(ts);
    const avg = tradeAvgScore(ts);
    const tGroup = tGroups.find(g => g.trade_ids.includes(trade.id));
    const analyzing = scoringId === trade.id;
    const hasScores = tradeHasAnalysis(ts);
    return (
      <div className="analysis-detail">
        <div className="analysis-detail-head">
          <SideTag side={trade.side} />
          <span className="analysis-detail-title">{trade.name || trade.code}</span>
          <span className="muted mono">{trade.code}</span>
          {avg != null && <span className="tag gold">均分 {avg}</span>}
        </div>
        <div className="muted mono" style={{ fontSize: 12, marginBottom: 10 }}>
          {trade.price} × {trade.qty} · 费用 {trade.fees}
        </div>
        {tGroup && (
          <button className="ghost no-print" style={{ marginBottom: 10, fontSize: 12 }}
            onClick={() => onFocusGroup(tGroup.id)}>
            查看做T整体分析 →
          </button>
        )}
        {!hasScores ? (
          <>
            <Empty text="尚未分析此笔交易" />
            <button className="primary no-print" style={{ marginTop: 12 }} disabled={analyzing || scoringId !== null}
              onClick={() => onAnalyzeTrade(trade.id)}>
              {analyzing ? '分析中…' : '✦ AI 分析此笔'}
            </button>
          </>
        ) : (
          <>
            {summary && <div className="trade-score-summary">{summary}</div>}
            <ScoreDimRows
              scores={ts as Record<string, ScoreEntry | undefined>}
              side={trade.side}
              onSetScore={(dim, v) => onSetTradeScore(trade.id, dim, v)}
            />
            <button className="ghost no-print" style={{ marginTop: 12 }} disabled={analyzing || scoringId !== null}
              onClick={() => onAnalyzeTrade(trade.id)}>
              {analyzing ? '分析中…' : '重新分析此笔'}
            </button>
          </>
        )}
      </div>
    );
  }

  return <Empty text="点击左侧交易卡片或做T组合，此处显示对应 AI 分析" />;
}

const REHEARSAL_STATUS: Record<string, string> = {
  match: '一致',
  planned_hold_gone: '预演持有但未持有',
  unplanned_new: '未预演但新开',
  more_than_planned: '多于预演',
  less_than_planned: '少于预演',
};

function positionClose(p: SnapshotPosition | PositionRehearsal): number | null {
  const qty = p.qty;
  const mv = 'market_value' in p ? p.market_value : undefined;
  const rawPrice = 'price' in p ? p.price : undefined;
  if (mv != null && qty != null && qty > 0) {
    const implied = mv / qty;
    if (rawPrice != null && rawPrice > 0) {
      const ratio = rawPrice / implied;
      if (ratio >= 5 || ratio <= 0.2) return Math.round(implied * 10000) / 10000;
    }
    return Math.round(implied * 10000) / 10000;
  }
  if (p.close != null && p.close > 0) return p.close;
  if (rawPrice != null && rawPrice > 0) return rawPrice;
  return null;
}

function JournalPrintReport({ data, day, username }: { data: DailyReview; day: string; username: string }) {
  const tGroups = data.t_groups ?? [];
  const tradeCount = data.trades.length;
  const assets = data.snapshot?.total_assets;

  return (
    <PrintDocument>
      <PrintDocHeader
        username={username}
        title="每日复盘"
        subtitle={`${fmtCnDate(day)} · ${day}`}
        badge={tradeCount > 0 ? `${tradeCount} 笔交易` : undefined}
      />

      {assets != null && (
        <PrintSection title="收盘概览">
          <div className="print-daily-hero">
            <div className="print-daily-hero-item">
              <span className="print-daily-hero-label">总资产</span>
              <span className="print-daily-hero-value">¥{fmtMoney(assets)}</span>
            </div>
            {data.snapshot?.positions?.length ? (
              <div className="print-daily-hero-item">
                <span className="print-daily-hero-label">持仓数</span>
                <span className="print-daily-hero-value">{data.snapshot.positions.length}</span>
              </div>
            ) : null}
          </div>
          {(data.snapshot?.positions?.length ?? 0) > 0 && (
            <table className="print-table print-table-elegant">
              <thead><tr><th>代码</th><th>名称</th><th className="num">数量</th></tr></thead>
              <tbody>
                {data.snapshot!.positions.map((p, i) => (
                  <tr key={i}>
                    <td className="mono">{p.code}</td>
                    <td>{p.name}</td>
                    <td className="num mono">{p.qty}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </PrintSection>
      )}

      <PrintSection title="当日交易与 AI 分析">
        {data.trades.length === 0 ? (
          <p className="print-muted">当日无交易</p>
        ) : data.trades.map(t => {
          const ts = data.trade_scores?.[String(t.id)] ?? {};
          const summary = tradeSummary(ts);
          const avg = tradeAvgScore(ts);
          const tGroup = tGroups.find(g => g.trade_ids.includes(t.id));
          return (
            <div className="print-trade-card" key={t.id}>
              <div className="print-trade-card-head">
                <PrintTradeSide side={t.side} />
                <div className="print-trade-card-info">
                  <div className="print-trade-card-name">{t.name || t.code}</div>
                  <div className="print-trade-card-meta mono">{t.code} · {t.price} × {t.qty}</div>
                </div>
                <div className="print-trade-card-tags">
                  {tGroup && <span className="print-tag">做T</span>}
                  {avg != null && <span className="print-tag gold">均分 {avg}</span>}
                </div>
              </div>
              <PrintSummaryBox label="AI 总评" text={summary ?? ''} />
              {tradeHasAnalysis(ts) ? (
                <PrintScoreDimRows scores={ts as Record<string, ScoreEntry | undefined>} side={t.side} />
              ) : (
                <p className="print-muted">未分析</p>
              )}
            </div>
          );
        })}
        {tGroups.map(g => {
          const gs = data.trade_scores?.[groupScoreKey(g.code)] ?? {};
          if (!tradeHasAnalysis(gs)) return null;
          return (
            <div className="print-trade-card" key={g.id}>
              <div className="print-trade-card-head">
                <span className="print-trade-side t-group">T</span>
                <div className="print-trade-card-info">
                  <div className="print-trade-card-name">做T · {g.name}</div>
                  <div className="print-trade-card-meta mono">{g.code}</div>
                </div>
              </div>
              <PrintSummaryBox label="组合总评" text={tradeSummary(gs) ?? ''} />
              <PrintScoreDimRows scores={gs as Record<string, ScoreEntry | undefined>} />
            </div>
          );
        })}
      </PrintSection>

      <PrintSection title="整日操作概览">
        <PrintScoreDimRows scores={data.scores} />
        <PrintSummaryBox label="整日 AI 总评" text={data.ai_summary ?? ''} />
      </PrintSection>

      <PrintSection title="复盘正文">
        <PrintTextBlock label="盘面观察 · 大盘 / 板块 / 市场情绪" text={data.market_observation} />
        <PrintTextBlock label="决策复盘 · 每笔操作的理由与对错" text={data.decision_review} />
        <PrintTextBlock label="错误与教训" text={data.mistakes} />
      </PrintSection>

      {(data.next_position_rehearsal?.length ?? 0) > 0 && (
        <PrintSection title="明日操作预演">
          <table className="print-table print-table-elegant">
            <thead><tr><th>代码</th><th>名称</th><th className="num">预演持仓</th><th>备注</th></tr></thead>
            <tbody>
              {data.next_position_rehearsal.map((p, i) => (
                <tr key={i}>
                  <td className="mono">{p.code}</td>
                  <td>{p.name}</td>
                  <td className="num mono">{p.qty} 股{p.qty === 0 ? '（清仓）' : ''}</td>
                  <td>{p.note ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </PrintSection>
      )}

      {data.rehearsal_ai_analysis?.trim() && (
        <PrintSection title="明日预演 AI 分析">
          <PrintMarkdownBody text={data.rehearsal_ai_analysis} />
        </PrintSection>
      )}

      <PrintSection title="次日预研">
        <PrintTextBlock label="大盘预判" text={data.next_market_forecast} />
        <PrintTextBlock label="仓位计划" text={data.next_position_plan} />
        <PrintTextBlock label="风险预案" text={data.next_risk_plan} />
        {(data.next_watchlist?.length ?? 0) > 0 && (
          <table className="print-table print-table-elegant" style={{ marginTop: 12 }}>
            <thead>
              <tr><th>代码</th><th>名称</th><th>触发条件</th><th>计划动作</th></tr>
            </thead>
            <tbody>
              {data.next_watchlist.map((w, i) => (
                <tr key={i}>
                  <td className="mono">{w.code || '—'}</td>
                  <td>{w.name || '—'}</td>
                  <td>{w.condition || '—'}</td>
                  <td>{w.action || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </PrintSection>
    </PrintDocument>
  );
}

export default function Journal() {
  const toast = useToast();
  const { setBusy } = useAiBusy();
  const [searchParams] = useSearchParams();
  const [day, setDay] = useState(() => searchParams.get('day') || today());
  const [data, setData] = useState<DailyReview | null>(null);
  const [savedSnapshot, setSavedSnapshot] = useState('');
  const [scoringId, setScoringId] = useState<number | 'all' | 'batch' | 't-group' | null>(null);
  const [focusedTradeId, setFocusedTradeId] = useState<number | null>(null);
  const [focusedGroupId, setFocusedGroupId] = useState<string | null>(null);
  const [selectedTradeIds, setSelectedTradeIds] = useState<number[]>([]);
  const scoreLockRef = useRef(false);
  const focusAfterLoadRef = useRef<{ tradeId?: number | null; groupId?: string | null } | null>(null);
  const loadSeqRef = useRef(0);
  const [loading, setLoading] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [copyingRehearsal, setCopyingRehearsal] = useState(false);
  const [rehearsalCloseLoading, setRehearsalCloseLoading] = useState<number | null>(null);
  const [rehearsalReviewing, setRehearsalReviewing] = useState(false);
  const [username, setUsername] = useState('');
  const [saving, setSaving] = useState(false);
  const [autoSavedAt, setAutoSavedAt] = useState<number | null>(null);
  const imgRef = useRef<HTMLInputElement>(null);

  const currentSnapshot = useMemo(
    () => (data?.review_date === day && data ? journalSaveSnapshot(data) : ''),
    [data, day],
  );
  const dirty = savedSnapshot !== '' && currentSnapshot !== savedSnapshot && !!data;
  useDirtyGuard(dirty);

  const requestDayChange = (next: string) => {
    if (next === day) return;
    if (!confirmDiscard(dirty)) return;
    setSavedSnapshot('');
    setDay(next);
  };

  const load = useCallback((d: string): Promise<void> => {
    const seq = ++loadSeqRef.current;
    return api.get<DailyReview>(`/api/reviews/daily/${d}`).then(res => {
      if (seq !== loadSeqRef.current) return;
      if (res.review_date !== d) return;
      const normalized: DailyReview = {
        ...res,
        market_observation: res.market_observation ?? '',
        decision_review: res.decision_review ?? '',
        mistakes: res.mistakes ?? '',
        ai_summary: res.ai_summary ?? '',
        next_market_forecast: res.next_market_forecast ?? '',
        next_position_plan: res.next_position_plan ?? '',
        next_risk_plan: res.next_risk_plan ?? '',
        images: res.images ?? [],
        scores: res.scores ?? {},
        trade_scores: res.trade_scores ?? {},
        trades: res.trades ?? [],
        next_watchlist: res.next_watchlist ?? [],
        t_groups: res.t_groups ?? [],
        next_position_rehearsal: res.next_position_rehearsal ?? [],
        today_positions: res.today_positions ?? [],
        rehearsal_baseline: res.rehearsal_baseline ?? {
          cash: res.snapshot?.available_cash ?? 0,
          total_assets: res.snapshot?.total_assets ?? 0,
        },
        prev_rehearsal: res.prev_rehearsal ?? [],
        rehearsal_compare: res.rehearsal_compare ?? [],
        rehearsal_ai_analysis: res.rehearsal_ai_analysis ?? '',
        day_rounds: res.day_rounds ?? [],
      };
      setData(normalized);
      setSavedSnapshot(journalSaveSnapshot(normalized));
      setAutoSavedAt(null);
      const keep = focusAfterLoadRef.current;
      if (keep) {
        if (keep.groupId) {
          setFocusedGroupId(keep.groupId);
          setFocusedTradeId(null);
        } else if (keep.tradeId != null) {
          setFocusedTradeId(keep.tradeId);
          setFocusedGroupId(null);
        }
        focusAfterLoadRef.current = null;
      }
    }).catch(e => {
      if (seq === loadSeqRef.current) toast(String(e));
      throw e;
    });
  }, [toast]);

  useEffect(() => {
    api.get<{ pdf_username?: string }>('/api/settings').then(v => setUsername(v.pdf_username ?? '')).catch(() => {});
  }, []);

  useEffect(() => {
    const q = searchParams.get('day');
    if (q && q !== day) requestDayChange(q);
  }, [searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setData(null);
    setLoading(true);
    setFocusedTradeId(null);
    setFocusedGroupId(null);
    setSelectedTradeIds([]);
    load(day).finally(() => setLoading(false));
  }, [day, load]);

  const patch = (p: Partial<DailyReview>) => {
    setData(d => (d && d.review_date === day ? { ...d, ...p } : d));
  };

  type ScoreApiResult = {
    trade_scores: Record<string, TradeScoreBundle>;
    scores: Record<string, ScoreEntry>;
    summary: string;
    merged_count?: number;
  };

  const applyScoreResult = (result: ScoreApiResult) => {
    patch({
      trade_scores: result.trade_scores,
      scores: result.scores,
      ai_summary: result.summary,
    });
  };

  const saveTextsForAi = async () => {
    if (!data) return;
    await api.put(`/api/reviews/daily/${day}`, {
      market_observation: data.market_observation,
      decision_review: data.decision_review,
      mistakes: data.mistakes,
      next_market_forecast: data.next_market_forecast,
      next_watchlist: data.next_watchlist,
      next_position_plan: data.next_position_plan,
      next_risk_plan: data.next_risk_plan,
    });
  };

  const persistAndRefresh = async (
    message: string,
    keep?: { tradeId?: number | null; groupId?: string | null },
    result?: ScoreApiResult,
  ) => {
    if (keep?.groupId) {
      setFocusedGroupId(keep.groupId);
      setFocusedTradeId(null);
    } else if (keep?.tradeId != null) {
      setFocusedTradeId(keep.tradeId);
      setFocusedGroupId(null);
    }
    if (result) {
      applyScoreResult(result);
      await save(true);
    } else {
      await load(day);
    }
    toast(message);
  };

  const save = async (silent = false) => {
    if (!data) return false;
    setSaving(true);
    try {
      await api.put(`/api/reviews/daily/${day}`, {
        market_observation: data.market_observation,
        decision_review: data.decision_review,
        mistakes: data.mistakes,
        scores: data.scores,
        trade_scores: data.trade_scores ?? {},
        next_market_forecast: data.next_market_forecast,
        next_watchlist: data.next_watchlist,
        next_position_plan: data.next_position_plan,
        next_risk_plan: data.next_risk_plan,
        next_position_rehearsal: data.next_position_rehearsal ?? [],
        rehearsal_ai_analysis: data.rehearsal_ai_analysis ?? '',
      });
      setSavedSnapshot(journalSaveSnapshot(data));
      if (silent) setAutoSavedAt(Date.now());
      else toast('复盘已保存');
      return true;
    } catch (e) { toast(String(e)); return false; } finally { setSaving(false); }
  };

  useAutosave(
    !!data && dirty && !saving && scoringId === null && !reviewing && !rehearsalReviewing,
    async () => { await save(true); },
    [currentSnapshot],
  );

  const focusTrade = (id: number) => {
    setFocusedTradeId(id);
    setFocusedGroupId(null);
  };

  const focusGroup = (groupId: string) => {
    setFocusedGroupId(groupId);
    setFocusedTradeId(null);
  };

  const toggleSelect = (id: number) => {
    setSelectedTradeIds(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]));
  };

  const toggleGroupSelect = (group: TGroup) => {
    const allSelected = group.trade_ids.every(id => selectedTradeIds.includes(id));
    if (allSelected) {
      setSelectedTradeIds(prev => prev.filter(id => !group.trade_ids.includes(id)));
    } else {
      setSelectedTradeIds(prev => [...new Set([...prev, ...group.trade_ids])]);
    }
  };

  const aiScoreAll = async () => {
    if (!data?.trades.length) { toast('当日无交易，无法打分'); return; }
    if (scoreLockRef.current) return;
    scoreLockRef.current = true;
    setScoringId('all');
    setBusy(true, 'AI 交易分析中…');
    try {
      await saveTextsForAi();
      const unscored = data.trades.filter(t => !tradeHasAnalysis(data.trade_scores?.[String(t.id)] ?? {}));
      const targets = unscored.length > 0 ? unscored : data.trades;
      focusTrade(targets[0]?.id ?? data.trades[0].id);
      const result = await api.post<ScoreApiResult>(
        `/api/reviews/daily/${day}/ai-score/batch`,
        { trade_ids: targets.map(t => t.id) },
      );
      if (result.merged_count === 0) {
        toast('AI 未返回有效评分，请重试');
        return;
      }
      await persistAndRefresh(
        `已完成 ${result.merged_count ?? targets.length} 笔交易 AI 分析，已自动保存`,
        { tradeId: targets[0]?.id ?? null },
        result,
      );
    } catch (e) { toast(String(e)); } finally {
      scoreLockRef.current = false;
      setScoringId(null);
      setBusy(false);
    }
  };

  const aiScoreOne = async (tradeId: number) => {
    if (scoreLockRef.current || scoringId !== null) return;
    scoreLockRef.current = true;
    setScoringId(tradeId);
    focusTrade(tradeId);
    setBusy(true, 'AI 分析此笔交易…');
    try {
      await saveTextsForAi();
      const result = await api.post<ScoreApiResult>(`/api/reviews/daily/${day}/ai-score/${tradeId}`);
      if (result.merged_count === 0) {
        toast('AI 未返回有效评分，请重试');
        return;
      }
      await persistAndRefresh('此笔交易 AI 分析完成，已自动保存', { tradeId }, result);
    } catch (e) { toast(String(e)); } finally {
      scoreLockRef.current = false;
      setScoringId(null);
      setBusy(false);
    }
  };

  const aiScoreSelected = async () => {
    if (!data || selectedTradeIds.length === 0) { toast('请先勾选要分析的交易'); return; }
    if (scoreLockRef.current || scoringId !== null) return;
    scoreLockRef.current = true;
    setScoringId('batch');
    focusTrade(selectedTradeIds[0]);
    setBusy(true, 'AI 批量分析中…');
    try {
      await saveTextsForAi();
      const result = await api.post<ScoreApiResult>(
        `/api/reviews/daily/${day}/ai-score/batch`,
        { trade_ids: selectedTradeIds },
      );
      if (result.merged_count === 0) {
        toast('AI 未返回有效评分，请重试');
        return;
      }
      await persistAndRefresh(
        `已分析选中的 ${result.merged_count ?? selectedTradeIds.length} 笔交易，已自动保存`,
        { tradeId: selectedTradeIds[0] },
        result,
      );
    } catch (e) { toast(String(e)); } finally {
      scoreLockRef.current = false;
      setScoringId(null);
      setBusy(false);
    }
  };

  const aiScoreTGroup = async (group: TGroup) => {
    if (scoreLockRef.current || scoringId !== null) return;
    scoreLockRef.current = true;
    setScoringId('t-group');
    focusGroup(group.id);
    setBusy(true, 'AI 做T 分析中…');
    try {
      await saveTextsForAi();
      const result = await api.post<ScoreApiResult>(`/api/reviews/daily/${day}/ai-score/t-group`, {
        code: group.code,
        trade_ids: group.trade_ids,
      });
      await persistAndRefresh(`「${group.name}」做T 分析完成，已自动保存`, { groupId: group.id }, result);
    } catch (e) { toast(String(e)); } finally {
      scoreLockRef.current = false;
      setScoringId(null);
      setBusy(false);
    }
  };

  const aiReview = async () => {
    await save(true);
    setReviewing(true);
    setBusy(true, 'AI 复盘生成中…');
    try {
      const result = await api.post<Partial<DailyReview>>(`/api/reviews/daily/${day}/ai-review`);
      patch({
        market_observation: result.market_observation ?? data?.market_observation ?? '',
        decision_review: result.decision_review ?? data?.decision_review ?? '',
        mistakes: result.mistakes ?? data?.mistakes ?? '',
        next_market_forecast: result.next_market_forecast ?? data?.next_market_forecast ?? '',
        next_position_plan: result.next_position_plan ?? data?.next_position_plan ?? '',
        next_risk_plan: result.next_risk_plan ?? data?.next_risk_plan ?? '',
      });
      toast('AI 复盘草稿已生成，请核对后保存');
    } catch (e) { toast(String(e)); } finally {
      setReviewing(false);
      setBusy(false);
    }
  };

  const aiRehearsal = async () => {
    if (!data || !(data.next_position_rehearsal?.length)) { toast('请先填写明日操作预演'); return; }
    setRehearsalReviewing(true);
    setBusy(true, 'AI 预演分析中…');
    try {
      const result = await api.post<{ rehearsal_ai_analysis: string }>(`/api/reviews/daily/${day}/ai-rehearsal`, {
        next_position_rehearsal: data.next_position_rehearsal ?? [],
        next_watchlist: data.next_watchlist ?? [],
        next_market_forecast: data.next_market_forecast ?? '',
        next_position_plan: data.next_position_plan ?? '',
        next_risk_plan: data.next_risk_plan ?? '',
        market_observation: data.market_observation ?? '',
        decision_review: data.decision_review ?? '',
        mistakes: data.mistakes ?? '',
        ai_summary: data.ai_summary ?? '',
      });
      patch({ rehearsal_ai_analysis: result.rehearsal_ai_analysis ?? '' });
      toast('AI 预演分析已生成');
    } catch (e) { toast(String(e)); } finally {
      setRehearsalReviewing(false);
      setBusy(false);
    }
  };

  const handlePrint = () => {
    if (!data) return;
    printDailyReview(username, day, msg => toast(msg));
  };

  const setTradeFinalScore = (tradeId: number, dim: string, value: number) => {
    if (!data) return;
    const key = String(tradeId);
    const tradeEntry = { ...(data.trade_scores?.[key] ?? {}) };
    const dimEntry = { ...(tradeDimEntry(tradeEntry, dim) ?? {}) };
    dimEntry.final = dimEntry.final === value ? null : value;
    tradeEntry[dim] = dimEntry;
    patch({ trade_scores: { ...data.trade_scores, [key]: tradeEntry } });
  };

  const setDailyFinalScore = (dim: string, value: number) => {
    if (!data) return;
    const entry = { ...(data.scores[dim] ?? {}) };
    entry.final = entry.final === value ? null : value;
    patch({ scores: { ...data.scores, [dim]: entry } });
  };

  const uploadImage = async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    try {
      await api.post(`/api/reviews/daily/${day}/images`, fd);
      load(day);
    } catch (e) { toast(String(e)); }
    if (imgRef.current) imgRef.current.value = '';
  };

  const watchlist = data?.next_watchlist ?? [];
  const setWatch = (i: number, p: Partial<WatchItem>) => {
    const next = watchlist.map((w, idx) => (idx === i ? { ...w, ...p } : w));
    patch({ next_watchlist: next });
  };

  const rehearsal = data?.next_position_rehearsal ?? [];
  const setRehearsal = (next: PositionRehearsal[]) => patch({ next_position_rehearsal: next });
  const setRehearsalRow = (i: number, p: Partial<PositionRehearsal>) => {
    setRehearsal(rehearsal.map((r, idx) => (idx === i ? { ...r, ...p } : r)));
  };

  const copyTodayToRehearsal = async () => {
    if (!data) return;
    const all = data.today_positions ?? [];
    const skipped = all.filter(p => !p.code && (p.qty ?? 0) > 0);
    const filtered = all.filter(p => p.code && (p.qty ?? 0) > 0);
    setCopyingRehearsal(true);
    try {
      const base = await Promise.all(
        filtered.map(async (p) => {
          const code = p.code!;
          let close = positionClose(p) ?? undefined;
          if (close == null) {
            close = (await fetchCloseOnDay(code, day)) ?? undefined;
          }
          return {
            code,
            name: p.name ?? '',
            qty: p.qty ?? 0,
            close,
            note: '',
          };
        }),
      );
      setRehearsal(base);
      if (skipped.length > 0) {
        toast(`有 ${skipped.length} 条持仓缺少代码未复制，请先在资金账本编辑补全`);
      } else {
        toast('已从今日持仓复制（收盘价取自行情），可调整数量（0=清仓）');
      }
    } catch (e) {
      toast(String(e));
    } finally {
      setCopyingRehearsal(false);
    }
  };

  const pickRehearsalStock = async (i: number, code: string, name: string) => {
    const fromToday = data?.today_positions?.find(p => p.code === code);
    let close = fromToday ? positionClose(fromToday) : null;
    setRehearsalRow(i, { code, name, close: close ?? undefined });
    if (close != null) return;
    setRehearsalCloseLoading(i);
    try {
      close = await fetchCloseOnDay(code, day);
      if (close != null) setRehearsalRow(i, { close });
    } finally {
      setRehearsalCloseLoading(null);
    }
  };

  const rehearsalStats = useMemo(() => {
    if (!data) return null;
    const baselineCash = data.rehearsal_baseline?.cash
      ?? data.snapshot?.available_cash
      ?? 0;
    const todayByCode = new Map<string, number>();
    const closeByCode = new Map<string, number>();
    for (const p of data.today_positions ?? []) {
      if (!p.code) continue;
      todayByCode.set(p.code, p.qty ?? 0);
      const c = positionClose(p);
      if (c != null) closeByCode.set(p.code, c);
    }
    let cashDelta = 0;
    let projectedMv = 0;
    const missingCodes: string[] = [];
    for (const r of rehearsal) {
      if (!r.code) continue;
      const close = r.close ?? closeByCode.get(r.code) ?? null;
      if (close == null) {
        missingCodes.push(r.code);
        continue;
      }
      const todayQty = todayByCode.get(r.code) ?? 0;
      cashDelta += (todayQty - r.qty) * close;
      projectedMv += r.qty * close;
    }
    const remainingCash = baselineCash + cashDelta;
    return {
      baselineCash,
      remainingCash,
      projectedMv,
      projectedTotal: remainingCash + projectedMv,
      missingCodes,
      todayByCode,
      closeByCode,
    };
  }, [data, rehearsal]);

  const setRehearsalQty = useCallback((i: number, newQty: number) => {
    if (!data) return;
    const row = rehearsal[i];
    const clamped = Math.max(0, newQty);
    if (clamped > row.qty && rehearsalStats) {
      const close = row.close
        ?? rehearsalStats.closeByCode.get(row.code)
        ?? positionClose(data.today_positions?.find(p => p.code === row.code) ?? {})
        ?? null;
      if (close != null) {
        const extraCost = (clamped - row.qty) * close;
        const newRemaining = rehearsalStats.remainingCash - extraCost;
        if (newRemaining < 0) {
          toast(`剩余现金不足 ¥${fmtMoney(-newRemaining)}，无法加至 ${clamped} 股`);
          return;
        }
      }
    }
    setRehearsalRow(i, { qty: clamped });
  }, [data, rehearsal, rehearsalStats, setRehearsalRow, toast]);

  const renderTradeCard = (t: DayTrade, nested = false) => {
    if (!data) return null;
    const ts = data.trade_scores?.[String(t.id)] ?? {};
    const hasScores = tradeHasAnalysis(ts);
    const isActive = focusedTradeId === t.id && !focusedGroupId;
    const checked = selectedTradeIds.includes(t.id);
    return (
      <div
        className={`trade-score-card${isActive ? ' active' : ''}${nested ? ' nested' : ''}`}
        key={t.id}
        onClick={() => focusTrade(t.id)}
        role="button"
        tabIndex={0}
        onKeyDown={e => { if (e.key === 'Enter') focusTrade(t.id); }}
      >
        <div className="trade-score-head">
          <label className="trade-select-check no-print" onClick={e => e.stopPropagation()}>
            <input type="checkbox" checked={checked} onChange={() => toggleSelect(t.id)} />
          </label>
          <div className="trade-score-meta">
            <SideTag side={t.side} />
            <span className="trade-score-name">{t.name}</span>
            <span className="muted mono">{t.code}</span>
            {hasScores && <span className="tag gold" style={{ fontSize: 11 }}>已分析</span>}
          </div>
          <span className="trade-score-numbers mono muted">
            {t.price} × {t.qty} · 费用 {t.fees}
          </span>
        </div>
      </div>
    );
  };

  const tGroups = data?.t_groups ?? [];
  const sections = data ? organizeSections(data.trades, tGroups) : [];

  return (
    <div className="fade-in journal-page">
      <div className="page-head no-print">
        <div>
          <h2 className="page-title">每日复盘</h2>
          <div className="page-sub">复盘是交易者的第二战场</div>
        </div>
        <div className="row">
          <DateInput value={day} onChange={requestDayChange} />
          {(dirty || saving || autoSavedAt) && (
            <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
              {saving ? '保存中…' : dirty ? '有未保存修改' : `已自动保存 ${new Date(autoSavedAt!).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`}
            </span>
          )}
          <button onClick={handlePrint} disabled={!data}>打印</button>
          <button onClick={aiReview} disabled={reviewing}>{reviewing ? 'AI 复盘中…' : '✦ AI 复盘'}</button>
          <button className="primary" onClick={() => save()} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </div>

      {loading && !data && (
        <div className="card"><Empty text="加载中…" /></div>
      )}

      {data && data.review_date === day && (
        <>
          <div className="screen-only">
          <div className="grid grid-2">
            <div className="card">
              <div className="page-head" style={{ marginBottom: 12 }}>
                <h3 className="card-title" style={{ margin: 0 }}>当日交易 · 逐条打分</h3>
                <div className="row" style={{ gap: 8 }}>
                  {selectedTradeIds.length > 0 && (
                    <button className="primary" onClick={() => void aiScoreSelected()}
                      disabled={scoringId !== null}>
                      {scoringId === 'batch' ? '分析中…' : `✦ 分析选中 (${selectedTradeIds.length})`}
                    </button>
                  )}
                  <button className="ghost no-print" onClick={aiScoreAll}
                    disabled={scoringId !== null || data.trades.length === 0}>
                    {scoringId === 'all' ? '分析中…' : '✦ AI 分析全部'}
                  </button>
                </div>
              </div>

              {selectedTradeIds.length > 0 && (
                <div className="row no-print" style={{ marginBottom: 10, gap: 8 }}>
                  <span className="muted" style={{ fontSize: 12 }}>已选 {selectedTradeIds.length} 笔</span>
                  <button className="ghost" style={{ fontSize: 12 }} onClick={() => setSelectedTradeIds([])}>清除选择</button>
                </div>
              )}

              {data.trades.length === 0 ? <Empty text="当日无交易记录" /> : (
                <div className="trade-score-list">
                  {sections.map(section => {
                    if (section.kind === 'single') {
                      return renderTradeCard(section.trade);
                    }
                    const { group } = section;
                    const groupActive = focusedGroupId === group.id;
                    const groupChecked = group.trade_ids.every(id => selectedTradeIds.includes(id));
                    const groupTrades = data.trades.filter(t => group.trade_ids.includes(t.id));
                    const gs = data.trade_scores?.[groupScoreKey(group.code)] ?? {};
                    return (
                      <div key={group.id} className={`trade-t-group${groupActive ? ' active' : ''}`}>
                        <div className="trade-t-group-head" onClick={() => focusGroup(group.id)}
                          role="button" tabIndex={0}
                          onKeyDown={e => { if (e.key === 'Enter') focusGroup(group.id); }}>
                          <label className="trade-select-check no-print" onClick={e => e.stopPropagation()}>
                            <input type="checkbox" checked={groupChecked}
                              onChange={() => toggleGroupSelect(group)} />
                          </label>
                          <span className="tag gold">做T</span>
                          <span className="trade-score-name">{group.name}</span>
                          <span className="muted mono">{group.code}</span>
                          <span className="muted" style={{ fontSize: 12 }}>{groupTrades.length} 笔</span>
                          {tradeHasAnalysis(gs) && <span className="tag" style={{ fontSize: 11 }}>已分析</span>}
                          <button className="ghost no-print" style={{ marginLeft: 'auto', fontSize: 12 }}
                            disabled={scoringId !== null}
                            onClick={e => { e.stopPropagation(); void aiScoreTGroup(group); }}>
                            {scoringId === 't-group' && groupActive ? '分析中…' : 'AI 分析做T'}
                          </button>
                        </div>
                        <div className="trade-t-group-body">
                          {groupTrades.map(t => renderTradeCard(t, true))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {(data.day_rounds?.length ?? 0) > 0 && (
                <div style={{ marginTop: 16 }}>
                  <PeriodRounds
                    rounds={data.day_rounds}
                    title="相关回合"
                    compact
                  />
                  <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                    点击日期可跳转该日复盘 · <Link to="/trades">查看全部回合 →</Link>
                  </div>
                </div>
              )}

              <div className="journal-snapshot-note">
                {data.snapshot ? (
                  <div className="snap-card journal-snap-mini">
                    <div className="snap-card-head">
                      <div>
                        <div className="muted" style={{ fontSize: 12 }}>收盘快照</div>
                        <div className="snap-card-total mono">¥{fmtMoney(data.snapshot.total_assets)}</div>
                      </div>
                      <div className="snap-card-meta" style={{ margin: 0 }}>
                        {data.snapshot.available_cash != null && (
                          <span className="muted mono">现金 ¥{fmtMoney(data.snapshot.available_cash)}</span>
                        )}
                        {data.snapshot.position_value != null && (
                          <span className="muted mono">持仓 ¥{fmtMoney(data.snapshot.position_value)}</span>
                        )}
                      </div>
                    </div>
                    {(data.snapshot.positions?.length ?? 0) > 0 && (
                      <div className="snap-card-positions">
                        {data.snapshot.positions.map((p, i) => (
                          <div className="snap-pos-row" key={`${p.code ?? i}-${i}`}>
                            <span className="mono">{p.code}</span>
                            <span>{p.name ?? '—'}</span>
                            <span className="mono muted">{p.qty != null ? `${p.qty}股` : ''}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <span className="muted">未录入收盘快照（到资金账本补录）</span>
                )}
              </div>
            </div>

            <div className="card">
              <h3 className="card-title">AI 分析详情</h3>
              <p className="muted" style={{ margin: '0 0 12px', fontSize: 12 }}>
                点击左侧卡片切换查看；勾选多笔后点「分析选中」可一次性评价
              </p>
              <AnalysisDetailPanel
                data={data}
                focusedTradeId={focusedTradeId}
                focusedGroupId={focusedGroupId}
                scoringId={scoringId}
                onAnalyzeTrade={id => void aiScoreOne(id)}
                onAnalyzeGroup={g => void aiScoreTGroup(g)}
                onFocusGroup={focusGroup}
                onSetTradeScore={setTradeFinalScore}
              />

              <h3 className="card-title" style={{ marginTop: 20 }}>整日操作概览</h3>
              <p className="muted" style={{ margin: '0 0 12px', fontSize: 12 }}>
                由各笔交易评分汇总，可手动调整
              </p>
              {Object.keys(SCORE_DIMS).some(dim => data.scores[dim]?.ai != null || data.scores[dim]?.comment) ? (
                <ScoreDimRows scores={data.scores} onSetScore={setDailyFinalScore} />
              ) : (
                <Empty text="完成逐条打分后，此处显示 6 维度汇总" />
              )}
              {data.ai_summary && (
                <div className="journal-day-summary">
                  <div className="journal-day-summary-label">整日 AI 总评</div>
                  {data.ai_summary}
                </div>
              )}
            </div>
          </div>

          <div className="card" style={{ marginTop: 18 }} key={day}>
            <h3 className="card-title">复盘正文</h3>
            <label className="field"><span>盘面观察 · 大盘 / 板块 / 市场情绪</span>
              <div className="print-only print-text-body">{data.market_observation || '—'}</div>
              <textarea className="no-print" key={`${day}-market`} value={data.market_observation} onChange={e => patch({ market_observation: e.target.value })} placeholder="今天市场发生了什么？" />
            </label>
            <label className="field"><span>决策复盘 · 每笔操作的理由与对错</span>
              <div className="print-only print-text-body">{data.decision_review || '—'}</div>
              <textarea className="no-print" key={`${day}-decision`} value={data.decision_review} onChange={e => patch({ decision_review: e.target.value })} placeholder="为什么买？为什么卖？现在看对了还是错了？" />
            </label>
            <label className="field"><span>错误与教训</span>
              <div className="print-only print-text-body">{data.mistakes || '—'}</div>
              <textarea className="no-print" key={`${day}-mistakes`} value={data.mistakes} onChange={e => patch({ mistakes: e.target.value })} placeholder="今天犯了什么错？下次如何避免？" />
            </label>

            <div className="row no-print">
              <input ref={imgRef} type="file" accept="image/*" style={{ display: 'none' }}
                onChange={e => e.target.files?.[0] && uploadImage(e.target.files[0])} />
              <button onClick={() => imgRef.current?.click()}>+ 粘贴K线截图</button>
            </div>
            {data.images.length > 0 && (
              <div className="row" style={{ marginTop: 12 }}>
                {data.images.map(url => (
                  <div key={url} style={{ position: 'relative' }}>
                    <img className="img-thumb" src={url} alt="" onClick={() => window.open(url)} />
                    <button className="danger-ghost no-print" style={{ position: 'absolute', top: 2, right: 2 }}
                      onClick={async () => {
                        await api.del(`/api/reviews/daily/${day}/images?url=${encodeURIComponent(url)}`);
                        load(day);
                      }}>×</button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card no-print" style={{ marginTop: 18 }}>
            <div className="page-head" style={{ marginBottom: 12 }}>
              <h3 className="card-title" style={{ margin: 0 }}>明日操作预演</h3>
              <div className="row" style={{ gap: 8 }}>
                <button className="ghost" onClick={() => { void copyTodayToRehearsal(); }} disabled={copyingRehearsal}>
                  {copyingRehearsal ? '拉取行情中…' : '从今日持仓复制'}
                </button>
                <button onClick={aiRehearsal} disabled={rehearsalReviewing || rehearsal.length === 0}>
                  {rehearsalReviewing ? '分析中…' : '✦ AI 预演分析'}
                </button>
              </div>
            </div>
            <p className="muted" style={{ margin: '0 0 12px', fontSize: 12 }}>
              预演明日收盘持仓：按当日收盘价估算资金占用；数量改为 0 表示清仓。
            </p>

            {rehearsalStats && (
              <div className="rehearsal-funds-bar">
                <div className="rehearsal-funds-item">
                  <span className="rehearsal-funds-label">今日可用</span>
                  <span className="mono">¥{fmtMoney(rehearsalStats.baselineCash)}</span>
                </div>
                <div className="rehearsal-funds-item highlight">
                  <span className="rehearsal-funds-label">预演剩余现金</span>
                  <span className={`mono${rehearsalStats.remainingCash < 0 ? ' neg' : ''}`}>
                    ¥{fmtMoney(rehearsalStats.remainingCash)}
                  </span>
                </div>
                <div className="rehearsal-funds-item">
                  <span className="rehearsal-funds-label">预演持仓市值</span>
                  <span className="mono">¥{fmtMoney(rehearsalStats.projectedMv)}</span>
                </div>
                <div className="rehearsal-funds-item">
                  <span className="rehearsal-funds-label">预演总资产</span>
                  <span className="mono">¥{fmtMoney(rehearsalStats.projectedTotal)}</span>
                </div>
              </div>
            )}
            {rehearsalStats && rehearsalStats.remainingCash < 0 && (
              <div className="alert" style={{ marginBottom: 12, padding: '8px 12px', fontSize: 12 }}>
                预演剩余现金为负，请减少买入或增加卖出。
              </div>
            )}
            {rehearsalStats && rehearsalStats.missingCodes.length > 0 && (
              <div className="muted" style={{ marginBottom: 12, fontSize: 12 }}>
                以下标的缺少收盘价，无法计入资金：{rehearsalStats.missingCodes.join('、')}
              </div>
            )}

            {(data.rehearsal_compare?.length ?? 0) > 0 && (
              <div className="rehearsal-compare-block" style={{ marginBottom: 16 }}>
                <div className="card-title" style={{ fontSize: 13, marginBottom: 8 }}>昨日预演 vs 今日实际</div>
                <table>
                  <thead>
                    <tr><th>标的</th><th>预演</th><th>实际</th><th>差异</th><th>状态</th></tr>
                  </thead>
                  <tbody>
                    {data.rehearsal_compare.map(row => (
                      <tr key={row.code}>
                        <td>{row.name} <span className="mono muted">{row.code}</span></td>
                        <td className="mono">{row.planned_qty}</td>
                        <td className="mono">{row.actual_qty}</td>
                        <td className={`mono${row.delta !== 0 ? ' neg' : ''}`}>{row.delta > 0 ? `+${row.delta}` : row.delta}</td>
                        <td><span className="tag">{REHEARSAL_STATUS[row.status] ?? row.status}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {(data.today_positions?.length ?? 0) > 0 && (
              <div style={{ marginBottom: 14 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>今日收盘持仓（基准）</div>
                <div className="snap-card-positions">
                    {(data.today_positions ?? []).map((p, i) => {
                      const c = positionClose(p);
                      return (
                      <div className="snap-pos-row" key={`today-${p.code}-${i}`}>
                        <span className="mono">{p.code}</span>
                        <span>{p.name ?? '—'}</span>
                        <span className="mono muted">{p.qty != null ? `${p.qty}股` : '—'}</span>
                        <span className="mono muted">{c != null ? `@${c.toFixed(3)}` : ''}</span>
                      </div>
                      );
                    })}
                </div>
              </div>
            )}

            {rehearsal.length === 0 ? (
              <Empty text="点击「从今日持仓复制」开始预演明日仓位变化" />
            ) : (
              <div className="rehearsal-edit-list">
                {rehearsal.map((r, i) => {
                  const close = r.close ?? positionClose(r) ?? (r.code
                    ? positionClose(data.today_positions?.find(p => p.code === r.code) ?? {})
                    : null);
                  const mv = close != null ? r.qty * close : null;
                  const todayQty = data.today_positions?.find(p => p.code === r.code)?.qty ?? 0;
                  const deltaCash = close != null ? (todayQty - r.qty) * close : null;
                  return (
                  <div className="row rehearsal-edit-row" key={i} style={{ marginBottom: 8, alignItems: 'flex-end' }}>
                    <StockPicker
                      code={r.code}
                      name={r.name}
                      onSelect={(code, name) => { void pickRehearsalStock(i, code, name); }}
                      style={{ flex: 2, minWidth: 160 }}
                    />
                    <label className="field" style={{ flex: '0 0 128px', margin: 0 }}>
                      <span>预演股数</span>
                      <QtyStepper value={r.qty} onChange={v => setRehearsalQty(i, v)} step={100} />
                    </label>
                    <div className="field" style={{ flex: '0 0 88px', margin: 0 }}>
                      <span>收盘价</span>
                      <div className={`mono${rehearsalCloseLoading === i ? ' muted' : ''}`} style={{ fontSize: 13, padding: '8px 0' }}>
                        {rehearsalCloseLoading === i ? '拉取中…' : close != null ? close.toFixed(3) : '—'}
                      </div>
                    </div>
                    <div className="field" style={{ flex: '0 0 96px', margin: 0 }}>
                      <span>市值</span>
                      <div className="mono muted" style={{ fontSize: 13, padding: '8px 0' }}>
                        {mv != null ? `¥${fmtMoney(mv)}` : '—'}
                      </div>
                    </div>
                    <div className="field" style={{ flex: '0 0 96px', margin: 0 }}>
                      <span>现金变动</span>
                      <div className={`mono${deltaCash != null && deltaCash < 0 ? ' neg' : ''}`} style={{ fontSize: 13, padding: '8px 0' }}>
                        {deltaCash != null ? `${deltaCash >= 0 ? '+' : ''}${fmtMoney(deltaCash)}` : '—'}
                      </div>
                    </div>
                    <input placeholder="备注" style={{ flex: 1, minWidth: 80 }}
                      value={r.note ?? ''} onChange={e => setRehearsalRow(i, { note: e.target.value })} />
                    <button className="danger-ghost" onClick={() => setRehearsal(rehearsal.filter((_, idx) => idx !== i))}>×</button>
                  </div>
                  );
                })}
              </div>
            )}

            <div className="rehearsal-card-footer">
              <button
                className="ghost"
                onClick={() => setRehearsal([...rehearsal, { code: '', name: '', qty: 0, note: '新开仓' }])}
              >
                + 预演新开仓
              </button>
            </div>
          </div>

          {data.rehearsal_ai_analysis?.trim() && (
            <div className="card" style={{ marginTop: 18 }}>
              <h3 className="card-title">明日预演 AI 分析</h3>
              <div className="journal-day-summary" style={{ marginTop: 0 }}>
                {data.rehearsal_ai_analysis}
              </div>
            </div>
          )}

          <div className="card" style={{ marginTop: 18 }}>
            <h3 className="card-title">次日预研</h3>
            <div className="grid grid-2">
              <label className="field"><span>大盘预判</span>
                <textarea value={data.next_market_forecast} onChange={e => patch({ next_market_forecast: e.target.value })} placeholder="明天大盘怎么走？多空条件是什么？" />
              </label>
              <label className="field"><span>仓位计划</span>
                <textarea value={data.next_position_plan} onChange={e => patch({ next_position_plan: e.target.value })} placeholder="明天计划保持几成仓？" />
              </label>
            </div>
            <label className="field"><span>风险预案</span>
              <textarea value={data.next_risk_plan} onChange={e => patch({ next_risk_plan: e.target.value })} placeholder="跌破哪里止损？突发利空怎么办？" />
            </label>

            <div className="card-title" style={{ marginTop: 8 }}>
              <span>关注标的</span>
              <button className="ghost no-print" onClick={() => patch({ next_watchlist: [...watchlist, { code: '', name: '', condition: '', action: '' }] })}>+ 添加</button>
            </div>
            {watchlist.length === 0 ? <Empty text="明天重点盯谁？触发什么条件做什么？" /> : watchlist.map((w, i) => (
              <div className="row" key={i} style={{ marginBottom: 8 }}>
                <StockPicker
                  code={w.code}
                  name={w.name}
                  onSelect={(code, name) => setWatch(i, { code, name })}
                  style={{ flex: 2, minWidth: 180 }}
                />
                <input placeholder="触发条件（如：放量站上20日线）" style={{ flex: 2, minWidth: 140 }} value={w.condition} onChange={e => setWatch(i, { condition: e.target.value })} />
                <input placeholder="动作（如：3成仓介入）" style={{ flex: 1, minWidth: 100 }} value={w.action} onChange={e => setWatch(i, { action: e.target.value })} />
                <button className="danger-ghost no-print" onClick={() => patch({ next_watchlist: watchlist.filter((_, idx) => idx !== i) })}>×</button>
              </div>
            ))}
          </div>
          </div>

          <div className="print-only">
            <JournalPrintReport data={data} day={day} username={username} />
          </div>
        </>
      )}
    </div>
  );
}
