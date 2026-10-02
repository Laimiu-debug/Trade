import { useEffect, useState } from 'react'

export type AIActivity = { running: boolean; accountId: string | null; runId: string | null }
export type AIHandoff = { page: string; accountId?: string; accountKind?: string; datasetId?: string; researchRunId?: string; artifact?: { type: 'valuation' | 'backtest' | 'portfolio'; id: string }; label?: string }
export const AI_HANDOFF_EVENT = 'trade-ai-handoff'
export const AI_ACTIVITY_EVENT = 'trade-ai-activity'
let waitingHandoff: AIHandoff | null = null
export function takeAIHandoff() { const value = waitingHandoff; waitingHandoff = null; return value }
export function sendAIHandoff(value: AIHandoff) {
  if (!value || typeof value.page !== 'string' || value.page.length > 80 ||
      ['accountId', 'accountKind', 'datasetId', 'researchRunId', 'label'].some(key => value[key as 'page'] !== undefined && (typeof value[key as 'page'] !== 'string' || value[key as 'page'].length > 256)) ||
      (value.artifact && (!['valuation', 'backtest', 'portfolio'].includes(value.artifact.type) || typeof value.artifact.id !== 'string' || !value.artifact.id || value.artifact.id.length > 128))) return
  waitingHandoff = { ...value, ...(value.artifact ? { artifact: { ...value.artifact } } : {}) }
  window.dispatchEvent(new CustomEvent(AI_HANDOFF_EVENT, { detail: waitingHandoff }))
}

export function aiPage(page: string): string {
  if (page === 'backtest' || page === 'backtests') return 'backtests'
  if (['review', 'period', 'trades', 'snapshots', 'flows', 'simulation', 'overview', 'reviews'].includes(page)) return 'reviews'
  if (['research', 'signals', 'events'].includes(page)) return 'research'
  return ['market', 'portfolio', 'valuation'].includes(page) ? page : 'generic'
}

export function GlobalAILauncher({ accountId, accountKind, page, datasetId, researchRunId, onOpen }: {
  accountId?: string; accountKind?: string; page: string; datasetId?: string | null; researchRunId?: string | null; onOpen: () => void;
}) {
  const [activity, setActivity] = useState<AIActivity>({ running: false, accountId: null, runId: null })
  useEffect(() => {
    const handle = (event: Event) => setActivity((event as CustomEvent<AIActivity>).detail)
    window.addEventListener(AI_ACTIVITY_EVENT, handle)
    return () => window.removeEventListener(AI_ACTIVITY_EVENT, handle)
  }, [])
  function open() {
    if (!activity.running && page !== 'ai') {
      const handoff: AIHandoff = { page: aiPage(page), accountId, accountKind,
        ...(page === 'market' && datasetId ? { datasetId } : {}),
        ...(page === 'research' && researchRunId ? { researchRunId } : {}) }
      sendAIHandoff(handoff)
    }
    onOpen()
  }
  return <button className="button secondary" type="button" onClick={open} aria-label={activity.running ? '查看运行中的 AI 助手' : '询问 AI 助手'}>{activity.running ? 'AI 生成中 · 查看 / 停止' : '询问 AI'}</button>
}

/** Page-local selection button: only explicit clicks hand off an existing ID. */
export function AIContextButton({ source, onOpen }: { source: AIHandoff; onOpen?: () => void }) {
  return <button className="button secondary" type="button" onClick={() => { sendAIHandoff(source); if (onOpen) onOpen(); else window.dispatchEvent(new Event('trade-ai-open')) }}>带入 AI 核对</button>
}
