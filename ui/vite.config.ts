import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Electron の file:// でも動作するよう相対パスを使用
  base: './',
  build: {
    outDir: 'dist',
  },
  server: {
    // 開発時は FastAPI にプロキシ
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
