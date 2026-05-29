import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Phase 9A dev convenience: proxy /api/* and /health to the backend so the
// frontend can use relative URLs and we don't need CORS middleware on the API.
// Production deployments should serve frontend + backend behind the same
// origin (reverse proxy) or add explicit CORS headers - neither is in scope here.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
