import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8007,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
      '/cmp': { target: apiTarget, changeOrigin: true, rewrite: (p) => p.replace(/^\/cmp/, '') },
      '/public': { target: apiTarget, changeOrigin: true },
    },
  },
})
