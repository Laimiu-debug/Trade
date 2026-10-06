import { createContext } from 'react';

export type Theme = 'light' | 'dark';
export const ThemeContext = createContext<Theme>('light');

export function readTheme(): Theme {
  const saved = localStorage.getItem('trade-theme-mode') ?? localStorage.getItem('lt-theme');
  if (saved === 'system') return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  return saved === 'dark' ? 'dark' : 'light';
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}
