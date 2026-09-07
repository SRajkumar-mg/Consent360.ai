import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    host: '0.0.0.0',
    proxy: {
      '/consent': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            // R3-01: no shared static key. This must be CAREER_HUB's own
            // tenant-bound key (see backend/seed_api_keys.py, which prints
            // it once at creation) - there is no fallback to the legacy
            // shared INTEGRATION_API_KEY here on purpose, so a missing env
            // var fails loudly (an unset header - the backend rejects the
            // request) instead of quietly authenticating as a shared,
            // unbound credential.
            const apiKey = process.env.JOB_PORTAL_API_KEY
            if (apiKey) {
              proxyReq.setHeader('X-API-Key', apiKey)
            } else {
              console.warn(
                '[job-portal] JOB_PORTAL_API_KEY is not set - requests to /consent will be sent with no ' +
                'X-API-Key header and rejected by the backend. Run `python seed_api_keys.py` in cms/backend ' +
                'and export the printed CAREER_HUB key as JOB_PORTAL_API_KEY.'
              )
            }
          })
        },
      },
      '/portal': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/public': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
