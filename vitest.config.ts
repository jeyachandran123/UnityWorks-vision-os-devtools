import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

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
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/support/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/simulator/**'],
    },
  },
});
