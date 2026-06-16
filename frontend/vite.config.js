import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dashboard talks to the control plane via same-origin /api and /ws paths.
// In dev, Vite proxies them to the control plane on :8000 (override the
// target with CONTROL_PLANE_URL if it runs elsewhere).
const target = process.env.CONTROL_PLANE_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/ws': { target, ws: true, changeOrigin: true },
    },
  },
})
