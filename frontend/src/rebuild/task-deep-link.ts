import { useEffect, useEffectEvent } from 'react'

export function useTaskDeepLink(kind: string, open: (id: string) => Promise<void>, selector: string, onError: (message: string) => void) {
  const openCurrent = useEffectEvent(open)
  const reportError = useEffectEvent(onError)
  useEffect(() => {
    let active = true
    let lastTask = ''
    function changed() {
      const task = new URLSearchParams(window.location.search).get('task') || ''
      const match = task.match(/^([a-z_]+):((?:[a-f0-9]{32}|[a-f0-9]{64}))$/)
      if (match?.[1] !== kind) { lastTask = ''; return }
      if (task === lastTask) return
      lastTask = task
      openCurrent(match[2]).then(() => { if (active) requestAnimationFrame(() => { if (active) document.querySelector(selector)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }) }).catch(err => { if (active) reportError(err instanceof Error ? err.message : String(err)) })
    }
    changed()
    window.addEventListener('popstate', changed)
    window.addEventListener('research-navigation', changed)
    return () => { active = false; window.removeEventListener('popstate', changed); window.removeEventListener('research-navigation', changed) }
  }, [kind, selector])
}
