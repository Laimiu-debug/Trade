import { chromium } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const tokens = JSON.parse(await readFile(new URL('../../docs/design-tokens.json', import.meta.url), 'utf8'))
const rgb = hex => `rgb(${[1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16)).join(', ')})`

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  await page.goto(`${process.env.TRADE_UI_BASE_URL || 'http://127.0.0.1:8011'}/rebuild.html`)
  await page.evaluate(() => { localStorage.setItem('trade-theme-mode', 'light'); localStorage.removeItem('trade-list-density') })
  await page.reload()
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'light')
  const light = await page.evaluate(() => ({
    background: getComputedStyle(document.body).backgroundColor,
    sidebar: getComputedStyle(document.querySelector('.sidebar')).width,
    token: getComputedStyle(document.documentElement).getPropertyValue('--action-primary').trim(),
  }))
  if (light.background !== rgb(tokens.themes.light.color['bg.canvas']) || light.sidebar !== `${tokens.sizePx.sidebar}px` || light.token !== tokens.themes.light.color['action.primary'])
    throw new Error(`light tokens failed: ${JSON.stringify(light)}`)
  await page.getByRole('button', { name: '深色' }).click()
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark')
  const dark = await page.evaluate(() => ({
    background: getComputedStyle(document.body).backgroundColor,
    token: getComputedStyle(document.documentElement).getPropertyValue('--action-primary').trim(),
  }))
  if (dark.background !== rgb(tokens.themes.dark.color['bg.canvas']) || dark.token !== tokens.themes.dark.color['action.primary'])
    throw new Error(`dark tokens failed: ${JSON.stringify(dark)}`)
  await page.getByRole('button', { name: '跟随系统' }).click()
  await page.emulateMedia({ colorScheme: 'light' })
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark')
  if (await page.evaluate(() => document.documentElement.dataset.density) !== 'compact') throw new Error('default density should be compact')
  await page.getByRole('button', { name: '舒适列表' }).click()
  if (await page.evaluate(() => document.documentElement.dataset.density) !== 'comfortable') throw new Error('comfortable density failed')
  await page.getByRole('button', { name: '紧凑列表' }).click()
  if (await page.evaluate(() => document.documentElement.dataset.density) !== 'compact') throw new Error('compact density failed')
  await page.reload()
  if (await page.evaluate(() => document.documentElement.dataset.density) !== 'compact') throw new Error('density preference not restored')
  console.log('browser smoke: generated tokens, system theme and list density')
} finally {
  await browser.close()
}
