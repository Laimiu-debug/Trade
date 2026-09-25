const BASE = '/journal-app';

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(BASE + url, {
      method,
      headers: body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: body instanceof FormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error('无法连接本地服务，请确认 TradingMS 已启动（托盘图标在运行）');
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail ?? JSON.stringify(data);
    } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body),
  put: <T>(url: string, body?: unknown) => request<T>('PUT', url, body),
  del: <T>(url: string) => request<T>('DELETE', url),
};

// ---------- 类型 ----------

export interface NavState {
  nav: number;
  assets: number;
  shares: number;
  day: string | null;
  lit_levels: number[];
  lit_count: number;
  next_level: number | null;
  next_threshold: number | null;
  next_gap_pct: number | null;
  next_assets_target?: number | null;
  max_nav: number;
  drawdown_pct: number;
  node_count: number;
  wave_pct: number;
  leg_progress_pct: number;
}

export interface CurvePoint {
  date: string;
  nav: number;
  assets: number;
  drawdown_pct: number;
}

export interface TradeRow {
  id: number;
  trade_date: string;
  code: string;
  name: string;
  side: 'buy' | 'sell';
  price: number;
  qty: number;
  fees: number;
  amount: number;
  note: string;
  source: string;
}

export interface RoundRow {
  code: string;
  name: string;
  start_date: string;
  end_date: string | null;
  status: 'closed' | 'open' | 'anomaly';
  position: number;
  buy_amount: number;
  sell_amount: number;
  fees: number;
  pnl: number | null;
  pnl_pct: number | null;
  trades: { id: number; date: string; side: string; price: number; qty: number; fees: number }[];
  review_snippet?: string;
  review_summary?: string;
  review_dates?: string[];
  trade_count?: number;
}

/** 回合与复盘串联摘要（日/周/月 API 返回） */
export interface LinkedRoundRow {
  code: string;
  name: string;
  start_date: string;
  end_date: string | null;
  status: 'closed' | 'open' | 'anomaly';
  position?: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  buy_amount: number;
  sell_amount?: number;
  fees?: number;
  trade_count: number;
  review_snippet: string;
  review_summary?: string;
  review_dates: string[];
  has_trade_today?: boolean;
  closed_today?: boolean;
}

export interface RoundStats {
  total_rounds: number;
  open_rounds: number;
  win_count: number;
  lose_count: number;
  win_rate: number | null;
  avg_win: number;
  avg_loss: number;
  profit_loss_ratio: number | null;
  max_win_streak: number;
  max_lose_streak: number;
  total_pnl: number;
}

export interface ScoreEntry {
  ai?: number | null;
  final?: number | null;
  comment?: string;
}

/** 单笔交易的 AI 评分包，含各维度分与整体总评 */
export interface TradeScoreBundle {
  _summary?: { comment?: string };
  _meta?: { kind?: string; code?: string; trade_ids?: number[] };
  [dim: string]: ScoreEntry | { comment?: string } | { kind?: string; code?: string; trade_ids?: number[] } | undefined;
}

export interface TGroup {
  id: string;
  code: string;
  name: string;
  kind: 't';
  trade_ids: number[];
}

export interface WatchItem {
  code: string;
  name: string;
  condition: string;
  action: string;
}

export interface SnapshotPosition {
  code?: string;
  name?: string;
  qty?: number;
  price?: number;
  close?: number;
  market_value?: number;
}

export interface SnapshotInfo {
  total_assets: number;
  available_cash?: number | null;
  position_value?: number | null;
  positions: SnapshotPosition[];
}

export interface RehearsalBaseline {
  cash: number;
  total_assets: number;
}

export interface PositionRehearsal {
  code: string;
  name: string;
  qty: number;
  close?: number;
  note?: string;
}

export interface RehearsalCompareRow {
  code: string;
  name: string;
  planned_qty: number;
  actual_qty: number;
  delta: number;
  status: string;
}

export interface DayDetail {
  date: string;
  nav: number | null;
  assets: number | null;
  drawdown_pct: number | null;
  trades: { id: number; code: string; name: string; side: string; price: number; qty: number; fees: number }[];
  positions: SnapshotPosition[];
  snapshot: { total_assets?: number; available_cash?: number | null; position_value?: number | null; estimated?: boolean } | null;
  has_review: boolean;
  ai_summary: string;
}

export interface DailyReview {
  review_date: string;
  market_observation: string;
  decision_review: string;
  mistakes: string;
  images: string[];
  scores: Record<string, ScoreEntry>;
  trade_scores: Record<string, TradeScoreBundle>;
  ai_summary: string;
  next_market_forecast: string;
  next_watchlist: WatchItem[];
  next_position_plan: string;
  next_risk_plan: string;
  next_position_rehearsal: PositionRehearsal[];
  today_positions: SnapshotPosition[];
  rehearsal_baseline: RehearsalBaseline;
  prev_rehearsal: PositionRehearsal[];
  rehearsal_compare: RehearsalCompareRow[];
  rehearsal_ai_analysis: string;
  trades: { id: number; code: string; name: string; side: string; price: number; qty: number; fees: number }[];
  t_groups: TGroup[];
  snapshot: SnapshotInfo | null;
  day_rounds: LinkedRoundRow[];
}

export interface FlashCard {
  id: number;
  content: string;
  tags: string;
  created_at: string;
}

export interface NodeInfo {
  level: number;
  threshold: number;
  assets_equiv: number | null;
  lit: boolean;
}

export interface NodeTiming {
  level: number;
  first_lit: string;
  days_taken: number | null;
}

export interface Overview {
  state: NavState;
  curve: CurvePoint[];
  max_drawdown_pct: number;
  weekly_returns: { period: string; return_pct: number; end_nav: number }[];
  monthly_returns: { period: string; return_pct: number; end_nav: number }[];
  round_stats: RoundStats;
  node_timing: NodeTiming[];
  missing_reviews: string[];
  missing_snapshots: string[];
}

export const SCORE_DIMS: Record<string, string> = {
  position: '仓位控制',
  drawdown: '回撤控制',
  discipline: '计划执行力',
  entry: '买点质量',
  exit: '卖点质量',
  emotion: '情绪管理',
};

export const TRADE_SCORE_DIMS: Record<string, string> = {
  timing: '时机质量',
  discipline: '计划执行力',
  emotion: '情绪管理',
};

export const tradeTimingLabel = (side: string) => (side === 'sell' ? '卖点质量' : '买点质量');

export const fmtMoney = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : v.toLocaleString('zh-CN', { maximumFractionDigits: 2 });

export const fmtPct = (v: number | null | undefined, signed = true) =>
  v === null || v === undefined ? '—' : `${signed && v > 0 ? '+' : ''}${v.toFixed(2)}%`;

export const today = () => new Date().toISOString().slice(0, 10);
