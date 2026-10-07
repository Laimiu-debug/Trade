import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './workspace-icons'
const SystemSettings = lazy(() => import('./system-settings').then(module => ({ default: module.SystemSettings })))
const LegacyImportWorkspace = lazy(() => import('./legacy-import').then(module => ({ default: module.LegacyImportWorkspace })))
const StrategyRegistry = lazy(() => import('./strategy-registry').then(module => ({ default: module.StrategyRegistry })))
const ProviderHealth = lazy(() => import('./provider-health').then(module => ({ default: module.ProviderHealth })))
const BrowserPreferencesPanel = lazy(() => import('./browser-preferences-panel').then(module => ({ default: module.BrowserPreferencesPanel })))

type Group = 'fees' | 'targets' | 'ai' | 'market_sources' | 'display' | 'calendar' | 'storage' | 'legacy' | 'strategies' | 'print'
type Theme = 'light' | 'dark' | 'system'
type Density = 'comfortable' | 'compact'
type Value = Record<string, unknown>
type Saved = { group: string; scope: string; account_id: string | null; account_name: string | null; account_kind: string | null; revision: number; value: Value; notes: string[]; readonly: boolean; credential_status?: Record<string, boolean> }
type Preview = { expected_revision: number; operation: string; before: Value; after: Value; diff: Array<{ path: string; before: unknown; after: unknown }>; preview_sha256: string; notes: string[] }
type Props = { accountId?: string; accountKind?: string; themeMode: Theme; density: Density; onThemeChange: (mode: Theme) => void; onDensityChange: (density: Density) => void; onImported?: (accountId: string) => void }
const names: Record<Group, string> = { fees: '账户费用', targets: '净值目标', ai: 'AI 模型', market_sources: '行情来源', display: '显示与密度', calendar: '本地日历', storage: '存储与备份', legacy: '旧资料导入', strategies: '策略启用', print: '打印与署名' }
const labels: Record<string, string> = { commission_rate: '佣金率', minimum_commission: '最低佣金（元）', sell_stamp_rate: '卖出印花税率', transfer_rate: '过户费率', multiplier: '每级倍率', node_count: '节点数量', temperature: '温度', max_tokens: '最大输出 token', timeout_seconds: '超时（秒）', provider: '默认在线来源', provider_order: '自动回退顺序', themeMode: '主题', density: '列表密度' }
const show = (value: unknown) => value == null ? '未设置' : typeof value === 'object' ? JSON.stringify(value) : String(value)
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
const preStyle = { whiteSpace: 'pre-wrap' as const, overflowWrap: 'anywhere' as const, maxHeight: '22rem', overflow: 'auto' }

function Difference({ diff }: { diff: Preview['diff'] }) {
  return diff.length ? <div className="table-wrap"><table><thead><tr><th>字段</th><th>当前</th><th>将变为</th></tr></thead><tbody>{diff.map(row => <tr key={row.path}><td>{labels[row.path] || row.path}</td><td style={{ overflowWrap: 'anywhere', maxWidth: '24rem' }}>{show(row.before)}</td><td style={{ overflowWrap: 'anywhere', maxWidth: '24rem' }}>{show(row.after)}</td></tr>)}</tbody></table></div> : <p>此组已经是默认值，没有字段变化。</p>
}

function GroupEditor({ group, accountId, onBusy }: { group: Exclude<Group, 'display' | 'storage' | 'legacy' | 'strategies'>; accountId?: string; onBusy: (busy: boolean) => void }) {
  const scoped = group === 'fees' || group === 'targets'
  const suffix = scoped ? `?account_id=${encodeURIComponent(accountId || '')}` : ''
  const path = useCallback((action = '') => `/settings/groups/${group}${action}${suffix}`, [group, suffix])
  const [saved, setSaved] = useState<Saved | null>(null)
  const [draft, setDraft] = useState<Value>({})
  const [calendarText, setCalendarText] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [latest, setLatest] = useState<Saved | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<Array<{ id: string; operation: string; created_at: string; after: Saved }>>([])
  const [legacy] = useState(() => {
    if (group !== 'market_sources') return null
    const provider = localStorage.getItem('trade-market-provider')
    const preferred = localStorage.getItem('trade-market-preferred-provider')
    if (provider === null && preferred === null) return null
    return { provider: provider || 'auto', provider_order: preferred === 'akshare' ? ['akshare', 'baostock'] : ['baostock', 'akshare'] }
  })
  function adopt(value: Saved) { setSaved(value); setDraft(value.value); setCalendarText(pretty(value.value)); setLatest(null); setPreview(null) }
  useEffect(() => {
    let active = true
    api<Saved>(path()).then(value => { if (active) adopt(value) }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [path])
  async function work(action: () => Promise<void>) {
    setBusy(true); onBusy(true); setError(''); setNotice('')
    try { await action() }
    catch (err) {
      setError(err instanceof Error ? err.message : '设置操作失败')
      if ((err as { code?: string }).code === 'SETTINGS_REVISION_CONFLICT') {
        setPreview(null)
        try { setLatest(await api<Saved>(path())) } catch { /* Keep the unsaved draft. */ }
      }
    } finally { setBusy(false); onBusy(false) }
  }
  function field(key: string, value: unknown) { setDraft(previous => ({ ...previous, [key]: value })); setPreview(null) }
  function providerField(channel: string, key: string, value: string) { field(channel, { ...(draft[channel] as Value), [key]: value }) }
  function inputValue(): Value { return group === 'calendar' ? JSON.parse(calendarText) as Value : draft }
  const dirty = Boolean(saved) && (group === 'calendar' ? calendarText !== pretty(saved!.value) : pretty(draft) !== pretty(saved!.value))
  const disabled = busy || !saved || Boolean(saved?.readonly) || Boolean(latest)
  async function save() {
    if (!saved) return
    adopt(await api<Saved>(path(), 'PUT', { expected_revision: saved.revision, value: inputValue() }))
    setNotice('此设置组已保存。')
  }
  async function previewDefaults() { if (saved) setPreview(await api<Preview>(path('/defaults-preview'), 'POST', { expected_revision: saved.revision })) }
  async function confirm() {
    if (!preview) return
    const importing = preview.operation === 'update'
    adopt(await api<Saved>(path(importing ? '/import' : '/reset'), 'POST', { expected_revision: preview.expected_revision, preview_sha256: preview.preview_sha256, ...(importing ? { value: preview.after } : {}) }))
    setNotice(importing ? '旧来源偏好已迁入全局配置，本机原键仍保留。' : '仅此设置组已恢复默认。')
  }
  const numericFields = group === 'fees' ? ['commission_rate', 'minimum_commission', 'sell_stamp_rate', 'transfer_rate'] : group === 'targets' ? ['multiplier', 'node_count'] : []
  return <section className="card span-all"><h2>{names[group]}</h2>{saved && <><p><strong>{saved.scope === 'account' ? `当前账户：${saved.account_name} · ${saved.account_kind === 'real' ? '实盘' : '模拟'} · ${saved.account_id}` : '全局：当前数据目录内共享'}</strong></p><p className="subtle">版本 {saved.revision} · {dirty ? '有未保存草稿' : '已同步'}</p>{saved.notes.map(note => <p key={note} className="muted">{note}</p>)}</>}
    {error && <p className="alert error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {latest && <div className="alert"><h3>服务端已有版本 {latest.revision}</h3><p>当前草稿已保留；比较后明确采用服务端，或以最新修订继续编辑。</p><details><summary>最新内容</summary><pre style={preStyle}>{pretty(latest.value)}</pre></details><button className="button secondary" onClick={() => adopt(latest)}>采用服务端设置</button><button className="button secondary" onClick={() => { setSaved(latest); setLatest(null); setPreview(null) }}>保留草稿，以最新修订继续</button></div>}
    <fieldset disabled={disabled} style={{ border: 0, minWidth: 0, padding: 0 }}><legend>编辑本组</legend><div className="form-grid">{numericFields.map(key => <label className="field" key={key}><span>{labels[key]}</span><input aria-label={labels[key]} type="number" step={key === 'node_count' ? '1' : 'any'} value={String(draft[key] ?? '')} onChange={event => field(key, key === 'node_count' ? Number(event.target.value) : event.target.value)} /></label>)}</div>
    {group === 'print' && <div className="form-grid"><label className="field"><span>导出署名</span><input maxLength={80} value={String(draft.author || '')} onChange={event => field('author', event.target.value)} /></label><label className="field"><span>本机导出目录（绝对路径）</span><input maxLength={500} value={String(draft.export_directory || '')} onChange={event => field('export_directory', event.target.value)} /></label></div>}
    {group === 'market_sources' && <div className="form-grid"><label className="field"><span>默认在线来源</span><select value={String(draft.provider || 'auto')} onChange={event => field('provider', event.target.value)}><option value="auto">自动回退</option><option value="baostock">BaoStock</option><option value="akshare">AKShare</option></select></label><label className="field"><span>自动回退顺序</span><select value={(draft.provider_order as string[] | undefined)?.[0] || 'baostock'} onChange={event => field('provider_order', event.target.value === 'akshare' ? ['akshare', 'baostock'] : ['baostock', 'akshare'])}><option value="baostock">BaoStock → AKShare</option><option value="akshare">AKShare → BaoStock</option></select></label></div>}
    {group === 'ai' && <>{(['text', 'vision'] as const).map(channel => <fieldset key={channel}><legend>{channel === 'text' ? '文本模型' : '视觉模型'}</legend><div className="form-grid">{(['base_url', 'model', 'secret_ref'] as const).map(key => <label className="field" key={key}><span>{channel === 'text' ? '文本' : '视觉'} · {{ base_url: '接口地址', model: '模型名称', secret_ref: '密钥环境变量名称' }[key]}</span><input value={String((draft[channel] as Value | undefined)?.[key] || '')} onChange={event => providerField(channel, key, event.target.value)} autoComplete="off" /></label>)}</div><small className="muted">环境变量状态：{saved?.credential_status?.[channel] ? '已配置' : '未配置或匿名本地服务'}。此处不接收密钥值。</small></fieldset>)}<div className="form-grid">{['temperature', 'max_tokens', 'timeout_seconds'].map(key => <label key={key} className="field"><span>{labels[key]}</span><input type="number" step={key === 'temperature' ? '.1' : '1'} value={Number(draft[key] ?? 0)} onChange={event => field(key, Number(event.target.value))} /></label>)}</div><p className="muted">连接测试与调用记录保留在 AI 工作台，只有明确测试或发送才联网。</p></>}
    {group === 'calendar' && <><p className="muted">导入 JSON 须含 source/start_date/end_date/days；每个自然日写明 is_open，最多 1096 天。请核对来源；保存日历不联网。</p><label className="field"><span>读取日历 JSON 到草稿</span><input type="file" accept=".json,application/json" onChange={event => { const file = event.target.files?.[0]; if (file) void work(async () => { if (file.size > 256 * 1024) throw new Error('日历文件最多 256 KiB'); setCalendarText(pretty(JSON.parse(await file.text()))); setPreview(null) }); event.target.value = '' }} /></label><label className="field"><span>本地日历内容</span><textarea rows={12} value={calendarText} onChange={event => { setCalendarText(event.target.value); setPreview(null) }} spellCheck={false} /></label></>}
    <div className="toolbar"><button className="button primary" disabled={!dirty} onClick={() => void work(save)}><Icon name="save" />保存本组设置</button><button className="button secondary" onClick={() => void work(previewDefaults)}><Icon name="document" />预览本组默认值</button></div></fieldset>
    {legacy && <details><summary>迁入旧本机行情来源偏好</summary><p>保留原本机键，只有确认后才替换全局来源配置。</p><pre style={preStyle}>{pretty(legacy)}</pre><button className="button secondary" disabled={disabled} onClick={() => void work(async () => { if (saved) setPreview(await api<Preview>(path('/preview'), 'POST', { expected_revision: saved.revision, value: legacy })) })}><Icon name="document" />预览旧来源偏好</button></details>}
    {preview && <div className="market-detail"><h3>{preview.operation === 'defaults' ? '恢复默认预览' : '旧来源迁入预览'} · {names[group]}</h3><p>基于版本 {preview.expected_revision}；确认只修改当前组。{preview.operation === 'defaults' && dirty && '当前未保存草稿将被此默认值替换。'}</p><Difference diff={preview.diff} /><div className="toolbar"><button className="button primary" disabled={disabled || !preview.diff.length} onClick={() => void work(confirm)}>{preview.operation === 'defaults' ? '确认仅恢复此组默认值' : '确认迁入旧来源偏好'}</button><button className="button secondary" onClick={() => setPreview(null)}><Icon name="close" />取消预览</button></div></div>}
    <div className="toolbar"><button className="button secondary" disabled={busy} onClick={() => void work(async () => { setLatest(await api<Saved>(path())) })}>读取最新版本对比</button><button className="button secondary" disabled={busy} onClick={() => void work(async () => { setHistory(await api(path('/audit'))) })}>读取本组变更历史</button></div>
    {history.length > 0 && <details><summary>本组最近 100 条变更</summary><div className="table-wrap"><table><thead><tr><th>版本</th><th>操作</th><th>时间</th><th>内容</th></tr></thead><tbody>{history.map(item => <tr key={item.id}><td>{item.after.revision}</td><td>{item.operation}</td><td>{item.created_at}</td><td><details><summary>查看</summary><pre style={preStyle}>{pretty(item.after.value)}</pre></details></td></tr>)}</tbody></table></div></details>}
  </section>
}

function localDisplay() {
  const raw = localStorage.getItem('trade-theme-mode') || localStorage.getItem('trade-theme')
  const value = { themeMode: raw === 'dark' || raw === 'system' ? raw : 'light', density: localStorage.getItem('trade-list-density') === 'compact' ? 'compact' : 'comfortable' }
  let previous: { revision: number; value: typeof value } | null = null
  try { previous = JSON.parse(localStorage.getItem('trade-rebuild.display-settings.v1') || 'null') } catch { /* Invalid metadata is replaced, not the display values. */ }
  const revision = previous && Number.isSafeInteger(previous.revision) && previous.revision >= 0 ? previous.revision + (pretty(previous.value) === pretty(value) ? 0 : 1) : 0
  const snapshot = { revision, value }
  localStorage.setItem('trade-rebuild.display-settings.v1', JSON.stringify(snapshot))
  return snapshot
}

function DisplaySettings({ themeMode, density, onThemeChange, onDensityChange }: Props) {
  const [preview, setPreview] = useState<ReturnType<typeof localDisplay> | null>(null)
  const [error, setError] = useState('')
  const defaults = { themeMode: 'light' as Theme, density: 'comfortable' as Density }
  function confirm() {
    if (!preview) return
    try {
      const current = localDisplay()
      if (current.revision !== preview.revision || pretty(current.value) !== pretty(preview.value) || themeMode !== preview.value.themeMode || density !== preview.value.density) throw new Error('本机显示设置已变化，请重新预览；其他浏览器标签页的修改不会被覆盖。')
      localStorage.setItem('trade-theme-mode', defaults.themeMode); localStorage.setItem('trade-list-density', defaults.density)
      localStorage.setItem('trade-rebuild.display-settings.v1', JSON.stringify({ revision: current.revision + 1, value: defaults }))
      onThemeChange(defaults.themeMode); onDensityChange(defaults.density); setPreview(null); setError('')
    } catch (err) { setError((err as Error).message); setPreview(null) }
  }
  return <section className="card span-all"><h2 className="title-with-icon"><Icon name="density" />显示与密度</h2><p>范围：当前浏览器，选项立即生效。不会改变服务端业务设置或其他账户数据。</p><div className="form-grid"><label className="field"><span>主题模式</span><select value={themeMode} onChange={event => onThemeChange(event.target.value as Theme)}><option value="light">浅色</option><option value="dark">深色</option><option value="system">跟随系统</option></select></label><label className="field"><span>列表密度</span><select value={density} onChange={event => onDensityChange(event.target.value as Density)}><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></label></div>{error && <p role="alert" className="alert error">{error}</p>}<button className="button secondary" onClick={() => { try { setPreview(localDisplay()); setError('') } catch (err) { setError((err as Error).message) } }}><Icon name="document" />预览显示默认值</button>{preview && <div className="market-detail"><h3>显示默认值预览 · 本机版本 {preview.revision}</h3><Difference diff={Object.keys(defaults).filter(key => preview.value[key as keyof typeof defaults] !== defaults[key as keyof typeof defaults]).map(key => ({ path: key, before: preview.value[key as keyof typeof defaults], after: defaults[key as keyof typeof defaults] }))} /><button className="button primary" onClick={confirm}><Icon name="check" />确认仅恢复显示默认值</button></div>}</section>
}

export function SettingsWorkspace(props: Props) {
  const initialGroup = (): Group => {
    const value = new URLSearchParams(window.location.search).get('settings_group')
    return value && Object.hasOwn(names, value) ? value as Group : 'market_sources'
  }
  const [group, setGroup] = useState<Group>(initialGroup)
  const [opened, setOpened] = useState<Group[]>(() => [initialGroup()])
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('settings_group', group)
    window.history.replaceState(window.history.state, '', url)
  }, [group])
  return <div className="two-col wide-left settings-workspace"><div className="span-all settings-intro"><div className="toolbar" role="tablist" aria-label="设置分组">{(Object.keys(names) as Group[]).map(key => <button type="button" role="tab" aria-selected={group === key} className={`button ${group === key ? 'primary' : 'secondary'}`} key={key} disabled={busy} onClick={() => { setGroup(key); setOpened(previous => previous.includes(key) ? previous : [...previous, key]) }}>{names[key]}</button>)}</div><p className="muted">每个分组分别保存、分别恢复默认。账户组只操作当前选中账户；全局组随当前数据目录保存与备份；显示偏好留在当前浏览器。</p></div>
    {opened.map(key => <div key={key} hidden={group !== key} className="span-all" role="tabpanel" aria-label={names[key]}>{key === 'strategies' ? <Suspense fallback={<p>加载策略设置…</p>}><StrategyRegistry onBusy={setBusy} /></Suspense> : key === 'storage' ? <Suspense fallback={<p>加载存储设置…</p>}><SystemSettings /></Suspense> : key === 'legacy' ? <Suspense fallback={<p>加载旧资料导入…</p>}><LegacyImportWorkspace onBusy={setBusy} onImported={props.onImported} /></Suspense> : key === 'display' ? <DisplaySettings {...props} /> : (key === 'fees' || key === 'targets') && (!props.accountId || (key === 'targets' && props.accountKind !== 'real')) ? <section className="card"><h2>{names[key]}</h2><p>{key === 'targets' ? '净值目标适用于实盘账户，请选择一个实盘账户。' : '请先选择此设置所属账户。'}</p></section> : <GroupEditor key={`${key}:${key === 'fees' || key === 'targets' ? props.accountId : 'global'}`} group={key} accountId={key === 'fees' || key === 'targets' ? props.accountId : undefined} onBusy={setBusy} />}</div>)}
    {group === 'market_sources' && <Suspense fallback={<p>加载来源能力…</p>}><ProviderHealth onBusy={setBusy} /></Suspense>}
    {group === 'display' && <Suspense fallback={<p>加载本机偏好…</p>}><BrowserPreferencesPanel onThemeChange={props.onThemeChange} onDensityChange={props.onDensityChange} /></Suspense>}
  </div>
}
