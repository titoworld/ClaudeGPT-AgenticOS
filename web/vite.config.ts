/// <reference types="vitest/config" />
import { svelte } from '@sveltejs/vite-plugin-svelte';
import pdfjs from 'pdfjs-dist/package.json' with { type: 'json' };
import { defineConfig } from 'vite';

// Same policy the backend sends in production (src/agentic_os/server/middleware.py);
// tests/server/test_server_http.py checks that the two stay identical.
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

/**
 * Files PDF.js fetches by name from a base URL (lib/pdf.ts imports them): the standard
 * fonts, the CMaps and the image decoders without WebAssembly.
 */
const PDFJS_DATA = /(?:^|\/)node_modules\/pdfjs-dist\/((?:standard_fonts|cmaps|wasm)\/[^/]+)$/;

/**
 * Where the build puts an asset: PDF.js's data files keep their names, under a folder
 * named after its version (the server caches /assets/ forever: a new version must be a
 * new folder); every other asset gets a hashed name.
 */
function assetFileNames(asset: { originalFileNames: readonly string[] }): string {
  const data = asset.originalFileNames.map((file) => PDFJS_DATA.exec(file)?.[1]).find(Boolean);
  return data ? `assets/pdfjs-${pdfjs.version}/${data}` : 'assets/[name]-[hash][extname]';
}

export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  // Component tests mount Svelte in jsdom, which needs its browser build (Vitest runs in mode "test").
  resolve: mode === 'test' ? { conditions: ['browser'] } : undefined,
  build: {
    target: 'es2022',
    // The three.js chunk (~540 kB) is lazy-loaded after the first paint.
    chunkSizeWarningLimit: 700,
    modulePreload: { polyfill: false },
    rolldownOptions: { output: { assetFileNames } },
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
