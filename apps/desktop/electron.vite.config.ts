import { resolve } from 'node:path';
import { defineConfig } from 'electron-vite';
import react from '@vitejs/plugin-react';
import tailwindcss from 'tailwindcss';

export default defineConfig({
  main: {
    build: {
      externalizeDeps: false,
      rollupOptions: { output: { format: 'cjs', entryFileNames: 'index.cjs' } },
    },
  },
  preload: {
    build: {
      externalizeDeps: false,
      rollupOptions: { output: { format: 'cjs', entryFileNames: 'index.cjs' } },
    },
  },
  renderer: {
    server: { host: '127.0.0.1', port: Number(process.env.TAVO_DEV_PORT ?? 5173), strictPort: true },
    plugins: [react()],
    css: { postcss: { plugins: [tailwindcss({ content: [resolve('src/renderer/**/*.{html,ts,tsx}')], theme: { extend: {} } })] } },
  },
});
