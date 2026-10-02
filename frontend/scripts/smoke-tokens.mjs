import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  const light = await page.evaluate(() => ({
    background: getComputedStyle(document.body).backgroundColor,
    sidebar: getComputedStyle(document.querySelector('.sidebar')).width,
    token: getComputedStyle(document.documentElement).getPropertyValue('--action-primary').trim(),
  }))
  if (light.background !== 'rgb(243, 247, 244)' || light.sidebar !== '248px' || light.token !== '#0a6b54')
    throw new Error(`light tokens failed: ${JSON.stringify(light)}`)
  await page.getByRole('button', { name: '深色' }).click()
  const dark = await page.evaluate(() => ({
    background: getComputedStyle(document.body).backgroundColor,
    token: getComputedStyle(document.documentElement).getPropertyValue('--action-primary').trim(),
  }))
  if (dark.background !== 'rgb(16, 26, 24)' || dark.token !== '#71d8b8')
    throw new Error(`dark tokens failed: ${JSON.stringify(dark)}`)
  await page.getByRole('button', { name: '跟随系统' }).click()
  await page.emulateMedia({ colorScheme: 'light' })
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark')
  await page.getByRole('button', { name: '紧凑列表' }).click()
  if (await page.evaluate(() => document.documentElement.dataset.density) !== 'compact') throw new Error('compact density failed')
  await page.reload()
  if (await page.evaluate(() => document.documentElement.dataset.density) !== 'compact') throw new Error('density preference not restored')
  console.log('browser smoke: generated tokens, system theme and list density')
} finally {
  await browser.close()
}
