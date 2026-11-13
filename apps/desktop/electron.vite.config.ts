import { resolve } from 'node:path';
import { defineConfig } from 'electron-vite';
import react from '@vitejs/plugin-react';
import tailwindcss from 'tailwindcss';

export default defineConfig({
  main: {
    build: {
      externalizeDeps: false,
      rollupOptions: { external: ['better-sqlite3'], output: { format: 'cjs', entryFileNames: 'index.cjs' } },
    },
  },
  preload: {
    build: {
      externalizeDeps: false,
      rollupOptions: { output: { format: 'cjs', entryFileNames: 'index.cjs' } },
    },
  },
  renderer: {
    plugins: [
      react(),
      {
        name: 'development-csp', apply: 'serve',
        transformIndexHtml: (html) => html
          .replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
          .replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")
          .replace("connect-src 'none'", "connect-src 'self' ws://localhost:* ws://127.0.0.1:*"),
      },
    ],
    css: { postcss: { plugins: [tailwindcss({ content: [resolve('src/renderer/**/*.{html,ts,tsx}')], theme: { extend: {} } })] } },
  },
});
