/** 导出 PDF 用内联样式（与 styles.css @media print 一致） */
export const PRINT_CSS = `
:root {
  --bg: #f5f0e6;
  --surface: #ffffff;
  --surface-2: #fbf7ef;
  --surface-3: #f1e9da;
  --border: #e6dcc6;
  --border-strong: #d2c4a3;
  --text: #3a322a;
  --text-2: #7a6f5e;
  --text-3: #a89c86;
  --gold: #e8a87c;
  --gold-bright: #d98a52;
  --gold-dim: rgba(232, 168, 124, 0.22);
  --up: #ff6b6b;
  --down: #4ecdc4;
  --font-display: Georgia, "Times New Roman", serif;
  --font-mono: "Consolas", "Courier New", monospace;
  --radius-sm: 6px;
  --radius-md: 10px;
}
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0;
  padding: 16mm;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 12px;
  line-height: 1.5;
}
.print-doc-header {
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--border-strong);
}
.print-doc-header h1 {
  margin: 0 0 6px;
  font-family: var(--font-display);
  font-size: 20px;
  color: var(--gold-bright);
  letter-spacing: 0.04em;
}
.print-doc-header .print-doc-meta {
  color: var(--text-2);
  font-size: 13px;
}
.print-section { margin-bottom: 18px; break-inside: avoid; }
.print-section h4 {
  margin: 0 0 10px;
  color: var(--gold-bright);
  font-family: var(--font-display);
  font-size: 14px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 6px;
}
.print-trade-block {
  margin-bottom: 14px;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  break-inside: avoid;
}
.print-trade-head { margin-bottom: 8px; }
.trade-score-summary {
  background: var(--gold-dim);
  color: var(--text);
  border: 1px solid var(--border);
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  margin-bottom: 8px;
  white-space: pre-wrap;
}
.journal-day-summary {
  background: var(--gold-dim);
  color: var(--text-2);
  border: 1px solid var(--border);
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  margin-top: 10px;
  white-space: pre-wrap;
}
.journal-day-summary-label { font-weight: 600; color: var(--gold-bright); margin-bottom: 6px; }
.print-text-block { margin-bottom: 10px; }
.print-text-label {
  font-weight: 600;
  color: var(--gold-bright);
  font-size: 12px;
  margin-top: 8px;
}
.print-text-body {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.65;
  padding: 8px 0 12px;
  color: var(--text);
}
.print-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  margin-top: 6px;
}
.print-table th, .print-table td {
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
}
.print-table th {
  background: var(--surface-3);
  color: var(--text-2);
}
.mono { font-family: var(--font-mono); }
.muted { color: var(--text-2); }
.tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  border: 1px solid var(--border-strong);
  background: var(--surface-2);
  color: var(--text-2);
  margin-left: 4px;
}
.tag.gold {
  color: var(--gold-bright);
  background: var(--gold-dim);
  border-color: var(--gold);
}
.score-row { margin: 4px 0; }
.score-dim { display: inline-block; width: 72px; color: var(--text-2); font-size: 11px; }
.score-print-value { color: var(--gold-bright); font-weight: 600; margin-left: 8px; }
.score-comment-wrap { display: block; color: var(--text-2); font-size: 11px; margin: 2px 0 6px 72px; }
.stat-row { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 16px; }
.stat-item { min-width: 100px; }
.stat-item .stat-label { font-size: 11px; color: var(--text-3); }
.stat-item .stat-value { font-size: 16px; font-weight: 600; font-family: var(--font-mono); }
.stat-item .stat-value.pos { color: var(--up); }
.stat-item .stat-value.neg { color: var(--down); }
.pos { color: var(--up); }
.neg { color: var(--down); }
.periodic-print-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 600px) { .periodic-print-grid { grid-template-columns: 1fr; } }
`;

export function wrapPrintHtml(title: string, bodyHtml: string): string {
  return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>${title}</title><style>${PRINT_CSS}</style></head><body>${bodyHtml}</body></html>`;
}

export function fmtCnDate(iso: string): string {
  const parts = iso.split('-').map(Number);
  if (parts.length < 3) return iso;
  return `${parts[1]}月${parts[2]}日`;
}

export function buildDailyPdfFilename(username: string, day: string): string {
  const name = username.trim() || '交易者';
  return `${name} Trading MS ${fmtCnDate(day)} 复盘日志.pdf`;
}

export function buildWeeklyPdfFilename(username: string, year: number, week: number): string {
  const name = username.trim() || '交易者';
  return `${name} Trading MS ${year}年第${week}周 周复盘.pdf`;
}

export function buildMonthlyPdfFilename(username: string, year: number, month: number): string {
  const name = username.trim() || '交易者';
  return `${name} Trading MS ${year}年${month}月 月复盘.pdf`;
}
