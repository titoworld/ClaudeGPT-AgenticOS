// The lock screen (audit A4). After a logout the server did not confirm, the login view
// says so, and honestly: the lock is local, the session is still open on the server. It
// offers «Torna-ho a provar» and a way to log in again (a login ends the old session),
// and while a logout is under way it shows nothing else.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from '../lib/protocol';
import { deferred, FakeApi, FakeSocket } from '../lib/test-server';

const SAVED: RuntimeSettings = {
  revision: 2,
  default_mode: 'debate',
  default_target: 'claude',
  debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' },
  refine: { max_rounds: 12, budget_eur: 3, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'claude' },
  use_cache: true,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'auto', eur_per_usd: 0.86 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
  pdf_in_revisions: 'text',
};

const LOCK_TITLE = "La sessió encara no s'ha pogut tancar al servidor";
const badGateway = () => new Response(JSON.stringify({ detail: 'Bad Gateway' }), { status: 502 });

/** A new app (as after a page load) and the login view, from the same module graph. */
async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Login } = await import('./Login.svelte');
  return { app, render, cleanup, textOf, flushSync, Login };
}

let server: FakeApi;
let env: Awaited<ReturnType<typeof load>> | null = null;

/** Logged in, then a logout the server did not confirm; the login view mounted. */
async function lockedScreen() {
  const e = (env = await load());
  await e.app.init();
  expect(e.app.auth).toBe('ready');
  server.logoutAnswer = badGateway;
  await e.app.logout();
  expect(e.app.auth).toBe('locked');
  const root = e.render(e.Login, {});
  return { e, root };
}

const buttonNamed = (root: HTMLElement, name: string) =>
  [...root.querySelectorAll('button')].find((b) => env!.textOf(b) === name) ?? null;

beforeAll(async () => {
  await load(); // compiles the components once, outside the tests' time limit
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  server = new FakeApi(SAVED);
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  env?.cleanup();
  env?.app.toLogin(); // stops this app's timers and socket
  env = null;
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});

describe('the lock screen (A4)', () => {
  it('says the session is still open on the server and the lock is only local', async () => {
    const { e, root } = await lockedScreen();
    const text = e.textOf(root);
    expect(text).toContain(LOCK_TITLE);
    expect(text).toContain('Aquest bloqueig només és local');
    expect(text).toContain('El servidor ha respost amb un error.'); // why the last attempt failed
    expect(buttonNamed(root, 'Torna-ho a provar')).not.toBeNull();
    // And a way to log in again.
    expect(root.querySelector('input[name="password"]')).not.toBeNull();
    expect(root.querySelector('input[name="totp"]')).not.toBeNull();
    expect(text).toContain('en iniciar sessió es tanca la sessió anterior');
  });

  it('«Torna-ho a provar» retries; once the server confirms, it is the plain login screen', async () => {
    const { e, root } = await lockedScreen();
    buttonNamed(root, 'Torna-ho a provar')!.click();
    await vi.waitFor(() => expect(server.count('POST /api/auth/logout')).toBe(2));
    await vi.waitFor(() => expect(e.app.logoutBusy).toBe(false));
    e.flushSync();
    expect(e.app.auth).toBe('locked'); // still failing
    expect(e.textOf(root)).toContain(LOCK_TITLE);

    server.logoutAnswer = null;
    buttonNamed(root, 'Torna-ho a provar')!.click();
    await vi.waitFor(() => expect(e.app.auth).toBe('login'));
    e.flushSync();
    expect(e.textOf(root)).not.toContain(LOCK_TITLE);
    expect(buttonNamed(root, 'Torna-ho a provar')).toBeNull();
    expect(root.querySelector('input[name="password"]')).not.toBeNull();
  });

  it('while a logout is under way, only says so', async () => {
    const { e, root } = await lockedScreen();
    const answer = deferred<Response>();
    server.logoutAnswer = () => answer.promise;
    buttonNamed(root, 'Torna-ho a provar')!.click();
    e.flushSync();
    expect(e.textOf(root)).toContain('Tancant la sessió…');
    expect(e.textOf(root)).not.toContain(LOCK_TITLE);
    expect(buttonNamed(root, 'Torna-ho a provar')).toBeNull();
    expect(root.querySelector('input[name="password"]')).toBeNull();

    answer.resolve(badGateway());
    await vi.waitFor(() => expect(e.app.logoutBusy).toBe(false));
    e.flushSync();
    expect(e.textOf(root)).toContain(LOCK_TITLE);
  });

  it('logging in from it opens the app', async () => {
    const { e, root } = await lockedScreen();
    const password = root.querySelector<HTMLInputElement>('input[name="password"]')!;
    password.value = 'contrasenya-del-propietari';
    password.dispatchEvent(new Event('input', { bubbles: true }));
    const code = root.querySelector<HTMLInputElement>('input[name="totp"]')!;
    code.value = '123456'; // six digits: sent on its own
    code.dispatchEvent(new Event('input', { bubbles: true }));
    await vi.waitFor(() => expect(e.app.auth).toBe('ready'));
    expect(server.count('POST /api/auth/login')).toBe(1);
    expect(localStorage.getItem('aos.logout-pending')).toBeNull();
  });

  it('is not on the plain login screen', async () => {
    const e = (env = await load());
    server.session = false;
    await e.app.init();
    expect(e.app.auth).toBe('login');
    const root = e.render(e.Login, {});
    expect(e.textOf(root)).not.toContain(LOCK_TITLE);
    expect(buttonNamed(root, 'Torna-ho a provar')).toBeNull();
    expect(root.querySelector('input[name="password"]')).not.toBeNull();
  });
});

describe('the login screen in English and Spanish', () => {
  /** The plain login screen (no session yet), with this module graph's language. */
  async function loginScreen() {
    const e = (env = await load());
    server.session = false;
    await e.app.init();
    expect(e.app.auth).toBe('login');
    const { i18n } = await import('../lib/i18n/index.svelte');
    const root = e.render(e.Login, {});
    return { e, root, i18n };
  }

  /** The owner picks `locale` in the screen's language picker. */
  function pick(root: HTMLElement, locale: string): void {
    const select = root.querySelector<HTMLSelectElement>('.language select')!;
    select.value = locale;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    env!.flushSync();
  }

  const fieldNames = (root: HTMLElement) => [...root.querySelectorAll('label.field > span:first-child')].map((s) => env!.textOf(s));
  const shown = (root: HTMLElement, selector: string) => env!.textOf(root.querySelector(selector));

  it('the language picker repaints it before any session, without opening a socket', async () => {
    const { root, i18n } = await loginScreen();
    expect(shown(root, '.tagline')).toBe('El teu consell privat de Claude i ChatGPT');

    pick(root, 'en');
    expect(i18n.locale).toBe('en');
    expect(document.documentElement.lang).toBe('en');
    expect(shown(root, '.tagline')).toBe('Your private council of Claude and ChatGPT');
    expect(fieldNames(root)).toEqual(['Password', 'Verification code']);
    expect(shown(root, '.hint')).toBe('The 6 digits from your authenticator app. It is sent automatically.');
    expect(shown(root, 'button[type=submit]')).toBe('Log in');
    expect(shown(root, '.foot')).toBe('Private access: only the owner can log in.');
    expect(shown(root, '.language label')).toBe('Language');

    pick(root, 'es');
    expect(shown(root, '.tagline')).toBe('Tu consejo privado de Claude y ChatGPT');
    expect(fieldNames(root)).toEqual(['Contraseña', 'Código de verificación']);
    expect(shown(root, 'button[type=submit]')).toBe('Entrar');
    expect(shown(root, '.language label')).toBe('Idioma');
    expect(localStorage.getItem('aos.lang')).toBe('es'); // the choice stays in this browser
    expect(FakeSocket.all).toHaveLength(0); // no session: nothing to reconnect
  });

  it('an error already shown follows the language', async () => {
    const { e, root } = await loginScreen();
    pick(root, 'en');
    root.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    e.flushSync();
    expect(shown(root, '.error')).toBe('Enter the password.');
    pick(root, 'es');
    expect(shown(root, '.error')).toBe('Escribe la contraseña.');
  });

  it('says the password or the code is wrong, in Spanish', async () => {
    const fetch = server.fetch;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      input === '/api/auth/login'
        ? Promise.resolve(new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 }))
        : fetch(input, init),
    );
    const { e, root } = await loginScreen();
    pick(root, 'es');
    const password = root.querySelector<HTMLInputElement>('input[name="password"]')!;
    password.value = 'no-és-aquesta';
    password.dispatchEvent(new Event('input', { bubbles: true }));
    const code = root.querySelector<HTMLInputElement>('input[name="totp"]')!;
    code.value = '654321'; // six digits: sent on its own
    code.dispatchEvent(new Event('input', { bubbles: true }));
    await vi.waitFor(() => {
      e.flushSync();
      expect(shown(root, '.error')).toBe('La contraseña o el código no son correctos.');
    });
    expect(e.app.auth).toBe('login');
  });

  it('the lock screen, in Spanish', async () => {
    localStorage.setItem('aos.lang', 'es'); // the owner's choice, before the page loads
    const { e, root } = await lockedScreen();
    const text = e.textOf(root);
    expect(text).toContain('Todavía no se ha podido cerrar la sesión en el servidor');
    expect(text).toContain('Motivo: El servidor ha respondido con un error.');
    expect(text).toContain('O vuelve a entrar: al iniciar sesión se cierra la sesión anterior.');
    expect(buttonNamed(root, 'Volver a intentarlo')).not.toBeNull();
  });
});
