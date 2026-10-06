import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import { sameMarketSymbol } from './market-symbols'
import { Icon } from './workspace-icons'

type Candidate = {
  symbol: string; dataset_id: string; as_of_date: string; name?: string; ret40: number
  turnover20: number | null; amount20: number | null; amplitude20: number
  trend_class: string; retrace20: number; pullback_days: number
  vol_slope20: number; up_down_volume_ratio: number
}
type Config = {
  enabled: boolean; source_mode: 'filter' | 'strategy'; ret40_min: number; ret40_max: number
  turnover20_min: number; amount20_min: number; amplitude20_min: number
  trend_classes: string[]; retrace20_min: number; retrace20_max: number
  pullback_days_max: number; vol_slope20_min: number; up_down_volume_ratio_min: number; top_n: number
}
type State = { config: Config; manual: Candidate[]; automatic: Candidate[]; order: string[] }
type Saved = { state: State; revision: number; sha256: string; updated_at: string | null }
type Preview = { state: State; member_count: number; notes: string[] }

const key = 'trade-rebuild.watch-pool.v1'
const defaults: Config = {
  enabled: true, source_mode: 'filter', ret40_min: 0.2, ret40_max: 2,
  turnover20_min: 0.05, amount20_min: 5e8, amplitude20_min: 0.03,
  trend_classes: ['A', 'A_B'], retrace20_min: 0, retrace20_max: 0.3,
  pullback_days_max: 5, vol_slope20_min: 0, up_down_volume_ratio_min: 0, top_n: 50,
}
const numericLabels: Record<Exclude<keyof Config, 'enabled' | 'source_mode' | 'trend_classes'>, string> = {
  ret40_min: '40 日涨幅下限', ret40_max: '40 日涨幅上限', turnover20_min: '20 日换手率下限',
  amount20_min: '20 日成交额下限（元）', amplitude20_min: '20 日振幅下限',
  retrace20_min: '20 日回撤下限', retrace20_max: '20 日回撤上限',
  pullback_days_max: '最大回撤天数', vol_slope20_min: '20 日量能斜率下限',
  up_down_volume_ratio_min: '上涨 / 下跌量比下限', top_n: '最多观察只数',
}

function readLegacy(): State | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || 'null')
    if (parsed && typeof parsed === 'object') {
      return { config: { ...defaults, ...parsed.config }, automatic: [],
               manual: Array.isArray(parsed.manual) ? parsed.manual : [],
               order: Array.isArray(parsed.order) ? parsed.order : [] }
    }
  } catch { /* Invalid local preference falls back to defaults. */ }
  return null
}

export function calculateWatchPool(input: Candidate[], config: Config): Candidate[] {
  if (!config.enabled || config.source_mode !== 'filter') return []
  return input.filter(row => row.ret40 >= config.ret40_min && row.ret40 <= config.ret40_max &&
    row.turnover20 !== null && row.turnover20 >= config.turnover20_min &&
    row.amount20 !== null && row.amount20 >= config.amount20_min &&
    row.amplitude20 >= config.amplitude20_min && config.trend_classes.includes(row.trend_class) &&
    row.retrace20 >= config.retrace20_min && row.retrace20 <= config.retrace20_max &&
    row.pullback_days <= config.pullback_days_max && row.vol_slope20 >= config.vol_slope20_min &&
    row.up_down_volume_ratio >= config.up_down_volume_ratio_min)
    .sort((a, b) => b.ret40 - a.ret40).slice(0, config.top_n)
}

export function WatchPoolPanel({ input, current, b1Symbols, onOpenMarket }: {
  input: Candidate[]; current: Candidate[]; b1Symbols: string[]; onOpenMarket: (datasetId: string) => void
}) {
  const [state, setState] = useState<State>({ config: defaults, manual: [], automatic: [], order: [] })
  const [saved, setSaved] = useState<Saved | null>(null)
  const [latest, setLatest] = useState<Saved | null>(null)
  const [legacy] = useState(readLegacy)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [audit, setAudit] = useState<Array<{ id: string; revision: number; action: string; created_at: string; snapshot: Saved }>>([])
  const [picked, setPicked] = useState('')
  useEffect(() => {
    let active = true
    api<Saved>('/research/watch-pool').then(value => { if (active) { setSaved(value); setState(value.state) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : '观察池读取失败') })
    return () => { active = false }
  }, [])
  const automaticPreview = useMemo(() => !state.config.enabled ? [] : state.config.source_mode === 'strategy'
    ? b1Symbols.map(symbol => input.find(row => sameMarketSymbol(row.symbol, symbol)))
        .filter((row): row is Candidate => Boolean(row)).slice(0, state.config.top_n)
    : calculateWatchPool(input, state.config), [input, b1Symbols, state.config])
  const rows = useMemo(() => {
    const combined = [...state.manual, ...state.automatic.filter(row => !state.manual.some(item => sameMarketSymbol(item.symbol, row.symbol)))]
    return [...combined].sort((a, b) => {
      const ai = state.order.findIndex(value => sameMarketSymbol(value, a.symbol)), bi = state.order.findIndex(value => sameMarketSymbol(value, b.symbol))
      if (ai === -1 && bi === -1) return 0
      if (ai === -1) return 1
      if (bi === -1) return -1
      return ai - bi
    })
  }, [state.manual, state.automatic, state.order])

  const dirty = saved !== null && JSON.stringify(state) !== JSON.stringify(saved.state)
  async function perform(task: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await task() }
    catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
      if ((err as { code?: string }).code === 'WATCH_POOL_VERSION_CONFLICT') {
        try { setLatest(await api<Saved>('/research/watch-pool')) } catch { /* Preserve draft while offline. */ }
      }
    } finally { setBusy(false) }
  }
  function save(importing = false) {
    if (!saved) return
    void perform(async () => {
      const value = await api<Saved>(`/research/watch-pool${importing ? '/import' : ''}`, importing ? 'POST' : 'PUT',
        { expected_revision: saved.revision, state: importing ? preview!.state : state })
      setSaved(value); setState(value.state); setLatest(null); setPreview(null)
      setMessage(importing ? '旧本机记录已迁入；本机原记录仍保留。' : '观察池已保存到服务端。')
    })
  }

  function change<K extends keyof Config>(field: K, value: Config[K]) {
    setState(previous => ({ ...previous, config: { ...previous.config, [field]: value } }))
  }
  function addManualRow(row: Candidate) {
    setState(previous => previous.manual.some(item => sameMarketSymbol(item.symbol, row.symbol)) ? previous :
      { ...previous, manual: [row, ...previous.manual], automatic: previous.automatic.filter(item => !sameMarketSymbol(item.symbol, row.symbol)),
        order: [row.symbol, ...previous.order.filter(item => !sameMarketSymbol(item, row.symbol))] })
  }
  function addManual() {
    const row = current.find(item => item.symbol === picked)
    if (row) addManualRow(row)
  }
  function move(symbol: string, delta: number) {
    const order = rows.map(row => row.symbol)
    const index = order.indexOf(symbol), next = index + delta
    if (index < 0 || next < 0 || next >= order.length) return
    ;[order[index], order[next]] = [order[next], order[index]]
    setState(previous => ({ ...previous, order }))
  }

  return <section className="market-detail watch-pool"><h3>观察池</h3><p className="muted">全账户共享的研究观察池。成员、排序和参数明确保存到服务端并进入完整备份。自动过滤取当前漏斗输入池；B1 模式取当前 B1 与输入池的交集。自动成员仅在点击更新时替换，保留当时冻结样本与日期；不随打开记录自动变化。</p>
    <div className="toolbar"><button className="button primary" disabled={!saved || busy || !dirty || Boolean(latest)} onClick={() => save()}><Icon name="save" />保存观察池</button><span className="subtle">{saved ? `修订 ${saved.revision} · ${dirty ? '有未保存更改' : '已同步'}` : '读取服务端…'}</span><button className="button secondary" disabled={busy} onClick={() => void perform(async () => { const value = await api<Saved>('/research/watch-pool'); setLatest(value) })}>读取最新版本对比</button><button className="button secondary" disabled={busy} onClick={() => void perform(async () => { setAudit(await api('/research/watch-pool/audit')) })}>读取保存历史</button></div>
    {error && <p className="alert error" role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {latest && <div className="sub-card"><h4>比较服务端修订 {latest.revision}</h4><p>当前草稿未覆盖。请明确选择采用服务端内容，或保留草稿后重新审阅保存。</p><details><summary>服务端内容</summary><pre className="json-view">{JSON.stringify(latest, null, 2)}</pre></details><button className="button secondary" disabled={busy} onClick={() => { setState(latest.state); setSaved(latest); setLatest(null) }}>采用服务端版本</button><button className="button secondary" disabled={busy} onClick={() => { setSaved(latest); setLatest(null) }}>保留草稿，以最新修订继续编辑</button></div>}
    {legacy && <details><summary>迁入旧本机观察池（原记录保留）</summary><p>检测到 {legacy.manual.length} 个人工成员。预览核对行情身份、日期及已保存筛选来源；确认迁入将用此预览替换当前服务端观察池，并新增历史版本。</p><button className="button secondary" disabled={busy || !saved} onClick={() => void perform(async () => { setPreview(await api<Preview>('/research/watch-pool/preview', 'POST', { state: legacy })) })}><Icon name="document" />预览旧本机记录</button>{preview && <><p>已验证 {preview.member_count} 个成员。{preview.notes.join(' ')}</p><details><summary>查看迁入内容</summary><pre className="json-view">{JSON.stringify(preview.state, null, 2)}</pre></details><button className="button secondary" disabled={busy || Boolean(latest)} onClick={() => save(true)}><Icon name="check" />确认迁入并替换服务端观察池</button></>}</details>}
    <fieldset disabled={!saved || busy}><legend>观察池草稿</legend>
    <div className="form-grid"><label className="check-field"><input type="checkbox" checked={state.config.enabled} onChange={event => change('enabled', event.target.checked)} />启用自动观察</label><label className="field"><span>来源</span><select value={state.config.source_mode} onChange={event => change('source_mode', event.target.value as Config['source_mode'])}><option value="filter">指标过滤</option><option value="strategy">B1 命中</option></select></label></div>
    {state.config.source_mode === 'filter' && <details><summary>观察池过滤参数</summary><div className="form-grid">{(Object.keys(numericLabels) as Array<keyof typeof numericLabels>).map(field => <label className="field" key={field}><span>{numericLabels[field]}</span><input type="number" step="any" value={state.config[field]} onChange={event => change(field, Number(event.target.value))} /></label>)}<fieldset><legend>趋势类别</legend>{(['A', 'A_B', 'B', 'Unknown'] as const).map(value => <label key={value} className="check-field"><input type="checkbox" checked={state.config.trend_classes.includes(value)} onChange={event => change('trend_classes', event.target.checked ? [...state.config.trend_classes, value] : state.config.trend_classes.filter(item => item !== value))} />{value}</label>)}</fieldset></div></details>}
    <p className="muted">当前条件预览 {automaticPreview.length} 只；已保存到草稿的自动成员 {state.automatic.length} 只。修改参数后请更新成员。</p><button className="button secondary" onClick={() => setState(previous => ({ ...previous, automatic: automaticPreview.filter(row => !previous.manual.some(item => sameMarketSymbol(item.symbol, row.symbol))) }))}>使用当前筛选更新自动成员</button>
    <div className="form-grid"><label className="field"><span>从当前阶段人工加入</span><select value={picked} onChange={event => setPicked(event.target.value)}><option value="">选择证券</option>{current.map(row => <option key={row.dataset_id} value={row.symbol}>{row.symbol} · {row.as_of_date}</option>)}</select></label><button type="button" className="button secondary" disabled={!picked} onClick={addManual}>加入观察池</button><button type="button" className="button secondary" disabled={state.manual.length === 0} onClick={() => setState(previous => ({ ...previous, manual: [], order: [] }))}>清空人工观察</button></div>
    <div className="table-wrap"><table><thead><tr><th>顺序</th><th>证券</th><th>来源</th><th>冻结样本</th><th>40 日涨幅</th><th>操作</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.symbol}><td>{index + 1}</td><td>{row.symbol}</td><td>{state.manual.some(item => item.symbol === row.symbol) ? '人工' : '自动快照'}</td><td>{row.as_of_date} · {row.dataset_id.slice(0, 10)}</td><td>{(row.ret40 * 100).toFixed(2)}%</td><td><button className="link-button" onClick={() => onOpenMarket(row.dataset_id)}>查看 K 线</button><button className="link-button" disabled={index === 0} onClick={() => move(row.symbol, -1)}>上移</button><button className="link-button" disabled={index === rows.length - 1} onClick={() => move(row.symbol, 1)}>下移</button>{state.manual.some(item => item.symbol === row.symbol) && <button className="link-button" onClick={() => setState(previous => ({ ...previous, manual: previous.manual.filter(item => item.symbol !== row.symbol) }))}>移除人工</button>}</td></tr>)}</tbody></table></div>{rows.length === 0 && <p className="muted">当前观察池为空。</p>}
    </fieldset>
    {audit.length > 0 && <details open><summary>最近 100 次保存历史</summary><div className="table-wrap"><table><thead><tr><th>修订</th><th>操作 / 时间</th><th>成员</th><th>恢复到草稿</th></tr></thead><tbody>{audit.map(item => <tr key={item.id}><td>{item.revision}</td><td>{item.action} · {item.created_at}</td><td>{item.snapshot.state.manual.length + item.snapshot.state.automatic.length}</td><td><button className="link-button" disabled={busy} onClick={() => { setState(item.snapshot.state); setMessage('历史版本已复制到草稿，需明确保存后生效。') }}>复制到草稿</button></td></tr>)}</tbody></table></div></details>}
  </section>
}

