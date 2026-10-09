import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = process.env.STRATEGYLAB_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    // Uploaded strategies are arbitrary code; nothing here should be reachable off this machine.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': backend,
    },
  },
})
