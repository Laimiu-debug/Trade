import { expect, test } from '@playwright/test'

test('large event rule catalogs remain editable across viewport and theme changes', async ({ page }) => {
  const ruleOptions = Array.from({ length: 80 }, (_, index) => ({
    rule_key: `layout_rule_${index}`, label: `规则 ${index + 1}`, category: '布局验收',
    description: '用于验证大量可编辑规则在屏幕缩放后仍可操作。', value_type: 'number',
    min_value: 0, max_value: 10, step: 0.1, default_value: 1,
  }))
  const catalog = {
    active_profile_id: 'layout',
    metric_options: [{ metric_key: 'event_background_score', label: '背景分', description: '' }],
    rule_options: ruleOptions,
    profiles: [{
      profile_id: 'layout', name: '布局验收模板', description: '', score_mode: 'dimension_weighted',
      is_system: false, updated_at: '2026-10-06T00:00:00Z', dimensions: [],
      rule_values: ruleOptions.map(rule => ({ rule_key: rule.rule_key, value: 1 })),
    }],
  }
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  // Intercept this fixture before the test site's service worker handles fetch.
  await page.addInitScript(data => {
    const original = window.fetch.bind(window)
    window.fetch = (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href)
      if (url.pathname === '/api/event-judgment/profiles' && (init?.method ?? 'GET') === 'GET') {
        return Promise.resolve(new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } }))
      }
      return original(input, init)
    }
  }, catalog)
  await page.goto('/strategy/events')
  const rows = page.locator('.event-rule-row')
  await expect(rows).toHaveCount(80)
  const name = page.locator('.legacy-content input').first()
  await name.fill('缩放保留的模板草稿')
  const value = rows.first().getByRole('spinbutton')
  await value.fill('2.5')
  await value.blur()
  for (const width of [1440, 768, 375, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await expect(name).toHaveValue('缩放保留的模板草稿')
    await expect(value).toHaveValue('2.5')
    await expect(rows).toHaveCount(80)
  }
  await page.getByRole('button', { name: '切换深色主题', exact: true }).click()
  await expect(name).toHaveValue('缩放保留的模板草稿')
  await expect(value).toHaveValue('2.5')
  expect(errors).toEqual([])
})
