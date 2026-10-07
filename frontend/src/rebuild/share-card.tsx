import { designTokens } from './design-tokens.generated'
import { sameMarketSymbol } from './market-symbols'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import { StockSearch, type StockHit } from './stock-search'
import { Icon } from './workspace-icons'

type Bar = { event_date: string; close: string; available_at: string | null }
type Dataset = { id: string; symbol: string; provider: string; adjustment: string; first_date: string; last_date: string; availability_quality: string; bars?: Bar[] }
type HistoryRow = { kind: string; run_id: string; dataset_id: string; observed_at: string; created_at: string; outcome: string; strategy_id?: string }
type History = { items: HistoryRow[]; note: string; limit_per_kind: number }
const historyKinds: Record<string, string> = { strategy: '策略信号', screener: '四步漏斗', b1: 'B1 筛选' }
const outcomes: Record<string, string> = { signal: '观察信号', no_signal: '未触发', insufficient: '样本不足 / 数据不可用', excluded: '未进入输入池', no_hit: '未命中', hits: 'B1 命中', step4: '最终观察池', step3: '通过量能阶段', step2: '通过趋势阶段', step1: '通过流动性阶段', input: '进入输入池' }

function wrapLines(context: CanvasRenderingContext2D, value: string, width: number): string[] {
  let line = ''
  const lines: string[] = []
  for (const character of value) {
    if (character === '\n' || context.measureText(line + character).width > width) {
      lines.push(line)
      line = character === '\n' ? '' : character
    } else line += character
  }
  if (line || !lines.length) lines.push(line)
  return lines
}

export function ShareCardEditor() {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [historyData, setHistoryData] = useState<History | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [stock, setStock] = useState<StockHit | null>(null)
  const [datasetId, setDatasetId] = useState('')
  const [dataset, setDataset] = useState<Dataset | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [generated, setGenerated] = useState<{ body: string; at: string } | null>(null)
  const refresh = useCallback(async () => {
    setDatasets(await api<Dataset[]>('/market/datasets'))
  }, [])
  useEffect(() => { refresh().catch(err => setError(err.message)) }, [refresh])
  useEffect(() => {
    let active = true
    setHistoryData(null); setHistoryError('')
    if (stock) api<History>('/research/stock-history?symbol=' + encodeURIComponent(stock.prefixed_symbol || stock.symbol))
      .then(value => { if (active) setHistoryData(value) }).catch(err => { if (active) setHistoryError(err.message) })
    return () => { active = false }
  }, [stock])
  const matching = datasets.filter(row => sameMarketSymbol(row.symbol, stock?.prefixed_symbol || stock?.symbol))
  useEffect(() => {
    if (!datasetId) { setDataset(null); return }
    let active = true
    api<Dataset>('/market/datasets/' + datasetId).then(row => {
      if (!active) return
      setDataset(row); setFrom(row.first_date); setTo(row.last_date)
    }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [datasetId])
  const bars = useMemo(() => (dataset?.bars || []).filter(row => (!from || row.event_date >= from) && (!to || row.event_date <= to)), [dataset, from, to])
  const prices = bars.map(row => Number(row.close))
  const latest = bars.at(-1)
  const change = bars.length >= 2 ? (Number(latest?.close) / Number(bars[0].close) - 1) * 100 : null
  const history = historyData?.items || []
  const source = dataset ? `${dataset.provider} · ${dataset.adjustment} · ${dataset.id.slice(0, 12)} · ${dataset.availability_quality}` : '无冻结行情；价格走势缺失'
  const copyBody = `【复盘分享卡】${stock?.name || '未选择证券'}${stock ? ` (${stock.prefixed_symbol})` : ''}\n${latest ? `区间 ${bars[0].event_date} 至 ${latest.event_date}；末日收盘 ¥${latest.close}；区间涨跌 ${change === null ? '缺失' : change.toFixed(2) + '%'}` : '价格走势缺失'}\n行情来源：${source}\n复盘附言：${note.trim() || '无'}`
  const copyText = `${copyBody}\n生成时间：${generated?.body === copyBody ? generated.at : '复制文案或导出图片时生成'}`

  async function copy() {
    setError(''); setNotice('')
    const timestamp = new Date().toISOString()
    setGenerated({ body: copyBody, at: timestamp })
    try { await navigator.clipboard.writeText(`${copyBody}\n生成时间：${timestamp}`); setNotice('复盘文案已复制') }
    catch { setError('剪贴板不可用，请手动复制预览文案') }
  }

  async function exportPng() {
    setError(''); setNotice('')
    const timestamp = new Date()
    const canvas = document.createElement('canvas')
    canvas.width = 1200
    const context = canvas.getContext('2d')
    if (!context) { setError('当前浏览器无法生成 PNG'); return }
    context.font = '22px Microsoft YaHei, sans-serif'
    const noteLines = wrapLines(context, note.trim() || '无', 1060)
    canvas.height = Math.max(675, 530 + (noteLines.length - 1) * 32 + 120)
    const colors = designTokens.themes.light.color
    context.fillStyle = colors['bg.canvas']; context.fillRect(0, 0, canvas.width, canvas.height)
    context.fillStyle = colors['action.primary']; context.fillRect(0, 0, 1200, 14)
    context.fillStyle = colors['text.primary']; context.font = 'bold 38px Microsoft YaHei, sans-serif'
    context.fillText(stock?.name || '未选择证券', 70, 82)
    context.font = '23px Microsoft YaHei, sans-serif'; context.fillStyle = colors['text.muted']
    context.fillText(stock?.prefixed_symbol || '无代码', 70, 122)
    context.font = '26px Microsoft YaHei, sans-serif'; context.fillStyle = colors['text.primary']
    context.fillText(latest ? `收盘 ¥${latest.close}` : '价格走势缺失', 70, 184)
    context.fillText(change === null ? '区间涨跌缺失' : `区间涨跌 ${change.toFixed(2)}%`, 450, 184)
    context.fillStyle = colors['text.muted']; context.font = '17px Microsoft YaHei, sans-serif'
    context.fillText(latest ? `${bars[0].event_date} 至 ${latest.event_date} · ${bars.length} 根日线` : '未选择有效行情区间', 70, 207)
    context.fillStyle = colors['bg.surface']; context.fillRect(70, 215, 1060, 225)
    if (prices.length > 1) {
      const low = Math.min(...prices), high = Math.max(...prices), span = Math.max(high - low, 0.01)
      context.beginPath(); context.strokeStyle = colors['chart.series1']; context.lineWidth = 4
      prices.forEach((price, index) => {
        const x = 100 + index / (prices.length - 1) * 1000
        const y = 405 - (price - low) / span * 155
        if (index === 0) context.moveTo(x, y); else context.lineTo(x, y)
      }); context.stroke()
    } else { context.fillStyle = colors['text.muted']; context.font = '24px Microsoft YaHei, sans-serif'; context.fillText('无可用区间走势', 95, 335) }
    context.fillStyle = colors['text.primary']; context.font = 'bold 25px Microsoft YaHei, sans-serif'; context.fillText('复盘附言', 70, 490)
    context.font = '22px Microsoft YaHei, sans-serif'; noteLines.forEach((line, index) => context.fillText(line, 70, 530 + index * 32))
    context.fillStyle = colors['text.muted']; context.font = '17px Microsoft YaHei, sans-serif'
    context.fillText(source.slice(0, 95), 70, canvas.height - 37)
    context.fillText(timestamp.toLocaleString('zh-CN'), 920, canvas.height - 37)
    const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'))
    if (!blob) { setError('PNG 编码失败'); return }
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = `trade-share-${stock?.symbol || 'note'}-${timestamp.getTime()}.png`; link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
    setGenerated({ body: copyBody, at: timestamp.toISOString() })
    setNotice('复盘分享卡 PNG 已下载')
  }

  const yLow = prices.length ? Math.min(...prices) : 0, yHigh = prices.length ? Math.max(...prices) : 1
  const path = prices.length > 1 ? prices.map((price, index) => `${index ? 'L' : 'M'}${20 + index / (prices.length - 1) * 860},${180 - (price - yLow) / Math.max(yHigh - yLow, 0.01) * 150}`).join(' ') : ''
  return <div className="two-col wide-left"><section className="card"><h2 className="title-with-icon"><Icon name="share" />复盘分享卡</h2><p className="muted">从离线股票库选证券，再选择冻结行情样本与日期范围。无行情仍可导出附言，并明确标记价格缺失。</p><StockSearch onSelect={row => { setStock(row); setDatasetId(''); setDataset(null); setFrom(''); setTo('') }} /><label className="field">冻结行情样本<select value={datasetId} onChange={event => setDatasetId(event.target.value)}><option value="">无行情，仅正文</option>{matching.map(row => <option key={row.id} value={row.id}>{row.provider} · {row.first_date} 至 {row.last_date} · {row.id.slice(0, 12)}</option>)}</select></label><div className="form-grid"><label className="field">开始日期<input type="date" value={from} disabled={!dataset} onChange={event => setFrom(event.target.value)} /></label><label className="field">结束日期<input type="date" value={to} disabled={!dataset} onChange={event => setTo(event.target.value)} /></label></div><label className="field">复盘附言<textarea rows={6} value={note} onChange={event => setNote(event.target.value)} maxLength={1000} /></label>{error && <p className="alert error">{error}</p>}{notice && <p className="alert success">{notice}</p>}<div className="form-actions"><button className="button secondary" onClick={copy}><Icon name="copy" />复制文案</button><button className="button primary" onClick={exportPng}><Icon name="download" />导出分享卡 PNG</button></div><h3>证券的筛选与策略历史</h3><p className="muted">按证券身份合并各份冻结行情的记录；行情图仍只使用上方选定样本。每类最多显示最近 {historyData?.limit_per_kind || 50} 条。</p>{historyData?.note && <p className="muted">{historyData.note}</p>}{historyError && <p role="alert" className="alert error">研究历史读取失败：{historyError}</p>}<div className="table-wrap"><table aria-label="证券研究历史"><thead><tr><th>来源</th><th>观察日期 / 决策时间</th><th>结果</th><th>冻结样本 / 记录</th><th>实际保存时间</th></tr></thead><tbody>{history.map(row => <tr key={row.kind + row.run_id + row.dataset_id}><td>{historyKinds[row.kind] || row.kind}{row.strategy_id && <><br /><small>{row.strategy_id}</small></>}</td><td>{row.observed_at}</td><td>{outcomes[row.outcome] || row.outcome}</td><td>{row.dataset_id === datasetId ? '当前样本' : '同证券其他样本'} · {row.dataset_id.slice(0, 12)}<br /><small>{row.run_id.slice(0, 16)}</small></td><td>{row.created_at}</td></tr>)}</tbody></table></div>{history.length === 0 && !historyError && <p className="muted">此证券暂无已保存的筛选或策略记录。</p>}</section><section className="card"><h2 className="title-with-icon"><Icon name="share" />分享预览</h2><div className="market-detail"><h3>{stock?.name || '未选择证券'} · {stock?.prefixed_symbol || '—'}</h3><p>{latest ? `${bars[0].event_date} 至 ${latest.event_date} · 末日收盘 ¥ ${latest.close} · 区间涨跌 ${change === null ? '缺失' : change.toFixed(2) + '%'}` : '价格走势缺失'}</p><p className="muted">{source}</p><svg viewBox="0 0 900 200" role="img" aria-label="分享卡区间收盘价走势" preserveAspectRatio="none">{path && <path d={path} fill="none" stroke="var(--brand-accent)" strokeWidth="3" />}</svg><p>{note.trim() || '复盘附言将在这里显示。'}</p></div><label className="field">可复制文案<textarea rows={9} value={copyText} readOnly /></label></section></div>
}
