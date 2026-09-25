import type { ReactNode } from 'react';
import { SCORE_DIMS, type ScoreEntry } from './api';

function normalizeScore(value: unknown): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function scoreForDots(entry: ScoreEntry): number {
  const ai = normalizeScore(entry.ai);
  const final = normalizeScore(entry.final);
  if (ai != null && final != null && final !== ai) return final;
  return ai ?? final ?? 0;
}

export function PrintDocument({ children }: { children: ReactNode }) {
  return (
    <article className="print-document">
      {children}
      <footer className="print-doc-footer">
        <span>Trading MS · 交易者管理系统</span>
        <span>仅供个人复盘存档</span>
      </footer>
    </article>
  );
}

export function PrintDocHeader({
  username,
  title,
  subtitle,
  badge,
}: {
  username: string;
  title: string;
  subtitle: string;
  badge?: string;
}) {
  const printedAt = new Date().toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  const author = username.trim() || '交易者';
  return (
    <header className="print-doc-header">
      <div className="print-doc-brand-row">
        <span className="print-doc-mark">Trading MS</span>
        <span className="print-doc-printed">打印于 {printedAt}</span>
      </div>
      <h1 className="print-doc-title">{title}</h1>
      <div className="print-doc-meta-row">
        <span className="print-doc-meta">{author} · {subtitle}</span>
        {badge && <span className="print-doc-badge">{badge}</span>}
      </div>
    </header>
  );
}

export function PrintSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="print-section-card">
      <h2 className="print-section-title">{title}</h2>
      <div className="print-section-body">{children}</div>
    </section>
  );
}

export function PrintTextBlock({
  label,
  text,
  placeholder = '（未填写）',
  plain = false,
}: {
  label: string;
  text: string;
  placeholder?: string;
  plain?: boolean;
}) {
  const empty = !text?.trim();
  return (
    <div className={`print-text-block${empty ? ' is-empty' : ''}${plain ? ' print-text-block--plain' : ''}`}>
      <div className="print-text-label">{label}</div>
      <div className="print-text-body">{empty ? placeholder : text}</div>
    </div>
  );
}

export function PrintStatGrid({ children }: { children: ReactNode }) {
  return <div className="print-stat-grid">{children}</div>;
}

export function PrintStatCard({
  label,
  value,
  tone,
  note,
}: {
  label: string;
  value: string;
  tone?: 'pos' | 'neg';
  note?: string;
}) {
  return (
    <div className="print-stat-card">
      <div className="print-stat-label">{label}</div>
      <div className={`print-stat-value${tone ? ` ${tone}` : ''}`}>{value}</div>
      {note && <div className="print-stat-note">{note}</div>}
    </div>
  );
}

export function PrintScoreDimRows({
  scores,
  side,
}: {
  scores: Record<string, ScoreEntry | undefined>;
  side?: string;
}) {
  const rows = Object.entries(SCORE_DIMS).map(([dim, label]) => {
    const entry = scores[dim] ?? {};
    const dimLabel = dim === 'entry' && side === 'sell' ? '买点质量'
      : dim === 'exit' && side === 'buy' ? '卖点质量'
      : label;
    const inactive = (dim === 'entry' && side === 'sell') || (dim === 'exit' && side === 'buy');
    if (inactive && entry.ai == null && entry.final == null && !entry.comment) return null;
    const score = scoreForDots(entry);
    const hasScore = score > 0;
    return (
      <div className="print-score-row" key={dim}>
        <span className="print-score-dim">{dimLabel}</span>
        <div className="print-score-bar-wrap">
          <div className="print-score-bar">
            <div
              className="print-score-fill"
              style={{ width: hasScore ? `${score * 10}%` : '0%' }}
            />
          </div>
          <span className="print-score-num">{hasScore ? `${score}/10` : '—'}</span>
        </div>
        {entry.comment && <div className="print-score-comment">{entry.comment}</div>}
      </div>
    );
  }).filter(Boolean);

  if (!rows.length) return <p className="print-muted">暂无评分</p>;
  return <div className="print-score-grid">{rows}</div>;
}

export function PrintTradeSide({ side }: { side: string }) {
  const buy = side === 'buy';
  return (
    <span className={`print-trade-side${buy ? ' buy' : ' sell'}`}>
      {buy ? '买' : '卖'}
    </span>
  );
}

export function PrintSummaryBox({ label, text }: { label: string; text: string }) {
  if (!text?.trim()) return null;
  return (
    <div className="print-summary-box">
      <div className="print-summary-label">{label}</div>
      <div className="print-summary-body">{text}</div>
    </div>
  );
}
