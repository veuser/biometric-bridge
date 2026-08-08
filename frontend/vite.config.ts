import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * The build lands directly in the backend's static directory: FastAPI serves the
 * portal on the same origin as the API, so there is no CORS surface and the session
 * cookie just works.
 *
 * Nothing is loaded from a CDN — many of these machines only have outbound HTTPS to
 * HRMS (PLAN.md §32), and bundling everything is also what lets the CSP stay strict.
 */
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/app/static',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    // Dev only: `npm run dev` proxies to the backend so the cookie is same-origin.
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/health': 'http://127.0.0.1:8080',
    },
  },
})
