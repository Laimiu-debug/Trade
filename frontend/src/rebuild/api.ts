import type { ApiResponse } from './api-contracts.generated'

export type Account = { id: string; name: string; kind: string; currency: string; input_revision: number; frozen?: boolean; reset_group_id?: string | null }
export type Trade = { id: string; trade_date: string; symbol: string; name: string; side: 'buy' | 'sell'; quantity: number; price: string; fee: string; calculated_fee: string; fee_source: 'auto' | 'manual'; fee_rule_version: number | null; fee_breakdown: { commission: string; stamp: string; transfer: string } | null; note: string; revision: number }
export type PendingTrade = Trade & { account_id: string; source: string; source_text: string | null; status: string; confirmed_trade_id: string | null; duplicate_trade_ids: string[]; created_at: string }
export type Flow = { id: string; flow_date: string; kind: string; amount: string; note: string; revision: number }
export type SnapshotPosition = { symbol: string; name: string; quantity: number; market_value: string }
export type Snapshot = { id: string; snap_date: string; total_assets: string; available_cash: string | null; position_value: string | null; positions: SnapshotPosition[]; revision: number }
export type Review = { review_date: string; title: string; market_observation: string; decision_review: string; mistakes: string; tomorrow_plan: string; overall_summary: string; reflection: string; tags: string[]; next_market_forecast: string; next_watchlist: Array<{ code: string; name: string; condition: string; action: string }>; next_position_plan: string; next_risk_plan: string; next_position_rehearsal: Array<{ code: string; name: string; qty: number; note: string; price?: string }>; next_target_date: string | null; revision: number }
export type Analytics = { status: string; account_input_revision: number; projection_input_revision: number | null; error: string | null; result: {
  nav: { current: { nav: string | null; assets: string | null; date: string | null; quality: string; lit_count: number; next_level: number | null; next_threshold: string | null; next_target_assets: string | null; next_gap_amount: string | null; next_gap_pct: string | null }; target_config: { version: number; multiplier: string; node_count: number }; node_events: Array<{ date: string; level: number; kind: string; segment: number; target_version: number }>; first_achievements: Array<{ segment: number; level: number; first_lit_date: string; days_from_start: number; target_version: number }>; points: Array<{ date: string; nav: string | null; assets: string; drawdown_pct: string | null; quality: string }> };
  positions: Array<{ symbol: string; name: string; quantity: number; cost_basis: string }>;
  rounds: Array<{ id: string; symbol: string; name: string; start_date: string; end_date: string | null; pnl: string | null; status: string; trade_ids: string[]; reason?: string }>;
  anomalies: Array<{ trade_id: string; symbol: string; date: string; reason: string; attempted_quantity: number; available_quantity: number }>;
  trade_stats: { trade_count: number; closed_rounds: number; win_rate_pct: string | null; payoff_ratio: string | null; profit_factor: string | null; max_consecutive_wins: number; max_consecutive_losses: number }
} | null }
export type SimPortfolio = { account_id: string; as_of_date: string; initial_capital: string; cash: string; reserved_cash: string; available_cash: string; config: Record<string, string>; config_version: number; wallet_revision: number; frozen: boolean; reset_group_id: string | null; positions: Array<{ symbol: string; quantity: number; sellable_quantity: number; cost_basis: string }>; valuation_quality: string }
export type SimOrder = { id: string; symbol: string; side: 'buy' | 'sell'; quantity: number; limit_price: string; signal_date: string; submit_date: string; status: string; reserved_cash: string; revision: number; legacy_origin?: { import_id: string; source_order_id: string; historical_config_unknown: boolean; price_is_fill_reference: boolean } | null }
export type SimFill = { id: string; order_id: string; fill_date: string; fill_price: string; gross: string; commission: string; stamp: string; transfer: string; realized_pnl: string | null; price_source: string }

let csrf = ''
let expiresAt = 0
let sessionGeneration = 0
let connecting: Promise<void> | null = null

export function connect(): Promise<void> {
  if (connecting) return connecting
  if (csrf && expiresAt > Date.now()) return Promise.resolve()
  if (!connecting) {
    connecting = (async () => {
      const response = await fetch('/api/v1/session', { credentials: 'same-origin', cache: 'no-store' })
      if (!response.ok) throw new Error('无法连接本地服务')
      const session: ApiResponse<'/session', 'get'> = await response.json()
      if (!session.data?.csrf_token) throw new Error('本地服务未返回有效会话')
      csrf = session.data.csrf_token
      // Refresh just before expiry. Older servers without expiry metadata use
      // a short cache and still recover from an explicit authentication error.
      const seconds = session.data.expires_in_seconds
      const ttl = Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 60_000
      expiresAt = Date.now() + Math.max(0, ttl - Math.min(30_000, ttl / 10))
      sessionGeneration += 1
    })().finally(() => { connecting = null })
  }
  return connecting
}

async function waitForConnection(signal?: AbortSignal) {
  signal?.throwIfAborted()
  const pending = connect()
  if (!signal) return pending
  await new Promise<void>((resolve, reject) => {
    const aborted = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
    signal.addEventListener('abort', aborted, { once: true })
    pending.then(resolve, reject).finally(() => signal.removeEventListener('abort', aborted))
  })
  signal.throwIfAborted()
}

// Only rejected authentication requests are replayed. A network error or a
// failed/aborted successful response may already have written data and must
// never cause an automatic second submission.
async function authenticatedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const signal = init.signal ?? undefined
  await waitForConnection(signal)
  const headers = new Headers(init.headers)
  const writes = !['GET', 'HEAD', 'OPTIONS'].includes((init.method || 'GET').toUpperCase())
  if (writes && !headers.has('Idempotency-Key')) headers.set('Idempotency-Key', crypto.randomUUID())
  let generation = sessionGeneration
  for (let attempt = 0; ; attempt += 1) {
    signal?.throwIfAborted()
    if (writes) headers.set('X-CSRF-Token', csrf)
    const response = await fetch('/api/v1' + path, { ...init, headers, credentials: 'same-origin' })
    if (attempt || ![401, 403].includes(response.status)) return response
    const json = await response.clone().json().catch(() => null)
    const rejected = (response.status === 401 && json?.error?.code === 'SESSION_REQUIRED') ||
      (response.status === 403 && json?.error?.code === 'CSRF_REJECTED')
    if (!rejected) return response
    signal?.throwIfAborted()
    if (generation === sessionGeneration) { expiresAt = 0; csrf = '' }
    await waitForConnection(signal)
    generation = sessionGeneration
  }
}

function responseError(json: { error?: { message?: string; code?: string } } | null, status: number, action = '请求失败') {
  const error = new Error(json?.error?.message || `${action} (${status})`) as Error & { code?: string }
  error.code = json?.error?.code
  return error
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await authenticatedFetch(path, {
    method,
    headers: method === 'GET' ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const json = await response.json().catch(() => null)
  if (!response.ok) throw responseError(json, response.status)
  if (!json || !('data' in json)) throw new Error('服务返回了无法解析的响应')
  return json.data as T
}

export async function apiDownload(path: string, body: unknown, fallbackName: string): Promise<void> {
  const response = await authenticatedFetch(path, { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw responseError(error, response.status, '导出失败')
  }
  const blob = await response.blob()
  const filename = response.headers.get('Content-Disposition')?.match(/filename="([a-zA-Z0-9_.-]+)"/)?.[1] || fallbackName
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = filename; document.body.appendChild(link); link.click(); link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export type APIStreamEvent = { event: string; data: unknown }

export async function apiStream(path: string, body: unknown, onEvent: (event: APIStreamEvent) => void, signal?: AbortSignal): Promise<void> {
  const response = await authenticatedFetch(path, {
    method: 'POST', signal,
    headers: {
      'Content-Type': 'application/json', Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const json = await response.json().catch(() => null)
    throw responseError(json, response.status)
  }
  if (!response.body) throw new Error('服务未返回可读取的响应流')
  const reader = response.body.getReader()
  const aborted = () => { void reader.cancel(signal?.reason).catch(() => {}) }
  signal?.addEventListener('abort', aborted, { once: true })
  const decoder = new TextDecoder()
  let buffer = ''
  function emit(block: string) {
    let event = 'message'
    const lines: string[] = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).replace(/^ /, '')
      else if (line.startsWith('data:')) lines.push(line.slice(5).replace(/^ /, ''))
    }
    if (!lines.length) return
    const raw = lines.join('\n')
    let data: unknown = raw
    try { data = JSON.parse(raw) } catch { /* Plain text SSE data is valid. */ }
    onEvent({ event, data })
  }
  try {
    while (true) {
      signal?.throwIfAborted()
      const { value, done } = await reader.read()
      signal?.throwIfAborted()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let boundary: RegExpExecArray | null
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        emit(buffer.slice(0, boundary.index))
        buffer = buffer.slice(boundary.index + boundary[0].length)
      }
      if (done) { if (buffer.trim()) emit(buffer); break }
    }
  } catch (error) {
    await reader.cancel().catch(() => {})
    throw error
  } finally { signal?.removeEventListener('abort', aborted); reader.releaseLock() }
}

export async function apiUpload<T>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const form = new FormData()
  form.append('file', file, file.name)
  for (const [key, value] of Object.entries(fields)) form.append(key, value)
  const response = await authenticatedFetch(path, {
    method: 'POST',
    body: form,
  })
  const json = await response.json().catch(() => null)
  if (!response.ok) throw responseError(json, response.status, '上传失败')
  if (!json || !('data' in json)) throw new Error('服务返回了无法解析的响应')
  return json.data as T
}
