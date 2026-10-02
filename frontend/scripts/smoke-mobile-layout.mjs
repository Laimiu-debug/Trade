import { chromium } from '@playwright/test'

const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 320, height: 720 } })
  await page.goto('http://127.0.0.1:8011/rebuild.html')
  await page.getByRole('button', { name: '总览', exact: true }).waitFor()
  if (await page.getByRole('heading', { name: '创建第一个账户' }).isVisible()) {
    await page.getByRole('textbox', { name: '账户名称' }).fill('窄屏验收')
    await page.getByRole('button', { name: '创建实盘账户' }).click()
  }
  const pages = ['总览', '交易流水', '资金流水', '资产快照', '每日复盘', '周期复盘', '行情样本', '情绪估值', '分享卡', '策略研究', '历史回测', '灵感闪记']
  for (const label of pages) {
    await page.getByRole('button', { name: label, exact: true }).click()
    const sizes = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
      wide: [...document.querySelectorAll('body *')].filter(item => item.getBoundingClientRect().right > innerWidth)
        .slice(0, 8).map(item => `${item.tagName}.${item.className}: ${Math.round(item.getBoundingClientRect().right)}`) }))
    if (sizes.scroll > sizes.viewport + 1) throw new Error(`${label} 横向溢出: ${JSON.stringify(sizes)}`)
  }
  page.on('dialog', dialog => dialog.accept(dialog.message().includes('名称') ? '窄屏模拟' : '100000'))
  await page.getByRole('button', { name: '新增模拟' }).click()
  await page.getByRole('button', { name: '模拟交易', exact: true }).waitFor()
  for (const label of ['模拟交易', '行情样本', '情绪估值', '分享卡', '策略研究', '历史回测', '灵感闪记']) {
    await page.getByRole('button', { name: label, exact: true }).click()
    const sizes = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth }))
    if (sizes.scroll > sizes.viewport + 1) throw new Error(`${label} 横向溢出: ${JSON.stringify(sizes)}`)
  }
  console.log('browser smoke: 320px real and simulation pages have no page-level horizontal overflow')
} finally { await browser.close() }
