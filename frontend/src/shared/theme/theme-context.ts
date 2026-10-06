import { createContext } from 'react'

export const WorkspaceThemeContext = createContext<'light' | 'dark'>('light')

export function readWorkspaceTheme(): 'light' | 'dark' {
  const mode = localStorage.getItem('trade-theme-mode')
  return mode === 'dark' || (mode === 'system' && window.matchMedia?.('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'
}
