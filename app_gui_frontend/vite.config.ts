import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/health': 'http://127.0.0.1:8765',
      '/frontend': 'http://127.0.0.1:8765',
      '/sessions': {
        target: 'http://127.0.0.1:8765',
        ws: true,
      },
      '/learning': 'http://127.0.0.1:8765',
      '/workspace': 'http://127.0.0.1:8765',
    },
  },
})
