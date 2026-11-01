import { fileURLToPath, URL } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// The Python package serves the build: synapptic dashboard -> src/synapptic/dashboard/static.
// In development the Vite server proxies /api (HTTP and the websocket) to `synapptic dashboard` on :5200.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:5200', ws: true, changeOrigin: false } },
  },
  build: {
    outDir: '../src/synapptic/dashboard/static',
    emptyOutDir: true,
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
