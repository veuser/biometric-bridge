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
    // Pinned to IPv4. Vite otherwise binds ::1 only on Windows, so the dev server
    // answers on localhost but refuses 127.0.0.1 — a confusing few minutes when a
    // tool or script uses the numeric form.
    host: '127.0.0.1',
    port: 5173,
    // Dev only: proxying keeps the API same-origin, so the session cookie behaves
    // exactly as it does in production.
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/health': 'http://127.0.0.1:8080',
    },
  },
})
