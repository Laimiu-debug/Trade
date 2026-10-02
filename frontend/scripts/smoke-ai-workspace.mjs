import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { chromium, expect } from '@playwright/test'

// Use an isolated rebuild test server. The only model provider is this process's loopback fixture.
const base = process.env.TRADE_SMOKE_BASE_URL || 'http://127.0.0.1:8011'
const requests = []
const provider = createServer(async (request, response) => {
  let raw = ''
  for await (const chunk of request) raw += chunk
  const body = JSON.parse(raw)
  requests.push(body)
  assert.equal(request.url, '/v1/chat/completions')
  assert.equal(body.stream, true)
  response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
  const send = value => response.write(`data: ${JSON.stringify(value)}\n\n`)
  const slow = body.messages.some(item => item.content.includes('中止 smoke'))
  if (slow) {
    send({ choices: [{ delta: { content: '取消测试：已接收第一段。' } }] })
    const timer = setInterval(() => send({ choices: [{ delta: { content: '后续片段。' } }] }), 300)
    response.on('close', () => clearInterval(timer))
  } else {
    send({ choices: [{ delta: { content: '冻结资料已收到。' } }] })
    const timer = setTimeout(() => {
      send({ choices: [{ delta: { content: '<img src=x onerror=alert(1)>' } }] })
      send({ choices: [], usage: { prompt_tokens: 12, completion_tokens: 8, total_tokens: 20 } })
      response.end('data: [DONE]\n\n')
    }, 80)
    response.on('close', () => clearTimeout(timer))
  }
})
await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve))
const mockURL = `http://127.0.0.1:${provider.address().port}/v1`
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
let page, originalConfig, csrf
async function write(path, body, method = 'POST') {
  const response = await page.request.fetch(base + '/api/v1' + path, { method, data: body, headers: { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() } })
  assert(response.ok(), await response.text())
  return (await response.json()).data
}
async function get(path) {
  const response = await page.request.get(base + '/api/v1' + path)
  assert(response.ok(), await response.text())
  return (await response.json()).data
}
try {
  page = await browser.newPage({ viewport: { width: 1365, height: 900 }, timezoneId: 'Asia/Shanghai' })
  page.setDefaultTimeout(10000)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  csrf = (await get('/session')).csrf_token
  originalConfig = await get('/ai/config')
  const serial = Date.now()
  await write('/accounts', { name: `AI 浏览器验证 ${serial}` })
  const dataset = await write('/market/datasets', { symbol: 'sh600000', bars: [1, 2, 3].map(day => ({ event_date: `2025-01-0${day}`, available_at: `2025-01-0${day}T08:00:00+00:00`, open: '10', high: '13', low: '9', close: String(9 + day), volume: 1000 })) })
  await page.goto(`${base}/rebuild.html`)
  await page.getByRole('button', { name: /^AI/ }).click()
  await page.locator('summary').filter({ hasText: '全局模型配置' }).click()
  const settings = page.getByRole('heading', { name: '文本模型', exact: true }).locator('..')
  await settings.getByLabel('兼容接口基础地址').fill(mockURL)
  await settings.getByLabel('模型名称').fill('loopback-smoke')
  await settings.getByLabel('密钥环境变量引用').fill('')
  let responsePromise = page.waitForResponse(response => response.url().endsWith('/ai/config') && response.request().method() === 'PUT')
  await page.getByRole('button', { name: '保存模型配置', exact: true }).click()
  let response = await responsePromise; assert(response.ok(), await response.text())
  assert.equal(requests.length, 0)
  await settings.getByRole('button', { name: '测试文本模型连接', exact: true }).click()
  await expect.poll(() => requests.length).toBe(1)
  await expect(page.getByRole('button', { name: '测试文本模型连接', exact: true })).toBeEnabled()
  const testCalls = await get('/ai/runs')
  const connection = testCalls.find(item => item.kind === 'connection_test' && item.model === 'loopback-smoke')
  assert(connection)
  assert.equal(connection.status, 'completed')
  assert.equal(connection.usage.total_tokens, 20)
  await page.locator('summary').filter({ hasText: '提示词与本地覆盖' }).click()
  const builtIn = (await get('/ai/templates')).find(item => item.readonly)
  assert(builtIn)
  await page.getByLabel('查看或编辑提示词', { exact: true }).selectOption(builtIn.id)
  await expect(page.getByLabel('提示词正文', { exact: true })).toBeDisabled()
  await expect(page.getByLabel('提示词正文', { exact: true })).toHaveValue(builtIn.content)
  await page.getByRole('button', { name: '复制为本地提示词', exact: true }).click()
  await expect(page.getByLabel('提示词正文', { exact: true })).toBeEnabled()
  await page.getByLabel('提示词名称', { exact: true }).fill(`AI smoke 提示词 ${serial}`)
  await page.getByLabel('提示词正文', { exact: true }).fill('只使用本次选择的冻结资料，并注明证据日期。')
  responsePromise = page.waitForResponse(response => response.url().endsWith('/ai/templates') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '创建本地提示词', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const template = (await response.json()).data
  await page.getByLabel('新会话标题').fill(`AI smoke ${serial}`)
  responsePromise = page.waitForResponse(response => response.url().endsWith('/ai/sessions') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const session = (await response.json()).data
  await page.getByLabel('本地提示词 / 策略 playbook', { exact: true }).selectOption(template.id)
  await page.getByLabel('本次问题', { exact: true }).fill('请核对所选冻结资料。')
  await page.locator('summary').filter({ hasText: '选择本次携带的上下文' }).click()
  await page.getByLabel('数据截止时间（行情必填，浏览器本地时区）').fill('2025-01-02T17:00')
  await page.getByLabel(`携带行情 ${dataset.symbol} ${dataset.id.slice(0, 8)}`, { exact: true }).check()
  await page.getByLabel('手动补充的背景').fill('仅供本次研究的冻结背景。')
  responsePromise = page.waitForResponse(response => response.url().endsWith(`/ai/sessions/${session.id}/preview`))
  await page.getByRole('button', { name: '预览本次提示词', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const preview = (await response.json()).data
  assert.equal(requests.length, 1, 'preview must not call the provider')
  assert.equal(preview.context.datasets[0].bars.length, 2)
  assert.equal(preview.context.datasets[0].bars.at(-1).event_date, '2025-01-02')
  assert.equal(preview.template.id, template.id)
  responsePromise = page.waitForResponse(response => response.url().endsWith(`/ai/sessions/${session.id}/runs`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '确认发送到所选模型', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const call = (await response.json()).data
  assert.equal(call.input_sha256, preview.input_sha256)
  await expect.poll(async () => (await get(`/ai/runs/${call.id}`)).status).toBe('completed')
  await expect(page.getByLabel('本次问题', { exact: true })).toBeEnabled()
  await expect(page.locator('pre').filter({ hasText: '冻结资料已收到。' }).first()).toBeVisible()
  assert.equal(await page.locator('img[src="x"]').count(), 0, 'model output must stay plain text')
  const completed = await get(`/ai/runs/${call.id}`)
  assert.equal(completed.usage.total_tokens, 20)
  assert.equal(requests.length, 2)
  await page.getByLabel('本次问题', { exact: true }).fill('中止 smoke：持续输出以测试取消。')
  await page.getByRole('button', { name: '预览本次提示词', exact: true }).click()
  await expect(page.getByRole('button', { name: '确认发送到所选模型', exact: true })).toBeEnabled()
  responsePromise = page.waitForResponse(response => response.url().endsWith(`/ai/sessions/${session.id}/runs`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '确认发送到所选模型', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  const partial = (await response.json()).data
  await expect(page.locator('pre').filter({ hasText: '取消测试：已接收第一段。' }).first()).toBeVisible()
  await page.getByRole('button', { name: '停止生成', exact: true }).click()
  await expect.poll(async () => (await get(`/ai/runs/${partial.id}`)).status, { timeout: 15000 }).toBe('cancelled')
  const stopped = await get(`/ai/runs/${partial.id}`)
  assert(stopped.output.includes('取消测试：已接收第一段。'))
  assert.equal(stopped.usage.total_tokens, null)
  await page.reload()
  await page.getByRole('button', { name: /^AI/ }).click()
  await page.getByLabel('已有会话').selectOption(session.id)
  await expect(page.getByRole('heading', { name: session.title, exact: true })).toBeVisible()
  await expect(page.locator('article').filter({ hasText: '取消测试：已接收第一段。' })).toContainText('已停止')
  assert.equal(requests.length, 3, 'reload must not restart a model call')
  await page.setViewportSize({ width: 320, height: 780 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), false)
  await page.setViewportSize({ width: 1365, height: 900 })
  await page.locator('summary').filter({ hasText: '提示词与本地覆盖' }).click()
  await page.getByLabel('查看或编辑提示词', { exact: true }).selectOption(template.id)
  await page.getByRole('button', { name: '删除提示词', exact: true }).click()
  responsePromise = page.waitForResponse(response => new URL(response.url()).pathname.endsWith(`/ai/templates/${template.id}`) && response.request().method() === 'DELETE')
  await page.getByRole('button', { name: '确认删除提示词', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  assert(!(await get('/ai/templates')).some(item => item.id === template.id))
  await page.getByRole('button', { name: '删除会话', exact: true }).click()
  responsePromise = page.waitForResponse(response => new URL(response.url()).pathname.endsWith(`/ai/sessions/${session.id}`) && response.request().method() === 'DELETE')
  await page.getByRole('button', { name: '确认删除会话', exact: true }).click()
  response = await responsePromise; assert(response.ok(), await response.text())
  assert(!(await get('/ai/sessions')).some(item => item.id === session.id))
  assert.deepEqual(errors, [])
  console.log('browser smoke: loopback config/test, readonly playbook copy, session, bounded point-in-time preview without network, frozen request hash, SSE plain text/token usage, cancellation with partial history, reload without replay, revisioned template/session deletion, mobile width')
} finally {
  if (page && originalConfig) {
    try {
      csrf = (await get('/session')).csrf_token
      const current = await get('/ai/config')
      const clean = key => Object.fromEntries(['base_url', 'model', 'secret_ref'].map(field => [field, originalConfig[key][field]]))
      await write('/ai/config', { expected_revision: current.revision, text: clean('text'), vision: clean('vision'), temperature: originalConfig.temperature, max_tokens: originalConfig.max_tokens, timeout_seconds: originalConfig.timeout_seconds }, 'PUT')
    } catch (error) { console.error('Could not restore isolated smoke server AI config:', error.message) }
  }
  await browser.close()
  provider.closeAllConnections()
  await new Promise(resolve => provider.close(resolve))
}
