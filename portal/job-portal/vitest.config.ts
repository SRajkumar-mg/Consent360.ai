import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Deliberately separate from vite.config.ts: that file exists to configure the
// dev server's API proxy (and the JOB_PORTAL_API_KEY header it injects), none
// of which a unit test should inherit or accidentally depend on. Tests talk to
// a stubbed `fetch`, never to a real backend.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
