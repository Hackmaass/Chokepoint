import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
const proxy = { '/api': 'http://127.0.0.1:8080' }
export default defineConfig({ plugins: [react()], server: { port: 5199, proxy }, preview: { port: 5198, proxy } })
