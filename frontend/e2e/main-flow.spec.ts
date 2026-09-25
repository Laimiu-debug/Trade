import { expect, test } from '@playwright/test'

test('main flow: screener -> chart -> signals', async ({ page }) => {
  await page.goto('/screener')
  await expect(page.getByText('选股漏斗控制台')).toBeVisible()

  await page.getByRole('button', { name: '运行第1步' }).click()
  await expect(page.getByText(/第1步运行完成/)).toBeVisible()
  await page.getByRole('button', { name: '运行第2步' }).click()
  await expect(page.getByText(/第2步运行完成/)).toBeVisible()
  await page.getByRole('button', { name: '运行第3步' }).click()
  await expect(page.getByText(/第3步运行完成/)).toBeVisible()
  await page.getByRole('button', { name: '运行第4步' }).click()
  await expect(page.getByText(/第4步运行完成/)).toBeVisible()
  await expect(page.getByText('第4步通过')).toBeVisible()
  await expect(page.getByRole('button', { name: '看图标注' }).first()).toBeVisible()

  await page.getByRole('button', { name: '看图标注' }).first().click()
  await expect(page.getByText(/K线标注/)).toBeVisible()
  await page.getByRole('button', { name: '保存人工标注' }).click()

  await page.goto('/signals')
  await expect(page.getByRole('heading', { name: '待买信号' })).toBeVisible()
})
