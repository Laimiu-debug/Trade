import { z } from 'zod'

const short = z.string().max(120)
const num = z.number().finite()
const id = z.string().regex(/^(?:[a-f0-9]{32}|[a-f0-9]{64})$/)
const optionalId = z.union([id, z.literal('')]).optional()
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/)
const markets = z.array(z.enum(['sh', 'sz', 'bj'])).max(3)
const boards = z.array(z.enum(['main', 'gem', 'star', 'beijing', 'st'])).max(5)
const dictionary = z.record(id, short)
const steps = z.object({
  step1: z.object({ rank_start: num, top_n: num, turnover_threshold: num, amount_threshold: num, amplitude_threshold: num }).strict(),
  step2: z.object({ retrace_min: num, retrace_max: num, max_pullback_days: num, min_ma10_above_ma20_days: num, min_ma5_above_ma10_days: num, max_price_vs_ma20: num, require_above_ma20: z.boolean(), allow_b_trend: z.boolean() }).strict(),
  step3: z.object({ min_vol_slope20: num, min_up_down_volume_ratio: num, max_pullback_volume_ratio: num, allow_blowoff_top: z.boolean(), allow_divergence_5d: z.boolean(), allow_upper_shadow_risk: z.boolean(), allow_degraded: z.boolean() }).strict(),
  step4: z.object({ final_top_n: num, min_ai_confidence: num, allowed_theme_stages: z.array(short).max(20), allow_degraded: z.boolean() }).strict(),
}).strict()
const b1 = z.object({ vol_ratio: num, chg_limit: num, amp_limit_10cm: num, amp_limit_20cm: num, kdj_j_upper: num }).strict()
const range = { dateFrom: day, dateTo: day, maxBars: num, lastRunId: optionalId }
const scenario = z.object({ label: short, earnings_yi: short, growth_rate_pct: num, base_pe: num, index_points: num, sentiment_coef: num, actual_cap_yi: short }).strict()
const schemas: Record<string, z.ZodType> = {
  'trade-theme-mode': z.enum(['light', 'dark', 'system']),
  'trade-theme': z.enum(['light', 'dark']),
  'trade-list-density': z.enum(['comfortable', 'compact']),
  'trade-rebuild.research-tab.v1': z.enum(['funnel', 'trend', 'ladder', 'sector', 'abnormal', 'valuation', 'matrix']),
  'trade-rebuild.screener-last-run.v1': id,
  'trade-rebuild.b1-last-run.v1': id,
  'trade-rebuild.screener-prefs.v1': z.object({ selectedIds: z.array(id).max(2000), floatShares: dictionary, shareDates: dictionary, shareHashes: dictionary, asOfDate: day, windowDays: num, mode: z.enum(['strict', 'loose']), settings: steps, universeMarkets: markets, maxBars: num, b1Params: b1, b1MaxBars: num, stage: z.enum(['input', 'step1', 'step2', 'step3', 'step4']) }).strict(),
  'trade-rebuild.trend-leaders.v1': z.object({ ...range, windowDays: num, dailyTopN: num, minAmount: num, boards, markets }).strict(),
  'trade-rebuild.limit-up-ladder.v1': z.object({ ...range, recentDays: num, minBoards: num, boards, markets }).strict(),
  'trade-rebuild.abnormal-scan.v1': z.object({ ...range, markets, boards, mode: z.enum(['snapshot', 'full']), includeWarnings: z.boolean(), coolingDays: num }).strict(),
  'trade-rebuild.sector-capital.v1': z.object({ ...range, dailyTopN: num, flowWindow: num, chartMode: z.enum(['flow', 'return']), tableFilter: z.enum(['all', 'top_flow', 'top_return']) }).strict(),
  'trade-rebuild.sentiment-valuation.v1': z.object({ symbol: short, datasetId: optionalId, scenarios: z.array(scenario).min(1).max(50), lastRunId: optionalId }).strict(),
  'trade-rebuild:sentiment-valuation-v1': z.object({ symbol: short, name: short, earningsYi: short, growthRatePct: short, basePe: short, indexPoints: short, sentimentCoef: short, actualCapYi: short }).strict(),
}
export const preferenceNames: Record<string, string> = {
  'trade-theme-mode': '主题模式', 'trade-theme': '旧主题兼容值', 'trade-list-density': '列表密度',
  'trade-rebuild.research-tab.v1': '研究页签', 'trade-rebuild.screener-last-run.v1': '最近漏斗结果',
  'trade-rebuild.b1-last-run.v1': '最近 B1 结果', 'trade-rebuild.screener-prefs.v1': '漏斗 / B1 参数与样本选择',
  'trade-rebuild.trend-leaders.v1': '趋势参数 / 最近记录', 'trade-rebuild.limit-up-ladder.v1': '连板参数 / 最近记录',
  'trade-rebuild.abnormal-scan.v1': '异动参数 / 最近记录', 'trade-rebuild.sector-capital.v1': '板块参数 / 显示 / 最近记录',
  'trade-rebuild.sentiment-valuation.v1': '情绪估值场景 / 最近记录', 'trade-rebuild:sentiment-valuation-v1': '即时估值表单',
}
const rawKeys = new Set(['trade-theme-mode', 'trade-theme', 'trade-list-density'])
type Entries = Record<string, string>
type Bundle = { format: 'trade-browser-preferences'; version: 1; created_at: string; entries: Entries; sha256: string }
export type PreferencePreview = { bundle: Bundle; before: Record<string, string | null>; changed: string[] }
const MAX_BYTES = 1024 * 1024
const size = (value: string) => new TextEncoder().encode(value).length
function validate(entries: Entries) {
  for (const [key, raw] of Object.entries(entries)) {
    if (!Object.hasOwn(schemas, key)) throw new Error(`文件包含不支持的偏好键：${key}`)
    if (typeof raw !== 'string' || size(raw) > 256 * 1024) throw new Error(`偏好内容过大：${preferenceNames[key]}`)
    let value: unknown
    try { value = rawKeys.has(key) ? raw : JSON.parse(raw) } catch { throw new Error(`偏好 JSON 损坏：${preferenceNames[key]}`) }
    if (!schemas[key].safeParse(value).success) throw new Error(`偏好格式不兼容：${preferenceNames[key]}；原值保留。`)
  }
}
async function checksum(entries: Entries) {
  const stable = JSON.stringify(Object.keys(entries).sort().map(key => [key, entries[key]]))
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(stable))
  return [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('')
}
export async function exportPreferences(storage: Storage = localStorage) {
  const entries: Entries = {}
  const skipped: string[] = []
  for (const key of Object.keys(schemas)) {
    const raw = storage.getItem(key)
    if (raw === null) continue
    try { validate({ [key]: raw }); entries[key] = raw } catch { skipped.push(preferenceNames[key]) }
  }
  const bundle: Bundle = { format: 'trade-browser-preferences', version: 1, created_at: new Date().toISOString(), entries, sha256: await checksum(entries) }
  const content = JSON.stringify(bundle, null, 2)
  if (size(content) > MAX_BYTES) throw new Error('偏好文件超过 1 MiB，请减少本机保存的样本选择。')
  return { content, skipped, count: Object.keys(entries).length }
}
export async function previewPreferences(content: string, storage: Storage = localStorage): Promise<PreferencePreview> {
  if (size(content) > MAX_BYTES) throw new Error('偏好文件最多 1 MiB')
  const raw = JSON.parse(content)
  const envelope = z.object({ format: z.literal('trade-browser-preferences'), version: z.literal(1), created_at: z.iso.datetime(), entries: z.record(z.string(), z.string().max(256 * 1024)), sha256: z.string().regex(/^[a-f0-9]{64}$/) }).strict().safeParse(raw)
  if (!envelope.success) throw new Error('不是支持的 Trade 浏览器偏好文件')
  const bundle = envelope.data
  validate(bundle.entries)
  if (await checksum(bundle.entries) !== bundle.sha256) throw new Error('偏好文件校验失败，内容已变化')
  const before = Object.fromEntries(Object.keys(bundle.entries).map(key => [key, storage.getItem(key)]))
  return { bundle, before, changed: Object.keys(before).filter(key => before[key] !== bundle.entries[key]) }
}
export function applyPreferences(preview: PreferencePreview, storage: Storage = localStorage) {
  const keys = Object.keys(preview.bundle.entries)
  validate(preview.bundle.entries)
  if (keys.some(key => storage.getItem(key) !== preview.before[key])) throw new Error('本机偏好已被其他页面修改，请重新读取文件预览。')
  const written: string[] = []
  try {
    for (const key of preview.changed) { storage.setItem(key, preview.bundle.entries[key]); written.push(key) }
  } catch {
    let rollbackFailed = false
    for (const key of written.reverse()) {
      try {
        if (storage.getItem(key) !== preview.bundle.entries[key]) { rollbackFailed = true; continue }
        const old = preview.before[key]
        if (old === null) storage.removeItem(key); else storage.setItem(key, old)
      } catch { rollbackFailed = true }
    }
    throw new Error(rollbackFailed ? '写入失败且部分偏好未能回退，请保留导出文件并逐项核对。' : '浏览器存储不足或不可用，已回退本次写入。')
  }
  const theme = storage.getItem('trade-theme-mode') || storage.getItem('trade-theme') || 'light'
  const mode: 'light' | 'dark' | 'system' = theme === 'dark' || theme === 'system' ? theme : 'light'
  return { theme: mode,
    density: storage.getItem('trade-list-density') === 'compact' ? 'compact' as const : 'comfortable' as const }
}
