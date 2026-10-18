import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const build = new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC'

export default defineConfig({
  define: { __BUILD__: JSON.stringify(build) },
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { chunkSizeWarningLimit: 900 },
})
