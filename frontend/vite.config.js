import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 开发态把 /api 代理到本地后端，规避跨域；生产构建后由同源反代承载
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
