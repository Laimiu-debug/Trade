import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'

export type ArtifactSource = { type: 'valuation' | 'backtest' | 'portfolio'; id: string }
export type AIDraft = { message: string; manual: string; datasetIds: string[]; runIds: string[]; artifactSources: ArtifactSource[]; decisionAt: string; accountDate: string; strict: boolean; templateId: string; channel: 'text' | 'vision'; page: string }
export const emptyAIDraft = (): AIDraft => ({ message: '', manual: '', datasetIds: [], runIds: [], artifactSources: [], decisionAt: '', accountDate: '', strict: true, templateId: '', channel: 'text', page: 'generic' })
export const aiDraftKey = (scope: string, sessionId: string | null) => `trade-ai-draft.v1:${scope || 'unbound'}:${sessionId || 'new'}`
type Saved = { revision: number; data: AIDraft }
const plain = (value: unknown) => JSON.stringify(value)

function parse(raw: string | null): Saved | null {
  if (!raw) return null
  const value = JSON.parse(raw) as Saved
  const data = value.data
  if (!Number.isSafeInteger(value.revision) || value.revision < 1 || !data ||
      ['message', 'manual'].some(key => typeof data[key as 'message'] !== 'string' || data[key as 'message'].length > 12000) ||
      !Array.isArray(data.datasetIds) || data.datasetIds.length > 5 || data.datasetIds.some(id => typeof id !== 'string') ||
      !Array.isArray(data.runIds) || data.runIds.length > 10 || data.runIds.some(id => typeof id !== 'string') ||
      !Array.isArray(data.artifactSources) || data.artifactSources.length > 3 || data.artifactSources.some(item => !item || !['valuation', 'backtest', 'portfolio'].includes(item.type) || typeof item.id !== 'string') ||
      typeof data.strict !== 'boolean' || !['text', 'vision'].includes(data.channel) ||
      ['decisionAt', 'accountDate', 'templateId', 'page'].some(key => typeof data[key as 'message'] !== 'string' || data[key as 'message'].length > 128)) throw new Error('本机 AI 草稿格式无效；当前输入保留')
  return { revision: value.revision, data: { ...emptyAIDraft(), ...data } }
}

function load(key: string) {
  try { const raw = localStorage.getItem(key); const saved = parse(raw); return { key, base: raw, revision: saved?.revision || 0, data: saved?.data || emptyAIDraft(), error: '' } }
  catch { return { key, base: null, revision: 0, data: emptyAIDraft(), error: '无法读取本机 AI 草稿，请保留当前输入并检查浏览器存储' } }
}

export function seedAIDraft(scope: string, sessionId: string | null, data: AIDraft) {
  const key = aiDraftKey(scope, sessionId)
  const before = parse(localStorage.getItem(key))
  if (before && plain(before.data) !== plain(emptyAIDraft())) throw new Error('目标会话已有本机草稿，不能自动覆盖')
  localStorage.setItem(key, plain({ revision: (before?.revision || 0) + 1, data }))
}

export function useAIDraft(scope: string, sessionId: string | null) {
  const key = aiDraftKey(scope, sessionId)
  const [state, setState] = useState(() => load(key))
  const [conflict, setConflict] = useState<{ raw: string | null; data: AIDraft | null } | null>(null)
  if (state.key !== key) { setState(load(key)); setConflict(null) }
  useEffect(() => {
    if (state.key !== key || conflict || state.error) return
    const prior = state.base ? parse(state.base)?.data : emptyAIDraft()
    if (plain(prior) === plain(state.data)) return
    try {
      const actual = localStorage.getItem(key)
      if (actual !== state.base) { setConflict({ raw: actual, data: parse(actual)?.data || null }); return }
      const raw = plain({ revision: state.revision + 1, data: state.data })
      localStorage.setItem(key, raw)
      setState(current => current.key === key ? { ...current, base: raw, revision: current.revision + 1 } : current)
    } catch { setState(current => ({ ...current, error: '本机 AI 草稿保存失败，当前输入仍在；请复制保留后检查浏览器存储' })) }
  }, [key, state, conflict])
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.key !== key || event.newValue === state.base) return
      try { setConflict({ raw: event.newValue, data: parse(event.newValue)?.data || null }) }
      catch { setConflict({ raw: event.newValue, data: null }) }
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [key, state.base])
  const setData: Dispatch<SetStateAction<AIDraft>> = value => setState(current => ({ ...current, data: typeof value === 'function' ? value(current.data) : value }))
  function field<K extends keyof AIDraft>(name: K): Dispatch<SetStateAction<AIDraft[K]>> {
    return value => setData(current => ({ ...current, [name]: typeof value === 'function' ? (value as (previous: AIDraft[K]) => AIDraft[K])(current[name]) : value }))
  }
  function resolve(useOther: boolean) {
    try {
      const raw = localStorage.getItem(key)
      const latest = parse(raw)
      setState(current => ({ ...current, base: raw, revision: latest?.revision || 0, data: useOther ? latest?.data || emptyAIDraft() : current.data, error: '' }))
      setConflict(null)
    } catch { setState(current => ({ ...current, error: '无法读取另一页面的草稿，请复制当前输入保留' })) }
  }
  return { data: state.data, setData, field, conflict, resolve, error: state.error, revision: state.revision }
}
