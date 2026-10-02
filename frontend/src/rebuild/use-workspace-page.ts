import { useCallback, useEffect, useState } from 'react'

export function useWorkspacePage<T extends string>(key: string, values: readonly T[], fallback: T) {
  const read = useCallback(() => {
    const value = new URLSearchParams(window.location.search).get(key) as T
    return values.includes(value) ? value : fallback
  }, [key, values, fallback])
  const [current, setCurrent] = useState<T>(read)
  useEffect(() => {
    const onPop = () => setCurrent(read())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [read])
  const navigate = useCallback((next: T) => {
    const url = new URL(window.location.href)
    url.searchParams.set(key, next)
    if (url.href !== window.location.href) window.history.pushState(null, '', url)
    setCurrent(next)
  }, [key])
  return [current, navigate] as const
}
