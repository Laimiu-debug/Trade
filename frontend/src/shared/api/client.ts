import type { ApiErrorPayload } from '@/types/contracts'

const REQUEST_TIMEOUT = 10_000
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
const DEFAULT_ERROR_MESSAGE = '请求失败'

export function buildApiUrl(path: string) {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path
  }
  return `${API_BASE_URL}${path}`
}

export class ApiError extends Error {
  code: string
  degraded?: boolean
  degraded_reason?: string
  trace_id?: string

  constructor(payload: ApiErrorPayload) {
    const message = typeof payload?.message === 'string' && payload.message.trim()
      ? payload.message
      : DEFAULT_ERROR_MESSAGE
    super(message)
    this.code = typeof payload?.code === 'string' && payload.code.trim()
      ? payload.code
      : 'HTTP_UNKNOWN'
    this.degraded = payload.degraded
    this.degraded_reason = payload.degraded_reason
    this.trace_id = payload.trace_id
  }
}

function isAbortError(error: unknown) {
  return (
    (error instanceof DOMException && error.name === 'AbortError')
    || (error instanceof Error && error.name === 'AbortError')
  )
}

async function withTimeout(input: RequestInfo | URL, init: RequestInit, timeoutMs: number) {
  if (!timeoutMs || timeoutMs <= 0) {
    return fetch(input, init)
  }
  const controller = new AbortController()
  let timedOut = false

  const onExternalAbort = () => controller.abort()
  if (init.signal) {
    if (init.signal.aborted) {
      controller.abort()
    } else {
      init.signal.addEventListener('abort', onExternalAbort, { once: true })
    }
  }

  const id = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  try {
    return await fetch(input, {
      ...init,
      signal: controller.signal,
    })
  } catch (error) {
    if (timedOut && isAbortError(error)) {
      const timeoutError = new Error('REQUEST_TIMEOUT')
      timeoutError.name = 'TimeoutError'
      throw timeoutError
    }
    throw error
  } finally {
    clearTimeout(id)
    if (init.signal) {
      init.signal.removeEventListener('abort', onExternalAbort)
    }
  }
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
) {
  const { timeoutMs = REQUEST_TIMEOUT, ...requestInit } = init
  const requestPath = buildApiUrl(path)

  let response: Response
  try {
    response = await withTimeout(requestPath, requestInit, timeoutMs)
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }
    if (error instanceof Error && error.name === 'TimeoutError') {
      throw new ApiError({
        code: 'REQUEST_TIMEOUT',
        message: `请求超时（${Math.ceil(timeoutMs / 1000)}秒）`,
      })
    }
    if (isAbortError(error)) {
      throw new ApiError({
        code: 'REQUEST_ABORTED',
        message: '请求已取消',
      })
    }
    throw error
  }

  if (!response.ok) {
    let payload: ApiErrorPayload = {
      code: `HTTP_${response.status}`,
      message: DEFAULT_ERROR_MESSAGE,
    }
    try {
      const raw = await response.json() as unknown
      if (raw && typeof raw === 'object') {
        const body = raw as Record<string, unknown>
        const code = typeof body.code === 'string' && body.code.trim()
          ? body.code
          : `HTTP_${response.status}`
        const message = typeof body.message === 'string' && body.message.trim()
          ? body.message
          : typeof body.detail === 'string' && body.detail.trim()
            ? body.detail
            : response.statusText || DEFAULT_ERROR_MESSAGE
        payload = {
          code,
          message,
          degraded: typeof body.degraded === 'boolean' ? body.degraded : undefined,
          degraded_reason: typeof body.degraded_reason === 'string' ? body.degraded_reason : undefined,
          trace_id: typeof body.trace_id === 'string' ? body.trace_id : undefined,
        }
      } else {
        payload = {
          code: `HTTP_${response.status}`,
          message: response.statusText || DEFAULT_ERROR_MESSAGE,
        }
      }
    } catch {
      payload = {
        code: `HTTP_${response.status}`,
        message: response.statusText,
      }
    }
    throw new ApiError(payload)
  }

  return (await response.json()) as T
}
