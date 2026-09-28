/// <reference types="vitest/config" />
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

// Same policy the backend sends in production (src/agentic_os/security/headers.py).
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "style-src-attr 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
].join('; ');

export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  // Component tests mount Svelte in jsdom, which needs its browser build (Vitest runs in mode "test").
  resolve: mode === 'test' ? { conditions: ['browser'] } : undefined,
  build: {
    target: 'es2022',
    // The three.js chunk (~540 kB) is lazy-loaded after the first paint.
    chunkSizeWarningLimit: 700,
    modulePreload: { polyfill: false },
  },
  server: {
    // Development: proxy the FastAPI backend (uv run agentic-os serve --dev).
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
  preview: {
    headers: {
      'Content-Security-Policy': CSP,
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    passWithNoTests: true,
  },
}));
