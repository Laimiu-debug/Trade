import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist-rebuild', rollupOptions: { input: 'rebuild.html' } },
  server: {
    host: '127.0.0.1',
    port: 4174,
    strictPort: true,
    proxy: { '/api/v1': { target: 'http://127.0.0.1:8011', changeOrigin: true } },
  },
})
