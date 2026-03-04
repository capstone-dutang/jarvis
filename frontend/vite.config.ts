import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '0.0.0.0',
    watch: {
      usePolling: true,  // Docker on Windows 파일 변경 감지
    },
    proxy: {
      '/api': 'http://backend:8000',
    },
  },
})
