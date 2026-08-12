import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * The console is a static bundle. It holds no secrets and no server state.
 *
 * `HARNESS_URL` is the only backend coordinate it knows: the Validation Harness.
 * It never learns a Vision OS module path, an import name, or a database URL —
 * there is nothing here to configure one with, which is the structural form of
 * "communication occurs only through public APIs".
 */
const harness = process.env.VOSVC_HARNESS_URL ?? 'http://127.0.0.1:8808';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@contract': fileURLToPath(new URL('./src/contract', import.meta.url)),
      '@transport': fileURLToPath(new URL('./src/transport', import.meta.url)),
      '@state': fileURLToPath(new URL('./src/state', import.meta.url)),
      '@components': fileURLToPath(new URL('./src/components', import.meta.url)),
      '@panels': fileURLToPath(new URL('./src/panels', import.meta.url)),
      '@simulator': fileURLToPath(new URL('./src/simulator', import.meta.url)),
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5273,
    strictPort: true,
    proxy: {
      '/api': { target: harness, changeOrigin: true },
      '/ws': { target: harness, ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2022',
  },
});
