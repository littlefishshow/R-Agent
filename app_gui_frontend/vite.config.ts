import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const projectRoot = fileURLToPath(new URL('.', import.meta.url))
const katexFontDir = resolve(projectRoot, '../node_modules/katex/dist/fonts')

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    fs: {
      // If KaTeX is temporarily resolved from the repo-level install, allow only
      // KaTeX font assets instead of widening access to the whole repository/node_modules.
      allow: [projectRoot, katexFontDir],
    },
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
