/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Em desenvolvimento, /api vai para o backend local. Em produção, o Caddy faz esse papel (ADR-003).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // changeOrigin: false preserva o Host, como o Caddy faz em produção; sem isso a checagem
      // de Origin do backend recusa todas as mutações (o atalho em string usa changeOrigin: true).
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
