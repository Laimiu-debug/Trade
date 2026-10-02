export type IntradayPoint = { time: string; open: string | null; high: string | null; low: string | null; close: string; volume: number | string | null; amount: string | null }
export type IntradayData = { id?: string; symbol: string; date: string; provider: string; quality: string; availability_quality: string; as_of_at?: string; volume_unit?: string; price_unit?: string; quality_flags?: string[]; source: { filename?: string; sha256: string; file_mtime_utc?: string; retrieved_at?: string }; points: IntradayPoint[] }

export function IntradayChart({ data }: { data: IntradayData }) {
  const points = data.points
  if (!points.length) return <p className="muted">没有可显示的真实分时记录。</p>
  const online = data.provider === 'eastmoney_online'
  const priceUnit = data.price_unit === 'index_points' ? '点' : '元'
  const volumeUnit = data.volume_unit === 'lots' ? '手' : '来源原单位'
  const prices = points.map(point => Number(point.close))
  const low = Math.min(...prices), high = Math.max(...prices)
  const span = Math.max(high - low, high * 0.002, 0.01)
  const bottom = low - span * 0.1, top = high + span * 0.1
  const x = (index: number) => 24 + index / Math.max(1, points.length - 1) * 852
  const y = (price: number) => 180 - (price - bottom) / (top - bottom) * 150
  const line = prices.map((price, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(price).toFixed(1)}`).join(' ')
  const volume = points.some(point => point.volume === null) ? null : points.reduce((sum, point) => sum + Number(point.volume), 0)
  const amount = points.some(point => point.amount === null) ? null : points.reduce((sum, point) => sum + Math.round(Number(point.amount) * 100), 0) / 100
  return <div className="market-chart"><h3>{data.symbol} · {data.date} {online ? '在线一分钟分时缓存' : '原始一分钟分时'}</h3><p className="muted">{online ? `东方财富原始响应；获取于 ${data.source.retrieved_at}，观察截止 ${data.as_of_at}。显示时间为 Asia/Shanghai；价格 ${priceUnit}，成交量 ${volumeUnit}，成交额元。不复权。` : `实时读取本地 ${data.source.filename}；`}来源 SHA-256 {data.source.sha256.slice(0, 16)}。历史可得时间未知；缺失记录不补造，分时仅供观察。</p>{data.quality_flags?.includes('source_fields_missing') && <p className="muted">来源部分字段缺失（历史开盘价 0 也按缺失处理）；合计在有缺失时保持未知。</p>}{data.quality_flags?.includes('future_minutes_excluded') && <p className="muted">已排除观察截止之后的分时记录。</p>}<svg viewBox="0 0 900 215" role="img" aria-label={`${data.symbol} ${data.date} 一分钟收盘价曲线`} preserveAspectRatio="none"><line x1="24" x2="876" y1="180" y2="180" className="chart-grid" /><path d={line} className="chart-ma ma5" fill="none" strokeWidth="2" /><text x="24" y="205" className="chart-axis">{points[0].time}</text><text x="820" y="205" className="chart-axis">{points.at(-1)?.time}</text></svg><div className="period-summary"><span>记录：{points.length} 分钟</span><span>最低收盘：{low.toFixed(4)} {priceUnit}</span><span>最高收盘：{high.toFixed(4)} {priceUnit}</span><span>成交量合计：{volume === null ? '未知' : volume.toLocaleString('zh-CN', { maximumFractionDigits: 8 })} {volumeUnit}</span><span>成交额合计：{amount === null ? '未知' : amount.toFixed(2)} 元</span></div><div className="table-wrap"><table><thead><tr><th>时间</th><th>开</th><th>高</th><th>低</th><th>收</th><th>量（{volumeUnit}）</th><th>额（元）</th></tr></thead><tbody>{points.map(point => <tr key={point.time}><td>{point.time}</td><td>{point.open ?? '缺失'}</td><td>{point.high ?? '缺失'}</td><td>{point.low ?? '缺失'}</td><td>{point.close}</td><td>{point.volume ?? '缺失'}</td><td>{point.amount ?? '缺失'}</td></tr>)}</tbody></table></div></div>
}
