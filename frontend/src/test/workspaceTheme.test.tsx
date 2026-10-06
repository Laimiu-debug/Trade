import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AppProviders } from '@/app/providers'
import { ThemeButton } from '@/shared/theme/ThemeButton'

describe('workspace theme preference', () => {
  it('switches themes without discarding an unsaved field and restores the preference', async () => {
    const view = render(<AppProviders><ThemeButton /><input aria-label="未保存草稿" defaultValue="" /></AppProviders>)
    const field = screen.getByLabelText('未保存草稿')
    fireEvent.change(field, { target: { value: '保留正在编辑的内容' } })
    fireEvent.click(screen.getByRole('button', { name: '切换深色主题' }))
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'))
    expect(field).toHaveValue('保留正在编辑的内容')
    expect(localStorage.getItem('trade-theme-mode')).toBe('dark')
    view.unmount()
    render(<AppProviders><ThemeButton /></AppProviders>)
    expect(screen.getByRole('button', { name: '切换浅色主题' })).toBeInTheDocument()
  })

  it('applies a theme preference changed in another workspace', async () => {
    render(<AppProviders><ThemeButton /></AppProviders>)
    localStorage.setItem('trade-theme-mode', 'dark')
    fireEvent(window, new StorageEvent('storage', { key: 'trade-theme-mode', newValue: 'dark' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '切换浅色主题' })).toBeInTheDocument())
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
