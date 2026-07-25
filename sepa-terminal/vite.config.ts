import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      // Override with SEPA_API_URL when port 8000 is taken by something else.
      '/api': process.env.SEPA_API_URL ?? 'http://127.0.0.1:8000',
    },
  },
})
