import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

// In dev the Vue app calls /api and Vite proxies to the Nest backend.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5180,
    proxy: {
      '/api': {
        target: process.env.API_TARGET ?? 'http://127.0.0.1:4000',
        changeOrigin: true,
      },
    },
  },
});
