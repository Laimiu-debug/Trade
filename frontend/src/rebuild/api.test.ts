import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'

let transport: typeof import('./api')
const request = vi.fn<typeof fetch>()
const json = (data: unknown) => new Response(JSON.stringify({ data }), { headers: { 'Content-Type': 'application/json' } })
const rejected = (status: number, code: string) => new Response(JSON.stringify({ error: { code, message: code } }), { status, headers: { 'Content-Type': 'application/json' } })
const session = (token = 'first', seconds = 3600) => json({ csrf_token: token, expires_in_seconds: seconds })
const headers = (init?: RequestInit) => new Headers(init?.headers)
const calls = (path: string) => request.mock.calls.filter(([url]) => url === '/api/v1' + path)

beforeEach(async () => {
  vi.resetModules()
  request.mockReset()
  vi.stubGlobal('fetch', request)
  transport = await import('./api')
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('shares one bootstrap and caches only until the returned session expires', async () => {
  let clock = 1000
  vi.spyOn(Date, 'now').mockImplementation(() => clock)
  request.mockImplementation(async url => url === '/api/v1/session' ? session('first', 2) : json('ok'))
  await Promise.all([transport.connect(), transport.connect(), transport.api('/value')])
  await transport.connect()
  expect(calls('/session')).toHaveLength(1)
  clock = 2900
  await transport.api('/value')
  expect(calls('/session')).toHaveLength(2)
})

it('recovers an expired-cookie 401 and retries the rejected request once', async () => {
  let attempts = 0
  request.mockImplementation(async url => url === '/api/v1/session' ? session() : ++attempts === 1 ? rejected(401, 'SESSION_REQUIRED') : json('restored'))
  await expect(transport.api('/accounts')).resolves.toBe('restored')
  expect(calls('/session')).toHaveLength(2)
  expect(calls('/accounts')).toHaveLength(2)
})

it('shares concurrent CSRF recovery after another tab replaces the cookie and preserves each idempotency key', async () => {
  let resolveSession!: (value: Response) => void
  let bootstraps = 0
  request.mockImplementation(async (url, init) => {
    if (url === '/api/v1/session') return ++bootstraps === 1 ? session('old') : new Promise<Response>(resolve => { resolveSession = resolve })
    return headers(init).get('X-CSRF-Token') === 'old' ? rejected(403, 'CSRF_REJECTED') : json('saved')
  })
  await transport.connect()
  const first = transport.api('/one', 'POST', { value: 1 })
  const second = transport.api('/two', 'POST', { value: 2 })
  await waitFor(() => expect(calls('/session')).toHaveLength(2))
  resolveSession(session('new'))
  await expect(Promise.all([first, second])).resolves.toEqual(['saved', 'saved'])
  expect(calls('/session')).toHaveLength(2)
  for (const path of ['/one', '/two']) {
    const pair = calls(path)
    expect(pair).toHaveLength(2)
    expect(headers(pair[0][1]).get('Idempotency-Key')).toBe(headers(pair[1][1]).get('Idempotency-Key'))
    expect(headers(pair[1][1]).get('X-CSRF-Token')).toBe('new')
  }
  expect(headers(calls('/one')[0][1]).get('Idempotency-Key')).not.toBe(headers(calls('/two')[0][1]).get('Idempotency-Key'))
})

it.each([[401, 'SESSION_REQUIRED', 2], [403, 'CSRF_REJECTED', 2], [403, 'ACCESS_DENIED', 1], [500, 'INTERNAL_ERROR', 1]])('bounds recovery for %s %s', async (status, code, count) => {
  request.mockImplementation(async url => url === '/api/v1/session' ? session() : rejected(status as number, code as string))
  await expect(transport.api('/save', 'POST', { value: 1 })).rejects.toMatchObject({ code })
  expect(calls('/save')).toHaveLength(count as number)
  expect(calls('/session')).toHaveLength(count as number)
})

it('does not replay a write when the network fails after its outcome becomes unknown', async () => {
  request.mockImplementation(async url => { if (url === '/api/v1/session') return session(); throw new TypeError('connection lost') })
  await expect(transport.api('/save', 'POST', { value: 1 })).rejects.toThrow('connection lost')
  expect(calls('/save')).toHaveLength(1)
  expect(calls('/session')).toHaveLength(1)
})

it('reuses the upload body and idempotency key after an authentication rejection', async () => {
  let attempts = 0
  request.mockImplementation(async url => url === '/api/v1/session' ? session() : ++attempts === 1 ? rejected(401, 'SESSION_REQUIRED') : json('uploaded'))
  const file = new File(['test'], 'test.txt', { type: 'text/plain' })
  await expect(transport.apiUpload('/upload', file, { purpose: 'audit' })).resolves.toBe('uploaded')
  const pair = calls('/upload')
  expect(pair).toHaveLength(2)
  expect(pair[0][1]?.body).toBe(pair[1][1]?.body)
  expect((pair[1][1]?.body as FormData).get('purpose')).toBe('audit')
  expect(headers(pair[0][1]).get('Idempotency-Key')).toBe(headers(pair[1][1]).get('Idempotency-Key'))
  expect(headers(pair[1][1]).has('Content-Type')).toBe(false)
})

it('recovers a download and creates only one browser download', async () => {
  vi.useFakeTimers()
  const OriginalURL = URL
  const create = vi.fn(() => 'blob:audit')
  const revoke = vi.fn()
  vi.stubGlobal('URL', class extends OriginalURL { static createObjectURL = create; static revokeObjectURL = revoke })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  let attempts = 0
  request.mockImplementation(async url => url === '/api/v1/session' ? session() : ++attempts === 1 ? rejected(403, 'CSRF_REJECTED') : new Response('download'))
  await transport.apiDownload('/download', {}, 'audit.csv')
  expect(click).toHaveBeenCalledTimes(1)
  expect(create).toHaveBeenCalledTimes(1)
  const pair = calls('/download')
  expect(headers(pair[0][1]).get('Idempotency-Key')).toBe(headers(pair[1][1]).get('Idempotency-Key'))
  vi.runOnlyPendingTimers()
  expect(revoke).toHaveBeenCalledWith('blob:audit')
})

it('recovers a stream before it starts and parses fragmented SSE exactly once', async () => {
  let attempts = 0
  request.mockImplementation(async url => {
    if (url === '/api/v1/session') return session()
    if (++attempts === 1) return rejected(401, 'SESSION_REQUIRED')
    return new Response(new ReadableStream({ start(controller) {
      for (const part of ['event: token\r\ndata: {"text":', '"first"}\r\n\r\nevent: done\ndata: {}\n\n']) controller.enqueue(new TextEncoder().encode(part))
      controller.close()
    } }), { headers: { 'Content-Type': 'text/event-stream' } })
  })
  const events = vi.fn()
  await transport.apiStream('/stream', {}, events)
  expect(events.mock.calls.map(([event]) => event)).toEqual([{ event: 'token', data: { text: 'first' } }, { event: 'done', data: {} }])
  const pair = calls('/stream')
  expect(headers(pair[0][1]).get('Idempotency-Key')).toBe(headers(pair[1][1]).get('Idempotency-Key'))
})

it('aborts a waiting stream without cancelling a bootstrap shared with another caller', async () => {
  let resolveSession!: (value: Response) => void
  request.mockImplementation(async url => url === '/api/v1/session' ? new Promise<Response>(resolve => { resolveSession = resolve }) : json('ok'))
  const controller = new AbortController()
  const stream = transport.apiStream('/stream', {}, vi.fn(), controller.signal)
  const other = transport.api('/value')
  controller.abort()
  await expect(stream).rejects.toMatchObject({ name: 'AbortError' })
  resolveSession(session())
  await expect(other).resolves.toBe('ok')
  expect(calls('/session')).toHaveLength(1)
  expect(calls('/stream')).toHaveLength(0)
})

it('cancels a live stream on abort without reconnecting or replaying it', async () => {
  const cancel = vi.fn()
  request.mockImplementation(async url => url === '/api/v1/session' ? session() : new Response(new ReadableStream({ cancel })))
  const controller = new AbortController()
  const result = transport.apiStream('/stream', {}, vi.fn(), controller.signal)
  await waitFor(() => expect(calls('/stream')).toHaveLength(1))
  controller.abort()
  await expect(result).rejects.toMatchObject({ name: 'AbortError' })
  expect(cancel).toHaveBeenCalledTimes(1)
  expect(calls('/stream')).toHaveLength(1)
  expect(calls('/session')).toHaveLength(1)
})

it('does not replay an accepted stream whose reader later fails', async () => {
  request.mockImplementation(async url => url === '/api/v1/session' ? session() : new Response(new ReadableStream({ start(controller) { controller.error(new Error('stream interrupted')) } })))
  await expect(transport.apiStream('/stream', {}, vi.fn())).rejects.toThrow('stream interrupted')
  expect(calls('/stream')).toHaveLength(1)
  expect(calls('/session')).toHaveLength(1)
})
