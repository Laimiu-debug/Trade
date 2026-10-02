import { defineConfig } from '@playwright/test'

const port = Number(process.env.TRADE_E2E_PORT || 4173)
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid TRADE_E2E_PORT')

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    ...(process.env.TRADE_BROWSER_CHANNEL ? { channel: process.env.TRADE_BROWSER_CHANNEL } : {}),
    trace: 'on-first-retry',
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    port,
    reuseExistingServer: false,
    // Unhandled mock requests must never reach an existing user's backend.
    env: { VITE_ENABLE_MSW: 'true', VITE_API_BASE_URL: '', VITE_API_PROXY_TARGET: 'http://127.0.0.1:9' },
  },
})

