import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 构建产物直接输出到 dist/，由 FastAPI 静态托管
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8848',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/unit/**/*.spec.ts'],
    setupFiles: ['tests/unit/setup.ts'],
  },
})
