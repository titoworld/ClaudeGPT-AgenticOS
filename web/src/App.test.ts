// The dialog shown when the server rejects the page's origin (WebSocket close 4403)
// names the settings that decide it (audit N24): AOS_PUBLIC_ORIGIN, which must be the
// exact address of the page, and AOS_EXTRA_ORIGINS. AOS_ALLOWED_ORIGINS does not exist:
// the server ignores it.
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from './lib/protocol';
import { FakeApi, FakeSocket, polyfillDialog } from './lib/test-server';

const SAVED: RuntimeSettings = {
  revision: 1,
  default_mode: 'solo',
  default_target: 'claude',
  debate: { rounds: 1, consensus_threshold: 80, synthesizer: 'claude' },
  refine: { max_rounds: 12, budget_eur: 3, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'claude' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'manual', eur_per_usd: 0.9 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
  pdf_in_revisions: 'text',
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('the rejected-origin dialog (N24)', () => {
  it('names AOS_PUBLIC_ORIGIN and AOS_EXTRA_ORIGINS, and the address to put there', async () => {
    localStorage.setItem('aos.effects', 'off'); // no 3D scene in jsdom
    const server = new FakeApi(SAVED);
    server.session = false;
    vi.stubGlobal('fetch', server.fetch);
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.resetModules();
    const { app } = await import('./lib/app.svelte');
    const { render, cleanup, textOf } = await import('./lib/test-render');
    const { flushSync } = await import('svelte');
    const { default: App } = await import('./App.svelte');
    const root = render(App, {});
    try {
      await vi.waitFor(() => expect(app.auth).toBe('login'));
      app.fatal = "El servidor ha rebutjat la connexió en temps real perquè l'origen d'aquesta pàgina no és a la llista permesa (codi 4403).";
      flushSync();
      const hint = textOf(root.querySelector('[role=alertdialog]'));
      expect(hint).toContain('AOS_PUBLIC_ORIGIN');
      expect(hint).toContain('AOS_EXTRA_ORIGINS');
      expect(hint).toContain(location.origin);
      expect(hint).not.toContain('AOS_ALLOWED_ORIGINS');
    } finally {
      cleanup();
      app.toLogin();
    }
  });

  it('in English: the server refused the socket, and the settings and the address are code', async () => {
    localStorage.setItem('aos.effects', 'off'); // no 3D scene in jsdom
    localStorage.setItem('aos.lang', 'en'); // the owner's choice, before the page loads
    polyfillDialog();
    FakeSocket.all = [];
    const server = new FakeApi(SAVED);
    vi.stubGlobal('fetch', server.fetch);
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.resetModules();
    const { app } = await import('./lib/app.svelte');
    const { render, cleanup, textOf } = await import('./lib/test-render');
    const { flushSync } = await import('svelte');
    const { default: App } = await import('./App.svelte');
    const root = render(App, {});
    try {
      await vi.waitFor(() => expect(FakeSocket.all).toHaveLength(1));
      FakeSocket.last().onclose?.({ code: 4403 }); // the server rejects the page's origin
      flushSync();
      const dialog = root.querySelector('[role=alertdialog]')!;
      expect(textOf(dialog.querySelector('h2'))).toBe('Connection refused');
      expect(textOf(dialog.querySelector('#fatal-text'))).toBe(
        "The server refused the real-time connection because this page's origin is not on the allowed list (code 4403).",
      );
      expect([...dialog.querySelectorAll('.hint code')].map((c) => c.textContent)).toEqual([
        'AOS_PUBLIC_ORIGIN',
        location.origin,
        'AOS_EXTRA_ORIGINS',
      ]);
      expect(textOf(dialog.querySelector('.hint'))).toBe(
        `On the server, AOS_PUBLIC_ORIGIN must be exactly the address of this page, ${location.origin} (or this address must be in AOS_EXTRA_ORIGINS). Fix it, restart the app and reload the page.`,
      );
      expect(textOf(dialog.querySelector('button'))).toBe('Reload');
    } finally {
      cleanup();
      app.toLogin();
    }
  });
});
