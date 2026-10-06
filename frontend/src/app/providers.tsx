import { useEffect, useState, type PropsWithChildren } from 'react'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { workspaceTheme } from '@/shared/theme/theme'
import { readWorkspaceTheme, WorkspaceThemeContext } from '@/shared/theme/theme-context'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 45_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
})

export function AppProviders({ children }: PropsWithChildren) {
  // Set data-theme before the first render: chartColor() resolves canvas colors from it.
  const [mode, setMode] = useState(() => {
    const initial = readWorkspaceTheme()
    document.documentElement.dataset.theme = initial
    return initial
  })
  useEffect(() => {
    const update = () => {
      const next = readWorkspaceTheme()
      document.documentElement.dataset.theme = next
      setMode(next)
    }
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    update()
    window.addEventListener('trade-theme-change', update)
    window.addEventListener('storage', update)
    media?.addEventListener('change', update)
    return () => {
      window.removeEventListener('trade-theme-change', update)
      window.removeEventListener('storage', update)
      media?.removeEventListener('change', update)
    }
  }, [])
  return (
    <ConfigProvider locale={zhCN} theme={workspaceTheme(mode)}>
      <WorkspaceThemeContext.Provider value={mode}>
        <AntApp>
          <QueryClientProvider client={queryClient}>
            <BrowserRouter>{children}</BrowserRouter>
          </QueryClientProvider>
        </AntApp>
      </WorkspaceThemeContext.Provider>
    </ConfigProvider>
  )
}

