export function ResearchTradeDownloads({ kind, id, detailKey }: { kind: 'single' | 'portfolio' | 'legacy'; id: string; detailKey?: string }) {
  const root = kind === 'single' ? '/backtests' : kind === 'portfolio' ? '/research/portfolios' : '/research/legacy-reports'
  const query = detailKey ? `?detail_key=${encodeURIComponent(detailKey)}` : ''
  return <span className="toolbar" style={{ flexWrap: 'wrap' }}>{(['csv', 'html'] as const).map(format => <a key={format} className="button secondary" href={`/api/v1${root}/${encodeURIComponent(id)}/trades.${format}${query}`} download>{kind === 'legacy' ? '导出原成交' : '导出逐笔成交'} {format.toUpperCase()}</a>)}</span>
}

export function PlateauTableDownload({ id, legacy = false }: { id: string; legacy?: boolean }) {
  return <a className="button secondary" href={`/api/v1/research/${legacy ? 'legacy-reports' : 'plateaus'}/${encodeURIComponent(id)}/${legacy ? 'plateau.xlsx' : 'export.xlsx'}`} download>{legacy ? '导出原平原全点 Excel' : '导出平原全点 Excel'}</a>
}
