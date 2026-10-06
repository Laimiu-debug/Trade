import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'

type CloseResult = { symbol: string; provider: string; adjustment: string; requested_date: string; trading_date: string; close: string; available_at: string | null; availability_quality: string }

export function MarketCloseLookup({ datasetId, lastDate }: { datasetId: string; lastDate: string }) {
  const [day, setDay] = useState(lastDate)
  const [result, setResult] = useState<CloseResult | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { setDay(lastDate); setResult(null); setError('') }, [datasetId, lastDate])
  async function lookup() {
    setResult(null); setError('')
    try {
      const query = new URLSearchParams({ dataset_id: datasetId, day })
      setResult(await api<CloseResult>('/market/close?' + query.toString()))
    } catch (err) { setError(err instanceof Error ? err.message : '收盘价查询失败') }
  }
  return <div className="market-detail"><h3>指定日期收盘价</h3><div className="form-actions"><label className="field">日期<input aria-label="收盘价查询日期" type="date" value={day} onChange={event => { setDay(event.target.value); setResult(null); setError('') }} /></label><button className="button secondary" onClick={lookup}><Icon name="search" />查询收盘价</button></div>{error && <p className="alert error">{error}</p>}{result && <div className="period-summary"><span>交易日期：{result.trading_date}</span><span>收盘价：¥ {result.close}</span><span>来源：{result.provider}</span><span>复权：{result.adjustment}</span><span>历史可得时间：{result.available_at ?? '未知'}</span></div>}<p className="muted">仅查询所选冻结样本的精确日期；非交易日或缺失时不取邻近价格。</p></div>
}
