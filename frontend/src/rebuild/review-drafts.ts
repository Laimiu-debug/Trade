import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

type BufferState<T> = { key: string; value: T | null; raw: string | null; base: string | null; warning: string; conflict: boolean }
// A failed browser write must still survive in-app navigation. It cannot survive closing the browser.
const unsaved = new Map<string, { raw: string; base: string | null }>()

export function clearReviewDraftMemoryForTests() { unsaved.clear() }

export function useReviewBuffer<T>(key: string, parse: (raw: string) => T) {
  function load(): BufferState<T> {
    const memory = unsaved.get(key)
    try {
      const raw = localStorage.getItem(key)
      if (memory) return { key, value: parse(memory.raw), raw: memory.raw, base: memory.base, warning: '这份草稿暂存在当前应用内存；请保存正式正文或复制保留后再关闭浏览器。', conflict: raw !== memory.base }
      return { key, value: raw ? parse(raw) : null, raw, base: raw, warning: '', conflict: false }
    } catch {
      return { key, value: memory ? parse(memory.raw) : null, raw: memory?.raw || null, base: memory?.base || null,
        warning: '本机草稿无法读取或格式不完整；原存储未删除。请核对后选择保留本页内容。', conflict: true }
    }
  }
  const [state, setState] = useState<BufferState<T>>(load)
  const ref = useRef(state)
  if (state.key !== key) setState(load())
  // Publish only committed scope changes to asynchronous save/storage handlers.
  useLayoutEffect(() => { ref.current = state }, [state])
  const replace = useCallback((next: BufferState<T>) => { ref.current = next; setState(next) }, [])
  function write(value: T, force = false) {
    const current = ref.current
    if (current.key !== key) return
    const raw = JSON.stringify(value)
    let base = current.base
    try {
      const actual = localStorage.getItem(key)
      if (!force && (current.conflict || actual !== base)) {
        unsaved.set(key, { raw, base })
        replace({ ...current, value, raw, conflict: true }); return
      }
      base = actual
      localStorage.setItem(key, raw)
      unsaved.delete(key)
      replace({ key, value, raw, base: raw, warning: '', conflict: false })
    } catch {
      unsaved.set(key, { raw, base })
      replace({ key, value, raw, base, conflict: false, warning: '本机存储写入失败；草稿暂存在当前应用内存。请保存正式正文或复制保留后再关闭浏览器。' })
    }
  }
  function clear(expected: string | null) {
    const current = ref.current
    if (current.key !== key || current.raw !== expected || current.conflict) return false
    try {
      const actual = localStorage.getItem(key)
      if (actual !== current.base) { replace({ ...current, conflict: true }); return false }
      localStorage.removeItem(key); unsaved.delete(key)
      replace({ key, value: null, raw: null, base: null, warning: '', conflict: false }); return true
    } catch { replace({ ...current, warning: '正文已保存，但本机草稿清理失败；重新打开时请核对正式版本。' }); return false }
  }
  function resolve(keepLocal: boolean) {
    const current = ref.current
    if (keepLocal && current.value) { write(current.value, true); return current.value }
    try {
      const raw = localStorage.getItem(key), value = raw ? parse(raw) : null
      unsaved.delete(key); replace({ key, value, raw, base: raw, warning: '', conflict: false }); return value
    } catch { replace({ ...current, warning: '另一页面的草稿无法读取，当前内容保留；可复制后明确保留本页草稿。' }); return undefined }
  }
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      const current = ref.current
      if ((event.key !== key && event.key !== null) || event.newValue === current.base) return
      if (current.raw) unsaved.set(key, { raw: current.raw, base: current.base })
      replace({ ...current, conflict: true })
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [key, replace])
  return { ...state, get: () => ref.current, write, clear, resolve }
}
