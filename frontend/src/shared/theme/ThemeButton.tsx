import { useContext } from 'react'
import { Button } from 'antd'
import { WorkspaceThemeContext } from './theme-context'

export function ThemeButton() {
  const mode = useContext(WorkspaceThemeContext)
  return <Button type="text" onClick={() => {
    localStorage.setItem('trade-theme-mode', mode === 'light' ? 'dark' : 'light')
    window.dispatchEvent(new Event('trade-theme-change'))
  }} aria-label={mode === 'light' ? '切换深色主题' : '切换浅色主题'}>{mode === 'light' ? '深色' : '浅色'}</Button>
}
