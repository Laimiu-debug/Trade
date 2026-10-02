import assert from 'node:assert/strict'
import { spawn, execFile } from 'node:child_process'
import { mkdtemp, mkdir, copyFile, readFile, readdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import net from 'node:net'
import { expect, request } from '@playwright/test'

assert.equal(process.platform, 'win32', 'This smoke verifies the actual Windows packaged process tree')
assert(process.env.TRADE_BUNDLE_EXE, 'Provide the specific built EXE; no existing service is used')
const evidence = await mkdtemp(join(tmpdir(), 'trade-launcher-cleanup-'))
const portable = join(evidence, '独立程序'), data = join(evidence, '独立数据'), runtime = join(evidence, 'runtime')
await Promise.all([mkdir(portable), mkdir(runtime)])
const exe = join(portable, 'TradeRebuild.exe')
await copyFile(resolve(process.env.TRADE_BUNDLE_EXE), exe)
const socket = net.createServer()
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve))
const port = socket.address().port
await new Promise(resolve => socket.close(resolve))
const base = `http://127.0.0.1:${port}`
const state = join(evidence, 'launcher.json'), processes = []
const env = { ...process.env, TEMP: runtime, TMP: runtime, TMPDIR: runtime, PYINSTALLER_STRICT_UNPACK_MODE: '1' }
let logs = ''
function start() {
  const child = spawn(exe, ['--no-browser', '--no-tray', '--port', String(port), '--data-dir', data, '--state-file', state],
    { cwd: dirname(exe), windowsHide: true, env, stdio: ['ignore', 'pipe', 'pipe'] })
  child.stdout.on('data', value => { logs += value }); child.stderr.on('data', value => { logs += value })
  child.completion = new Promise(resolve => child.once('exit', resolve))
  processes.push(child); return child
}
const client = await request.newContext({ baseURL: base })
async function health() { try { const r = await client.get('/health', { timeout: 1000 }); return r.ok() ? await r.json() : null } catch { return null } }
async function waitExit(child) {
  return Promise.race([child.completion, new Promise((_, reject) => setTimeout(() => reject(new Error('Owned EXE failed to exit')), 25000).unref())])
}
try {
  const first = start(), second = start()
  await expect.poll(async () => Boolean(await health()), { timeout: 50000 }).toBe(true)
  const identity = (await health()).instance_id
  await expect.poll(() => first.exitCode !== null || second.exitCode !== null, { timeout: 40000 }).toBe(true)
  const owner = first.exitCode === null ? first : second
  const duplicate = owner === first ? second : first
  assert.equal(duplicate.exitCode, 0)
  assert(logs.includes(`Trade already running: ${base}/rebuild.html`))
  assert.equal((await health()).instance_id, identity)
  const token = (await (await client.get('/api/v1/session')).json()).data.csrf_token
  const accountResponse = await client.post('/api/v1/accounts', { data: { name: '异常退出保留验收' },
    headers: { 'X-CSRF-Token': token, 'Idempotency-Key': crypto.randomUUID() } })
  assert(accountResponse.ok(), await accountResponse.text())
  const account = (await accountResponse.json()).data
  const record = JSON.parse(await readFile(join(data, '.trade-running.json'), 'utf8'))
  assert.equal(record.instance_id, identity)
  assert(Number.isSafeInteger(record.launcher_pid) && record.launcher_pid > 0)
  // Verify ownership through the isolated EXE path and this exact outer process.
  // End ONLY the Python launcher, so the OS Job must reap the server by itself.
  const escapedExe = exe.replaceAll("'", "''")
  const stop = `$p=Get-CimInstance Win32_Process -Filter 'ProcessId=${record.launcher_pid}'; if (!$p -or $p.ExecutablePath -ne '${escapedExe}' -or ($p.ProcessId -ne ${owner.pid} -and $p.ParentProcessId -ne ${owner.pid})) { throw 'Owned launcher identity mismatch' }; Stop-Process -Id ${record.launcher_pid} -Force`
  await new Promise((resolve, reject) => execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', stop],
    { windowsHide: true, timeout: 15000 }, error => error ? reject(error) : resolve()))
  await waitExit(owner)
  await expect.poll(async () => Boolean(await health()), { timeout: 10000 }).toBe(false)
  assert.deepEqual((await readdir(runtime)).filter(name => name.startsWith('_MEI')), [])
  const restarted = start()
  await expect.poll(async () => Boolean(await health()), { timeout: 50000 }).toBe(true)
  assert.notEqual((await health()).instance_id, identity)
  const nextToken = (await (await client.get('/api/v1/session')).json()).data.csrf_token
  const accounts = (await (await client.get('/api/v1/accounts')).json()).data
  assert(accounts.some(row => row.id === account.id && row.name === account.name))
  assert.equal((await client.get('/rebuild.html')).status(), 200)
  const exit = await client.post('/api/v1/system/lifecycle/exit', { data: {},
    headers: { 'X-CSRF-Token': nextToken, 'Idempotency-Key': crypto.randomUUID() } })
  assert.equal(exit.status(), 202)
  assert.equal(await waitExit(restarted), 0)
  await expect.poll(async () => Boolean(await health()), { timeout: 10000 }).toBe(false)
  assert.deepEqual((await readdir(runtime)).filter(name => name.startsWith('_MEI')), [])
  assert(!(await readdir(data)).includes('.trade-running.json'))
  await writeFile(join(evidence, 'result.json'), JSON.stringify({ status: 'passed', owner: record,
    checks: ['simultaneous_double_launch_reused', 'kill_launcher_only_reaps_server', 'port_and_data_lock_released',
      'fresh_restart_preserves_account', 'normal_exit_clears_discovery', 'onefile_extraction_cleaned'] }, null, 2))
  console.log(JSON.stringify({ status: 'passed', evidence }))
} catch (error) { console.error(evidence, logs); throw error }
finally {
  await client.dispose()
  for (const child of processes) if (child.exitCode === null && child.pid) {
    await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, resolve))
  }
}
