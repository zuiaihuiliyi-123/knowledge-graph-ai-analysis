import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 前端开发服务器：将 /api 与 /health 代理到 FastAPI 后端，避免跨域问题
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 拆分第三方库，避免单个 chunk 过大
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-element': ['element-plus', '@element-plus/icons-vue'],
          'vendor-g6': ['@antv/g6'],
        },
      },
    },
  },
})
