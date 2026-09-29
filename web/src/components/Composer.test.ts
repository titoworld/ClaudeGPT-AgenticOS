// The composer before the saved settings arrive (audit A11): it cannot send, it says
// why, a failed load offers «Torna-ho a provar», and the owner's choices survive the load.
// The length of a question (N19): the server's limits, and a draft that is never lost
// when the server rejects the question. Enter on phones and tablets (N20).
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings, ServerMessage } from '../lib/protocol';
import { deferred, FakeApi, FakeSocket } from '../lib/test-server';

const SAVED: RuntimeSettings = {
  revision: 3,
  default_mode: 'solo',
  default_target: 'chatgpt',
  debate: { rounds: 1, consensus_threshold: 70, synthesizer: 'chatgpt' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'auto', eur_per_usd: 0.86 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
};

/** The longest question the server takes, in characters (docs/PROTOCOL.md). */
const LIMIT = 100_000;
const QUESTION = 'Quina és la capital de Mongòlia?';
const NO_USAGE = { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0, cost_usd: null };

/** A new app (as after a page load) and the composer, from the same module graph. */
async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { toasts } = await import('../lib/toasts.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Composer } = await import('./Composer.svelte');
  return { app, toasts, render, cleanup, textOf, flushSync, Composer };
}

/** The system's answers to media queries (`matchMedia`); any other query does not match. */
class FakeMedia {
  readonly #answers = new Map<string, boolean>();
  readonly #listeners = new Map<string, ((e: { matches: boolean }) => void)[]>();

  set(query: string, matches: boolean): void {
    this.#answers.set(query, matches);
    for (const listener of this.#listeners.get(query) ?? []) listener({ matches });
  }

  readonly matchMedia = (query: string) => ({
    media: query,
    matches: this.#answers.get(query) ?? false,
    addEventListener: (_type: 'change', listener: (e: { matches: boolean }) => void) => {
      this.#listeners.set(query, [...(this.#listeners.get(query) ?? []), listener]);
    },
    removeEventListener: () => {},
  });
}

const COARSE = '(pointer: coarse)';
let media: FakeMedia;

let server: FakeApi;
let env: Awaited<ReturnType<typeof load>> | null = null;

async function fresh() {
  env = await load();
  return env;
}

/** Logs in with the settings request pending, mounts the composer and opens the socket. */
async function start(getSettings: () => RuntimeSettings | Promise<RuntimeSettings>) {
  const e = await fresh();
  server.settingsGet = getSettings;
  const entering = e.app.init();
  await vi.waitFor(() => expect(e.app.auth).toBe('ready'));
  const socket = FakeSocket.last();
  socket.open();
  const root = e.render(e.Composer, {});
  const textarea = root.querySelector('textarea')!;
  type(textarea, QUESTION);
  return { e, entering, socket, root, textarea };
}

/** The composer with the saved settings loaded and the socket open, a question typed. */
async function ready() {
  const started = await start(() => structuredClone(SAVED));
  await started.entering;
  started.e.flushSync();
  return started;
}

const sendButton = (root: HTMLElement) => root.querySelector<HTMLButtonElement>('button.send')!;
const turnStarts = (socket: FakeSocket) => socket.sent.filter((m) => m.type === 'turn.start');
const lastRequestId = (socket: FakeSocket) => String(turnStarts(socket).at(-1)!.request_id);

/** What the owner types (the whole text of the text area). */
function type(textarea: HTMLTextAreaElement, value: string): void {
  textarea.value = value;
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  env!.flushSync();
}

/** A key press in the text area (Enter unless said otherwise); returns the event. */
function press(textarea: HTMLTextAreaElement, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...init });
  textarea.dispatchEvent(event);
  env!.flushSync();
  return event;
}

function pressEnter(textarea: HTMLTextAreaElement): void {
  press(textarea);
}

/** The server rejects a turn before it starts: turn.failed without turn.started, nothing stored. */
function rejectTurn(
  socket: FakeSocket,
  requestId: string,
  error = { kind: 'not_found', message: 'La conversa no existeix.' },
): void {
  socket.receive({ type: 'turn.failed', request_id: requestId, seq: 1, error, usage: NO_USAGE });
  env!.flushSync();
}

beforeAll(async () => {
  await load(); // compiles the components once, outside the tests' time limit
});

beforeEach(() => {
  server = new FakeApi(SAVED);
  FakeSocket.all = [];
  media = new FakeMedia();
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
  vi.stubGlobal('matchMedia', media.matchMedia);
});

afterEach(() => {
  env?.cleanup();
  env?.app.toLogin(); // stops this app's timers and socket
  env = null;
  vi.unstubAllGlobals();
});

describe('Composer before the saved settings arrive (A11)', () => {
  it('cannot send until they load, and says so', async () => {
    const slow = deferred<RuntimeSettings>();
    const { e, entering, socket, root, textarea } = await start(() => slow.promise);
    expect(sendButton(root).disabled).toBe(true);
    expect(e.textOf(root.querySelector('.kbd-hint'))).toBe('Carregant la configuració…');
    pressEnter(textarea);
    sendButton(root).click();
    e.flushSync();
    expect(turnStarts(socket)).toEqual([]);

    slow.resolve(structuredClone(SAVED));
    await entering;
    e.flushSync();
    expect(sendButton(root).disabled).toBe(false);
    pressEnter(textarea);
    expect(turnStarts(socket)).toHaveLength(1);
    expect(turnStarts(socket)[0]).toMatchObject({ mode: 'solo', target: 'chatgpt' });
  });

  it('keeps a mode picked while they load', async () => {
    const slow = deferred<RuntimeSettings>();
    const { e, entering, root } = await start(() => slow.promise);
    const duel = root.querySelector<HTMLInputElement>('input[type=radio][value=duel]')!;
    duel.click();
    e.flushSync();
    slow.resolve(structuredClone(SAVED));
    await entering;
    e.flushSync();
    expect(duel.checked).toBe(true);
    expect(e.app.composer.mode).toBe('duel');
  });

  it('shows a failed load with «Torna-ho a provar»', async () => {
    let failing = true;
    const { e, entering, socket, root } = await start(() => {
      if (failing) throw new TypeError('Failed to fetch');
      return structuredClone(SAVED);
    });
    await entering;
    e.flushSync();
    const alert = root.querySelector('[role=alert]');
    expect(e.textOf(alert)).toContain("No s'ha pogut carregar la configuració.");
    expect(e.textOf(alert)).toContain('No es pot connectar amb el servidor.');
    expect(sendButton(root).disabled).toBe(true);

    failing = false;
    const gets = server.count('GET /api/settings');
    const retry = [...root.querySelectorAll('button')].find((b) => b.textContent?.includes('Torna-ho a provar'))!;
    retry.click();
    await vi.waitFor(() => expect(e.app.settingsStatus).toBe('ready'));
    e.flushSync();
    expect(server.count('GET /api/settings')).toBe(gets + 1);
    expect(root.querySelector('[role=alert]')).toBeNull();
    expect(sendButton(root).disabled).toBe(false);
    sendButton(root).click();
    e.flushSync();
    expect(turnStarts(socket)).toHaveLength(1);
  });
});

describe('Composer: the length of a question (N19)', () => {
  const counter = (root: HTMLElement) => root.querySelector('.char-count');
  const submitForm = (root: HTMLElement) =>
    root.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

  it('shows the count near the limit; past it, it does not send and keeps the draft', async () => {
    const { e, socket, root, textarea } = await ready();
    type(textarea, 'x'.repeat(LIMIT * 0.9 - 1));
    expect(counter(root)).toBeNull();
    type(textarea, 'x'.repeat(LIMIT * 0.9));
    expect(e.textOf(counter(root))).toBe('90.000 / 100.000 caràcters');
    expect(sendButton(root).disabled).toBe(false);

    type(textarea, 'x'.repeat(LIMIT + 1));
    expect(e.textOf(counter(root))).toBe('100.001 / 100.000 caràcters');
    expect(counter(root)!.classList.contains('over')).toBe(true);
    expect(e.textOf(root.querySelector('.too-long'))).toBe(
      'La pregunta passa del màxim de 100.000 caràcters: escurça-la per enviar-la.',
    );
    expect(sendButton(root).disabled).toBe(true);
    expect(sendButton(root).title).toBe('La pregunta és massa llarga');
    press(textarea);
    sendButton(root).click();
    submitForm(root); // even past the disabled button, the app refuses it
    e.flushSync();
    expect(turnStarts(socket)).toEqual([]);
    expect(e.app.composer.draft).toHaveLength(LIMIT + 1);
    expect(e.toasts.items.map((t) => t.text)).toContain(
      'La pregunta és massa llarga (màxim 100.000 caràcters). Escurça-la per enviar-la.',
    );

    // What surrounds it is neither sent nor counted.
    type(textarea, `  ${'x'.repeat(LIMIT)}\n`);
    expect(e.textOf(counter(root))).toBe('100.000 / 100.000 caràcters');
    expect(root.querySelector('.too-long')?.textContent ?? '').toBe('');
    expect(sendButton(root).disabled).toBe(false);
    press(textarea);
    expect(turnStarts(socket)).toHaveLength(1);
    expect(String(turnStarts(socket)[0]!.text)).toHaveLength(LIMIT);
    expect(e.app.composer.draft).toBe('');
  });

  it('counts characters as the server does: an emoji is one', async () => {
    const { e, socket, root, textarea } = await ready();
    type(textarea, '😀'.repeat(LIMIT)); // 200.000 UTF-16 code units
    expect(e.textOf(counter(root))).toBe('100.000 / 100.000 caràcters');
    expect(sendButton(root).disabled).toBe(false);
    press(textarea);
    expect(turnStarts(socket)).toHaveLength(1);
  });

  it('never sends a message longer than the server reads, so no turn waits forever', async () => {
    const { e, socket, root, textarea } = await ready();
    // Within the limit, but JSON writes each control character with 6: 600.000 > 524.288.
    type(textarea, `a${'\u0001'.repeat(LIMIT - 1)}`);
    expect(sendButton(root).disabled).toBe(false);
    press(textarea);
    expect(turnStarts(socket)).toEqual([]);
    expect(e.app.runningTurn).toBeNull();
    expect(Object.keys(e.app.turns.turns)).toEqual([]);
    expect(e.app.composer.draft).toHaveLength(LIMIT);
    expect(e.toasts.items.map((t) => t.text)).toContain(
      'La pregunta no es pot enviar: té massa caràcters de control, que ocupen massa. Treu-los i torna-ho a provar.',
    );
  });

  it('gives the question back when the server rejects it before the turn starts', async () => {
    const { e, socket, textarea } = await ready();
    press(textarea);
    expect(e.app.composer.draft).toBe('');
    rejectTurn(socket, lastRequestId(socket), {
      kind: 'invalid',
      message: 'La pregunta és massa llarga (màxim 100000 caràcters).',
    });
    expect(e.app.composer.draft).toBe(QUESTION);
    expect(textarea.value).toBe(QUESTION);
    expect(e.app.runningTurn).toBeNull();
  });

  it('gives the question back when the server refuses the turn.start with an error', async () => {
    const { e, socket, textarea } = await ready();
    press(textarea);
    const requestId = lastRequestId(socket);
    socket.receive({ type: 'error', code: 'busy', message: 'Ja hi ha massa torns en curs.', request_id: requestId });
    e.flushSync();
    expect(e.app.turns.get(requestId)?.status).toBe('failed');
    expect(e.app.composer.draft).toBe(QUESTION);
    expect(e.app.runningTurn).toBeNull();
  });

  it('keeps what the owner typed since, and never gives back a question the server stored', async () => {
    const { e, socket, textarea } = await ready();
    press(textarea);
    type(textarea, 'Una altra pregunta');
    rejectTurn(socket, lastRequestId(socket));
    expect(e.app.composer.draft).toBe('Una altra pregunta');

    type(textarea, QUESTION);
    press(textarea);
    const requestId = lastRequestId(socket);
    const started: ServerMessage = {
      type: 'turn.started', request_id: requestId, seq: 1, conversation_id: 7, turn_id: 40, mode: 'solo', new_conversation: true,
    };
    socket.receive(started);
    socket.receive({ type: 'turn.failed', request_id: requestId, seq: 2, error: { kind: 'rate_limit', message: 'Límit.' }, usage: NO_USAGE });
    e.flushSync();
    expect(e.app.turns.get(requestId)?.status).toBe('failed');
    expect(e.app.composer.draft).toBe('');
  });

  it('a message the server did not read (too_large) does not leave its turn waiting', async () => {
    const { e, socket, textarea } = await ready();
    press(textarea);
    const requestId = lastRequestId(socket);
    socket.receive({ type: 'error', code: 'too_large', message: 'El missatge és massa gran.' });
    e.flushSync();
    // The error does not say which turn it was: the app asks about the ones that have not started.
    expect(socket.sent.filter((m) => m.type === 'turn.subscribe')).toEqual([
      { type: 'turn.subscribe', request_id: requestId, after_seq: 0 },
    ]);
    socket.receive({ type: 'turn.unknown', request_id: requestId });
    e.flushSync();
    expect(e.app.runningTurn).toBeNull();
    expect(e.app.turns.get(requestId)?.error?.message).toBe(
      'El servidor no ha llegit la pregunta perquè el missatge era massa gran. Escurça-la i torna-ho a provar.',
    );
    expect(e.app.composer.draft).toBe(QUESTION);
  });
});

describe('Composer: Enter on phones and tablets (N20)', () => {
  const hint = (root: HTMLElement) => env!.textOf(root.querySelector('.kbd-hint'));

  it('with a coarse pointer Enter makes a new line and does not send', async () => {
    media.set(COARSE, true);
    const { socket, root, textarea } = await ready();
    type(textarea, '');
    expect(textarea.getAttribute('enterkeyhint')).toBe('enter');
    expect(hint(root)).toBe(
      "Ctrl+Enter per enviar . Enter fa un salt de línia. També pots enviar amb el botó Envia, o amb Cmd+Enter en un teclat d'Apple. Esc atura el torn en curs.",
    );
    type(textarea, QUESTION);
    expect(press(textarea).defaultPrevented).toBe(false); // the browser writes the line break
    expect(press(textarea, { shiftKey: true }).defaultPrevented).toBe(false);
    expect(turnStarts(socket)).toEqual([]);
    expect(sendButton(root).title).toBe('Envia (Ctrl+Enter)');
  });

  it('there the button, Ctrl+Enter and Cmd+Enter send', async () => {
    media.set(COARSE, true);
    const { e, socket, root, textarea } = await ready();
    expect(press(textarea, { ctrlKey: true }).defaultPrevented).toBe(true);
    expect(turnStarts(socket)).toHaveLength(1);
    rejectTurn(socket, lastRequestId(socket)); // the question comes back
    expect(press(textarea, { metaKey: true }).defaultPrevented).toBe(true);
    expect(turnStarts(socket)).toHaveLength(2);
    rejectTurn(socket, lastRequestId(socket));
    sendButton(root).click();
    e.flushSync();
    expect(turnStarts(socket)).toHaveLength(3);
  });

  it('with a fine pointer Enter sends, and Shift+Enter makes a new line', async () => {
    const { socket, root, textarea } = await ready();
    expect(textarea.getAttribute('enterkeyhint')).toBe('send');
    expect(sendButton(root).title).toBe('Envia (Enter)');
    expect(press(textarea, { shiftKey: true }).defaultPrevented).toBe(false);
    expect(turnStarts(socket)).toEqual([]);
    expect(press(textarea).defaultPrevented).toBe(true);
    expect(turnStarts(socket)).toHaveLength(1);
    type(textarea, '');
    expect(hint(root)).toBe('Enter per enviar . Maj+Enter fa un salt de línia i Esc atura el torn en curs.');
  });

  it('follows the pointer when it changes', async () => {
    media.set(COARSE, true);
    const { socket, textarea } = await ready();
    expect(press(textarea).defaultPrevented).toBe(false);
    media.set(COARSE, false); // e.g. a tablet gets a trackpad
    env!.flushSync();
    expect(textarea.getAttribute('enterkeyhint')).toBe('send');
    expect(press(textarea).defaultPrevented).toBe(true);
    expect(turnStarts(socket)).toHaveLength(1);
  });
});
