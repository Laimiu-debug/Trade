/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyPreferences, exportPreferences, previewPreferences } from './browser-preferences'

function memoryStorage() {
  const values = new Map<string, string>()
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) }, clear: () => values.clear(), key: (i: number) => [...values.keys()][i] ?? null, get length() { return values.size } } satisfies Storage
}
beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })
afterEach(() => vi.unstubAllGlobals())
describe('browser preferences archive', () => {
  it('restores exact registered strings after a read-only preview without including drafts or credentials', async () => {
    const source = memoryStorage(), target = memoryStorage()
    source.setItem('trade-theme-mode', 'dark')
    source.setItem('trade-rebuild.research-tab.v1', '"matrix"')
    source.setItem('trade-rebuild.screener-last-run.v1', '"' + 'a'.repeat(64) + '"')
    source.setItem('trade-rebuild.ai-draft.secret', 'PRIVATE MESSAGE')
    source.setItem('unrelated-token', 'PRIVATE KEY')
    target.setItem('trade-theme-mode', 'light')
    target.setItem('trade-list-density', 'compact')
    const result = await exportPreferences(source)
    expect(result.count).toBe(3); expect(result.content).not.toContain('PRIVATE')
    const preview = await previewPreferences(result.content, target)
    expect(target.getItem('trade-theme-mode')).toBe('light')
    expect(applyPreferences(preview, target)).toEqual({ theme: 'dark', density: 'compact' })
    expect(target.getItem('trade-rebuild.research-tab.v1')).toBe('"matrix"')
    expect(target.getItem('trade-list-density')).toBe('compact')
  })
  it('rejects tampered, unsupported, oversized, path-like result IDs and invalid nested shapes', async () => {
    const source = memoryStorage(); source.setItem('trade-theme-mode', 'dark')
    const good = JSON.parse((await exportPreferences(source)).content)
    for (const entries of [ { 'trade-theme-mode': 'light' }, { 'trade-theme-mode': 'dark', auth: 'key' },
      { 'trade-rebuild.b1-last-run.v1': '"../settings"' }, { 'trade-rebuild.sentiment-valuation.v1': '{"scenarios":{}}' } ]) {
      await expect(previewPreferences(JSON.stringify({ ...good, entries }), memoryStorage())).rejects.toThrow()
    }
    await expect(previewPreferences(' '.repeat(1024 * 1024 + 1))).rejects.toThrow('1 MiB')
  })
  it('leaves incompatible source values in place and reports every skipped key', async () => {
    const source = memoryStorage(); source.setItem('trade-rebuild.screener-prefs.v1', '{"selectedIds":{}}')
    const result = await exportPreferences(source)
    expect(result.count).toBe(0); expect(result.skipped).toEqual(['漏斗 / B1 参数与样本选择'])
    expect(source.getItem('trade-rebuild.screener-prefs.v1')).toBe('{"selectedIds":{}}')
  })
  it('detects a changed target before writing any preferences', async () => {
    const source = memoryStorage(), target = memoryStorage(); source.setItem('trade-theme-mode', 'dark'); source.setItem('trade-list-density', 'compact')
    const preview = await previewPreferences((await exportPreferences(source)).content, target)
    target.setItem('trade-list-density', 'comfortable')
    expect(() => applyPreferences(preview, target)).toThrow('其他页面')
    expect(target.getItem('trade-theme-mode')).toBeNull()
    expect(target.getItem('trade-list-density')).toBe('comfortable')
  })
  it('rolls earlier writes back when quota fails on a later key', async () => {
    const source = memoryStorage(), target = memoryStorage(); source.setItem('trade-theme-mode', 'dark'); source.setItem('trade-list-density', 'compact')
    target.setItem('trade-theme-mode', 'light')
    const preview = await previewPreferences((await exportPreferences(source)).content, target)
    const set = target.setItem
    target.setItem = (key, value) => { if (key === 'trade-list-density') throw new Error('QuotaExceeded'); set(key, value) }
    expect(() => applyPreferences(preview, target)).toThrow('已回退')
    expect(target.getItem('trade-theme-mode')).toBe('light')
    expect(target.getItem('trade-list-density')).toBeNull()
  })
  it('allows large real dataset choices but caps archive and per-entry sizes', async () => {
    const source = memoryStorage(), scenarios = Array.from({ length: 50 }, (_, i) => ({ label: '场景'.repeat(20) + i,
      earnings_yi: '1', growth_rate_pct: 20, base_pe: 15, index_points: 3000, sentiment_coef: 1, actual_cap_yi: '' }))
    source.setItem('trade-rebuild.sentiment-valuation.v1', JSON.stringify({ symbol: '600000', datasetId: '', scenarios }))
    const result = await exportPreferences(source)
    expect(result.content.length).toBeGreaterThan(2048)
    expect((await previewPreferences(result.content, memoryStorage())).changed).toEqual(['trade-rebuild.sentiment-valuation.v1'])
  })
})
