import path from 'node:path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    globals: true,
    // backtest/settings 等测试涉及完整页面渲染与 mock 数据处理,
    // 本地 ~3-8s,CI runner 慢 3-4 倍需 20-30s。统一放宽避免 CI 误报。
    testTimeout: 30_000,
    hookTimeout: 30_000,
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
})
