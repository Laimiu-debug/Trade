import { useEffect, useMemo, useState } from 'react'
import { StockSearch } from './stock-search'

type Form = { symbol: string; name: string; earningsYi: string; growthRatePct: string; basePe: string; indexPoints: string; sentimentCoef: string; actualCapYi: string }
const KEY = 'trade-rebuild:sentiment-valuation-v1'
const defaults: Form = { symbol: '', name: '', earningsYi: '10', growthRatePct: '20', basePe: '20', indexPoints: '4090', sentimentCoef: '1', actualCapYi: '' }
const pePresets = [['银行 / 保险', '10'], ['传统行业', '12.5'], ['消费', '17.5'], ['半科技', '22.5'], ['科技', '27.5'], ['高成长稀缺', '30']] as const
const examples: Array<{ name: string; note: string; form: Form }> = [
  { name: '长江电力', note: '旧版示例假设，非实时行情', form: { symbol: 'sh600900', name: '长江电力', earningsYi: '358', growthRatePct: '5', basePe: '12.5', indexPoints: '4090', sentimentCoef: '1', actualCapYi: '6523' } },
  { name: '贵州茅台', note: '旧版示例假设，非实时行情', form: { symbol: 'sh600519', name: '贵州茅台', earningsYi: '863', growthRatePct: '-15', basePe: '15', indexPoints: '4090', sentimentCoef: '1', actualCapYi: '15188' } },
  { name: '源杰科技', note: '旧版示例假设，非实时行情', form: { symbol: 'sh688498', name: '源杰科技', earningsYi: '12', growthRatePct: '100', basePe: '30', indexPoints: '4090', sentimentCoef: '1', actualCapYi: '2083' } },
  { name: '宏和科技', note: '旧版示例假设，非实时行情', form: { symbol: 'sh603256', name: '宏和科技', earningsYi: '10', growthRatePct: '150', basePe: '30', indexPoints: '4090', sentimentCoef: '1', actualCapYi: '2332' } },
  { name: '中际旭创', note: '旧版示例假设，非实时行情', form: { symbol: 'sz300308', name: '中际旭创', earningsYi: '50', growthRatePct: '20', basePe: '20', indexPoints: '4090', sentimentCoef: '1.5', actualCapYi: '' } },
]

function load(): Form {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (saved && typeof saved === 'object') return { ...defaults, ...saved }
  } catch { /* ignore damaged local preference */ }
  return defaults
}

function label(coef: number): string {
  if (coef < 0.95) return '偏低'
  if (coef <= 1.15) return '中性'
  if (coef <= 2) return '温和溢价'
  if (coef <= 3.5) return '高溢价'
  if (coef <= 6) return '极高溢价'
  return '超极值'
}

export function ValuationEditor() {
  const [form, setForm] = useState<Form>(load)
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(form)) } catch { /* local storage can be unavailable */ } }, [form])
  function edit(key: keyof Form, value: string) { setForm(current => ({ ...current, [key]: value })) }
  const calculation = useMemo(() => {
    const earnings = Number(form.earningsYi), growth = Number(form.growthRatePct), pe = Number(form.basePe)
    const index = Number(form.indexPoints), sentiment = Number(form.sentimentCoef)
    const actual = form.actualCapYi.trim() === '' ? null : Number(form.actualCapYi)
    if (![earnings, growth, pe, index, sentiment].every(Number.isFinite) || earnings <= 0 || growth <= -100 || pe <= 0 || index <= 0 || sentiment <= 0 || (actual !== null && (!Number.isFinite(actual) || actual <= 0))) return null
    const growthCoef = 1 + growth / 100
    const indexCoef = Math.max(0.5, index / 3000)
    const base = earnings * growthCoef * pe * indexCoef
    const theoretical = base * sentiment
    const implied = actual === null ? null : actual / base
    const scenarios = [
      { name: '谨慎', growth: Math.max(-99, growth - 20), sentiment: Math.max(0.1, sentiment * 0.7) },
      { name: '基准', growth, sentiment },
      { name: '乐观', growth: growth + 20, sentiment: sentiment * 1.3 },
    ].map(row => ({ ...row, cap: earnings * (1 + row.growth / 100) * pe * indexCoef * row.sentiment }))
    return { growthCoef, indexCoef, base, theoretical, implied, actual, scenarios,
      gapPct: actual === null ? null : (actual / theoretical - 1) * 100 }
  }, [form])

  return <div className="two-col wide-left"><section className="card"><h2>A 股五因子情绪估值</h2><p className="muted">市值（亿元）＝当期盈利（亿元）× 复合增速系数 × 基准 PE × 大盘水位系数 × 情绪溢价。所有行情与财务数值须人工核对；示例不是实时数据。</p><StockSearch onSelect={row => setForm(current => ({ ...current, symbol: row.prefixed_symbol, name: row.name }))} /><div className="form-grid"><label className="field">代码<input value={form.symbol} onChange={event => edit('symbol', event.target.value)} /></label><label className="field">名称<input value={form.name} onChange={event => edit('name', event.target.value)} /></label><label className="field">当期盈利（亿元，手工）<input type="number" step="any" value={form.earningsYi} onChange={event => edit('earningsYi', event.target.value)} /></label><label className="field">预期年增速（%）<input type="number" step="any" value={form.growthRatePct} onChange={event => edit('growthRatePct', event.target.value)} /></label><label className="field">行业基准 PE<input type="number" step="any" value={form.basePe} onChange={event => edit('basePe', event.target.value)} /></label><label className="field">大盘指数点位<input type="number" step="any" value={form.indexPoints} onChange={event => edit('indexPoints', event.target.value)} /></label><label className="field">情绪溢价系数<input type="number" step="any" value={form.sentimentCoef} onChange={event => edit('sentimentCoef', event.target.value)} /></label><label className="field">实际市值（亿元，可选）<input type="number" step="any" value={form.actualCapYi} onChange={event => edit('actualCapYi', event.target.value)} /></label></div><p className="muted">基准 PE 预设：</p><div className="form-actions">{pePresets.map(([name, pe]) => <button type="button" className="button secondary" key={name} onClick={() => edit('basePe', pe)}>{name} {pe}x</button>)}</div>{!calculation && <p className="alert error">盈利、PE、指数和情绪系数须为正数，增速须大于 -100%。亏损或缺失盈利请人工核实，不推导理论市值。</p>}{calculation && <><div className="metrics"><div className="metric"><span>理论市值</span><strong>{calculation.theoretical.toFixed(1)} 亿</strong></div><div className="metric"><span>基本面底座</span><strong>{calculation.base.toFixed(1)} 亿</strong></div><div className="metric"><span>隐含情绪溢价</span><strong>{calculation.implied === null ? '缺实际市值' : `${calculation.implied.toFixed(2)}x`}</strong></div></div><div className="period-summary"><span>增速系数：{calculation.growthCoef.toFixed(2)}x</span><span>大盘水位：{calculation.indexCoef.toFixed(2)}x（3000 点基准，下限 0.5）</span><span>设定情绪：{label(Number(form.sentimentCoef))}</span><span>实际与理论差：{calculation.gapPct === null ? '缺实际市值' : `${calculation.gapPct.toFixed(1)}%`}</span></div><h3>情景对照</h3><div className="table-wrap"><table><thead><tr><th>情景</th><th>增速</th><th>情绪</th><th>理论市值</th></tr></thead><tbody>{calculation.scenarios.map(row => <tr key={row.name}><td>{row.name}</td><td>{row.growth.toFixed(1)}%</td><td>{row.sentiment.toFixed(2)}x</td><td>{row.cap.toFixed(1)} 亿</td></tr>)}</tbody></table></div></>}</section><section className="card"><h2>旧版示例参数</h2><p className="muted">保留 final-trade 的示例假设供比较；实际市值与财务数据可能过时，载入后请复核。</p><div className="table-wrap"><table><thead><tr><th>示例</th><th>盈利</th><th>增速</th><th>PE</th><th>操作</th></tr></thead><tbody>{examples.map(row => <tr key={row.name}><td>{row.name}<br /><small className="muted">{row.note}</small></td><td>{row.form.earningsYi} 亿</td><td>{row.form.growthRatePct}%</td><td>{row.form.basePe}x</td><td><button className="link-button" onClick={() => setForm(row.form)}>载入</button></td></tr>)}</tbody></table></div><p className="muted">行情自动带入尚未接入。TTM、动态和静态 PE 暂不混用；只使用此处人工选择的行业基准 PE。</p></section></div>
}
