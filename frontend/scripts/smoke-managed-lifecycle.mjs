import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'
import { mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { spawn } from 'node:child_process'
import net from 'node:net'

// This smoke owns both directories, the launcher pointer, and its server process.
// It never changes the user's launcher pointer or the normal smoke server on 8011.
const output = await mkdtemp(join(tmpdir(), 'trade-managed-ui-'))
const source = join(output, 'source'), target = join(output, 'target')
const pointer = join(output, 'launcher.json'), stop = join(output, 'stop')
const binding = net.createServer()
await new Promise(resolve => binding.listen(0, '127.0.0.1', resolve))
const port = binding.address().port
await new Promise(resolve => binding.close(resolve))
const base = `http://127.0.0.1:${port}`
const program = `import sys, threading, time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from trade_app.platform.launcher import ManagedLauncher
launcher=ManagedLauncher(Path(sys.argv[1]),port=int(sys.argv[2]),state_path=Path(sys.argv[3]),no_browser=True)
def cleanup_watch():
    deadline=time.monotonic()+120
    while not Path(sys.argv[5]).exists() and time.monotonic()<deadline:
        time.sleep(.1)
    launcher._terminate_owned()
threading.Thread(target=cleanup_watch,daemon=True).start()
raise SystemExit(launcher.run(Path(sys.argv[4])))
`
const server = spawn('python', ['-u', '-c', program, resolve('../backend'), String(port), pointer, source, stop], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
let serverOutput = ''
server.stdout.on('data', data => { serverOutput += data })
server.stderr.on('data', data => { serverOutput += data })
const exited = new Promise(resolve => server.on('exit', code => resolve(code)))
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  await expect.poll(async () => { try { return (await fetch(base + '/health')).ok } catch { return false } }, { timeout: 30000 }).toBe(true)
  const initial = await (await fetch(base + '/health')).json()
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  await page.goto(base + '/rebuild.html?page=settings')
  await page.getByRole('tab', { name: '存储与备份', exact: true }).click()
  const section = page.getByRole('region', { name: '服务与目录切换' })
  await expect(section.getByRole('button', { name: '校验切换目标' })).toBeVisible()
  await page.getByLabel('复制到空目录', { exact: true }).fill(target)
  await page.getByRole('button', { name: '预览复制范围' }).click()
  await page.getByRole('button', { name: '确认复制到空目录' }).click()
  await page.getByRole('button', { name: '使用刚恢复或复制的目录' }).click()
  await section.getByRole('button', { name: '校验切换目标' }).click()
  await expect(section.getByRole('heading', { name: '确认切换范围' })).toBeVisible()
  const confirm = section.getByRole('button', { name: '确认切换并重启' })
  await expect(confirm).toBeDisabled()
  await section.getByRole('checkbox').check()
  await confirm.click()
  await expect(section.getByText('切换完成：' + target, { exact: true })).toBeVisible({ timeout: 30000 })
  await expect(section.getByText('当前目录：' + target, { exact: true })).toBeVisible()
  const current = await (await fetch(base + '/health')).json()
  assert.notEqual(current.instance_id, initial.instance_id)
  assert.equal(JSON.parse(await readFile(pointer, 'utf8')).active_data_dir, target)
  assert((await readdir(join(source, 'recovery'))).some(name => name.endsWith('.zip')))
  assert.equal(await page.evaluate(() => sessionStorage.getItem('trade-rebuild:lifecycle-pending')), null)
  await page.setViewportSize({ width: 320, height: 850 })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
  await section.getByRole('button', { name: '准备退出应用' }).click()
  await section.getByRole('button', { name: '确认退出应用' }).click()
  const exitCode = await Promise.race([exited, new Promise(resolve => setTimeout(() => resolve('timeout'), 15000))])
  assert.equal(exitCode, 0, serverOutput)
  assert.deepEqual(errors, [])
  console.log('Managed UI smoke passed: isolated snapshot copy, reviewed target, restart/session reconnect, committed pointer, source recovery point, owned exit, 320px. Evidence:', output)
} finally {
  await writeFile(stop, 'stop only this test launcher')
  await browser.close()
  if (server.exitCode === null) await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 12000))])
  if (server.exitCode === null) throw new Error('Test launcher did not terminate its owned process: ' + output)
}
