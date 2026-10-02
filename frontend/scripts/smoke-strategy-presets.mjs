import { chromium, expect } from '@playwright/test'

const base = 'http://127.0.0.1:8011'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(`${base}/rebuild.html`)
  await page.waitForLoadState('networkidle')
  if (await page.getByRole('heading', { name: '创建第一个账户' }).count()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('参数预设验收')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  await page.waitForFunction(() => Boolean(document.querySelector('.account-select select')?.value))
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  const research = page.getByRole('heading', { name: '固定样本信号', exact: true }).locator('..')
  await research.locator('label').filter({ has: page.getByText('策略', { exact: true }) }).locator('select').selectOption('relative_strength_breakout_v1')
  const panel = page.locator('details[aria-label="策略参数预设"]').first()
  await panel.locator('summary').first().click()
  const title = `预设验收 ${Date.now()}`
  await panel.getByLabel('预设名称', { exact: true }).fill(title)
  const createResponse = page.waitForResponse(response => response.url() === `${base}/api/v1/research/presets` && response.request().method() === 'POST')
  await panel.getByRole('button', { name: '保存当前参数为新预设', exact: true }).click()
  const created = await createResponse
  if (!created.ok()) throw new Error(await created.text())
  const preset = (await created.json()).data
  await expect(panel.locator('label').filter({ has: page.getByText('已保存参数预设', { exact: true }) }).locator('select')).toHaveValue(preset.id)
  await panel.getByRole('button', { name: '收藏预设', exact: true }).click()
  await expect(panel.getByRole('button', { name: '取消收藏预设', exact: true })).toBeEnabled()
  await panel.getByRole('checkbox', { name: '仅显示收藏', exact: true }).check()
  await expect(panel.locator('label').filter({ has: page.getByText('已保存参数预设', { exact: true }) }).locator('select')).toHaveValue(preset.id)

  await panel.locator('summary').filter({ hasText: '参数建议与人工确认' }).click()
  await panel.getByLabel('参数建议 JSON', { exact: true }).fill('{"min_ret40":"0.31"}')
  await panel.getByRole('button', { name: '预览 JSON 参数建议', exact: true }).click()
  const diff = panel.getByRole('region', { name: '参数建议差异', exact: true })
  await expect(diff.getByRole('cell', { name: '0.31', exact: true })).toBeVisible()
  const before = await (await page.request.get(`${base}/api/v1/research/presets/${preset.id}`)).json()
  if (before.data.params.min_ret40 === '0.31') throw new Error('Preview unexpectedly applied parameters')
  await diff.getByRole('button', { name: '确认建议并保存预设', exact: true }).click()
  await expect(diff.getByText(/已确认 · 版本 3/)).toBeVisible()
  await panel.getByRole('button', { name: '预览填入表单', exact: true }).click()
  const formDiff = panel.getByRole('region', { name: '填入表单差异', exact: true })
  await expect(formDiff.getByRole('cell', { name: '0.31', exact: true })).toBeVisible()
  await formDiff.getByRole('button', { name: '确认填入当前表单', exact: true }).click()
  await expect(research.getByRole('spinbutton', { name: /40.*日.*涨幅/ })).toHaveValue('0.31')

  await panel.getByRole('button', { name: '查看预设修订', exact: true }).click()
  await expect(panel.getByRole('region', { name: '预设修订历史', exact: true }).locator('summary')).toHaveCount(3)
  await panel.getByRole('button', { name: '生成参数分享码', exact: true }).click()
  await expect(panel.locator('label').filter({ has: page.getByText('参数分享码', { exact: true }) }).locator('textarea')).toHaveValue(/^TRADE-PRESET-1\./)
  const code = await panel.locator('label').filter({ has: page.getByText('参数分享码', { exact: true }) }).locator('textarea').inputValue()
  await panel.locator('summary').filter({ hasText: '导入参数分享码' }).click()
  await panel.getByLabel('待导入参数分享码', { exact: true }).fill(code)
  await panel.getByRole('button', { name: '检查分享码并预览差异', exact: true }).click()
  await panel.getByLabel('导入预设名称', { exact: true }).fill(`${title} · 导入`)
  const importedResponse = page.waitForResponse(response => response.url() === `${base}/api/v1/research/presets` && response.request().method() === 'POST')
  await panel.getByRole('button', { name: '确认另存导入预设', exact: true }).click()
  const imported = (await (await importedResponse).json()).data
  await expect(panel.locator('label').filter({ has: page.getByText('已保存参数预设', { exact: true }) }).locator('select')).toHaveValue(imported.id)
  if (imported.id === preset.id || imported.params.min_ret40 !== '0.31') throw new Error('Share import did not create the expected independent preset')

  await panel.getByRole('button', { name: '删除预设', exact: true }).click()
  await panel.getByRole('button', { name: '确认删除预设', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('预设已删除')
  const history = await (await page.request.get(`${base}/api/v1/research/presets/${imported.id}/history`)).json()
  if (history.data[0].action !== 'delete' || history.data.length !== 2) throw new Error('Deleted preset lost audit history')
  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: '策略研究', exact: true }).click()
  await page.getByRole('navigation', { name: '研究子页面' }).getByRole('link', { name: '单股研究', exact: true }).click()
  await panel.locator('summary').first().click()
  await panel.locator('label').filter({ has: page.getByText('已保存参数预设', { exact: true }) }).locator('select').selectOption(preset.id)
  await expect(panel.getByRole('button', { name: '取消收藏预设', exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '预览填入表单', exact: true }).click()
  await expect(formDiff.getByRole('cell', { name: '0.31', exact: true })).toBeVisible()
  if (errors.length) throw new Error(errors.join('\n'))
  console.log('browser smoke: presets save/favorite, explicit proposal diff/apply, form apply, immutable revisions, share import, delete history and reload')
} finally {
  await browser.close()
}
