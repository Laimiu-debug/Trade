import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/rebuild/test-setup.ts'],
    include: ['src/rebuild/**/*.test.{ts,tsx}'],
    css: true,
    globals: true,
    // Bound full-page renderer memory and lazy-import contention in full runs.
    maxWorkers: 4,
    // Full workspace renders are slow on CI runners.
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
})
