import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    proxy: {
      '/api': 'http://localhost:8001',
      '/rules': 'http://localhost:8001',
      '/gmail': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
