import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The worker imports ../bigdiff/*.py as raw text, so dev needs to read one level up.
export default defineConfig({
  plugins: [react()],
  server: { fs: { allow: ['..'] } },
  worker: { format: 'es' },
})
