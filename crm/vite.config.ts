import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8008,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/cmp': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/cmp/, ''),
      },
      '/public': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})