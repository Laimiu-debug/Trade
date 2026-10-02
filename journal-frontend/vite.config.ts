import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, process.cwd(), '').VITE_API_PROXY_TARGET || 'http://127.0.0.1:8010'
  return {
    plugins: [react()],
    base: '/journal-app/',
    server: {
      port: 5173,
      proxy: {
        '/journal-app/api': { target, changeOrigin: true },
        '/journal-app/uploads': { target, changeOrigin: true },
      },
    },
  }
})
