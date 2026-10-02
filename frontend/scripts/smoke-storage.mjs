import assert from 'node:assert/strict'
import { chromium, expect } from '@playwright/test'
import { mkdtemp, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const base = process.env.TRADE_SMOKE_BASE_URL || 'http://127.0.0.1:8011'
const output = await mkdtemp(join(tmpdir(), 'trade-storage-smoke-'))
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  await page.goto(base + '/rebuild.html?page=settings')
  await page.getByRole('tab', { name: '存储与备份', exact: true }).click()
  await expect(page.getByRole('heading', { name: '数据与备份', exact: true, level: 2 })).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: '下载完整备份 ZIP', exact: true }).click()
  const archive = await download
  const contents = await readFile(await archive.path())
  assert.equal(contents.readUInt32LE(), 0x04034b50)
  await page.getByLabel('选择备份 ZIP', { exact: true }).setInputFiles({ name: 'backup.zip', mimeType: 'application/zip', buffer: contents })
  await page.getByRole('button', { name: '校验备份内容', exact: true }).click()
  await expect(page.getByText(/已校验 trade-rebuild-backup-v3/)).toBeVisible()
  await page.getByLabel('恢复到空目录', { exact: true }).fill(join(output, 'restored'))
  await page.getByRole('button', { name: '恢复到此空目录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '数据已写入独立目录', exact: true })).toBeVisible()
  assert((await readFile(join(output, 'restored', 'trade.sqlite'))).length > 0)
  const response = await page.request.get(base + '/api/v1/system/storage')
  assert(response.ok())
  const active = (await response.json()).data.data_dir
  assert.notEqual(active, join(output, 'restored'))
  await page.getByLabel('复制到空目录', { exact: true }).fill(join(output, 'copied'))
  await page.getByRole('button', { name: '预览复制范围', exact: true }).click()
  await expect(page.getByRole('button', { name: '确认复制到空目录', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '确认复制到空目录', exact: true }).click()
  await expect.poll(async () => (await page.getByRole('status').textContent()) || '').toContain(join(output, 'copied'))
  assert((await readFile(join(output, 'copied', 'trade.sqlite'))).length > 0)
  await page.setViewportSize({ width: 320, height: 850 })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
  assert.deepEqual(errors, [])
  console.log('Storage smoke passed: verified ZIP, restored and copied to isolated empty paths, active directory unchanged, mobile width. Temporary evidence:', output)
} finally { await browser.close() }
