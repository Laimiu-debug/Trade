const paths: Record<string, string> = {
  overview: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  trades: 'M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4',
  flows: 'M3 5h17v15H3zM3 5V3h14M15 11h6v5h-6z',
  snapshots: 'm12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',
  review: 'M12 4H4v16h16v-8M16 3l5 5-9 9H7v-5l9-9Z',
  period: 'M4 5h16v16H4zM8 3v4M16 3v4M4 10h16M8 14h3M8 17h6',
  statistics: 'M4 20h17M7 16V9M12 16V4M17 16v-5',
  market: 'M3 17l5-6 5 3 8-10M16 4h5v5M3 21h18',
  research: 'M9 3h6M10 3v7L4 20h16l-6-10V3M8 15h8',
  signals: 'M12 3v3M12 18v3M3 12h3M18 12h3M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
  backtest: 'M4 5h16v14H4zM8 9l3 3-3 3M13 15h3',
  news: 'M4 4h16v17H4zM8 8h8M8 12h3M14 12h2M8 16h8',
  events: 'M3 4h18v5H3zM5 9v12h14V9M9 14h6',
  valuation: 'M3 20h18M6 16l4-7 4 3 4-8M17 4h4v4',
  insights: 'M9 18h6M10 21h4M8 14a6 6 0 1 1 8 0l-1 2H9l-1-2Z',
  ai: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
  tasks: 'M9 5h12M9 12h12M9 19h12M3 5l1 1 2-2M3 12l1 1 2-2M3 19l1 1 2-2',
  settings: 'M4 7h16M4 17h16M8 4v6M16 14v6',
  share: 'M16 8l-8 4 8 4M4 12a2 2 0 1 0 4 0 2 2 0 0 0-4 0M16 6a2 2 0 1 0 4 0 2 2 0 0 0-4 0M16 18a2 2 0 1 0 4 0 2 2 0 0 0-4 0',
  simulation: 'M4 4h16v16H4zM8 8l8 4-8 4V8Z',
}

export function WorkspaceIcon({ name }: { name: string }) {
  return <svg className="workspace-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name] || paths.overview} /></svg>
}
