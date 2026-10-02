import { useRef, useState } from 'react'

type Stock = { ts_code: string; symbol: string; name: string; industry: string; cnspell: string; exchange: string; list_status: string }
export type StockHit = Stock & { prefixed_symbol: string; match: '代码' | '名称' | '拼音' }
let cache: Stock[] | null = null
let pending: Promise<Stock[]> | null = null

async function library(): Promise<Stock[]> {
  if (cache) return cache
  if (!pending) pending = fetch('/data/stock-database.slim.json').then(async response => {
    if (!response.ok) throw new Error('本地股票库不可用')
    const rows = await response.json() as Stock[]
    cache = rows.filter(row => row.list_status === 'L')
    return cache
  }).finally(() => { pending = null })
  return pending
}

function prefixed(row: Stock): string {
  const prefix = ({ SSE: 'sh', SHSE: 'sh', SZSE: 'sz', BSE: 'bj' } as Record<string, string>)[row.exchange]
  return prefix ? prefix + row.symbol : row.symbol
}

export async function searchStocks(raw: string, limit = 12): Promise<StockHit[]> {
  const query = raw.trim().toLowerCase()
  if (!query) return []
  const rows = await library()
  const hits: Array<StockHit & { rank: number }> = []
  for (const row of rows) {
    const code = prefixed(row)
    const ts = row.ts_code.toLowerCase()
    const name = row.name.toLowerCase()
    const pinyin = row.cnspell.toLowerCase()
    let rank = 99
    let match: StockHit['match'] = '代码'
    if (row.symbol === query || code === query || ts === query) rank = 0
    else if (name === query) { rank = 1; match = '名称' }
    else if (row.symbol.startsWith(query) || code.startsWith(query) || ts.startsWith(query)) rank = 2
    else if (pinyin.startsWith(query)) { rank = 3; match = '拼音' }
    else if (name.includes(query)) { rank = 4; match = '名称' }
    else if (pinyin.includes(query)) { rank = 5; match = '拼音' }
    if (rank < 99) hits.push({ ...row, prefixed_symbol: code, match, rank })
  }
  hits.sort((a, b) => a.rank - b.rank || a.ts_code.localeCompare(b.ts_code))
  return hits.slice(0, limit)
}

export function StockSearch({ onSelect }: { onSelect: (row: StockHit) => void }) {
  const request = useRef(0)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<StockHit[]>([])
  const [error, setError] = useState('')
  async function search(value: string) {
    const current = ++request.current
    setQuery(value); setError('')
    try { const rows = await searchStocks(value); if (current === request.current) setHits(rows) }
    catch (err) { if (current === request.current) { setHits([]); setError(err instanceof Error ? err.message : '股票库搜索失败') } }
  }
  return <div className="market-detail"><label className="field">本地证券库搜索（代码 / 名称 / 拼音）<input value={query} onChange={event => search(event.target.value)} placeholder="如 600000、sh600000、股票名称或拼音" /></label>{error && <p className="alert error">{error}</p>}{hits.length > 0 && <div className="table-wrap"><table><thead><tr><th>代码</th><th>名称</th><th>市场</th><th>行业</th><th>匹配</th><th>选择</th></tr></thead><tbody>{hits.map(row => <tr key={row.ts_code}><td>{row.prefixed_symbol}</td><td>{row.name}</td><td>{row.exchange}</td><td>{row.industry}</td><td>{row.match}</td><td><button className="link-button" onClick={() => { onSelect(row); setQuery(`${row.name} (${row.prefixed_symbol})`); setHits([]) }}>选用</button></td></tr>)}</tbody></table></div>}<p className="muted">旧版离线股票库可能过时；未收录代码可手动输入。</p></div>
}
