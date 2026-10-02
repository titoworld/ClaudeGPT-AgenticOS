// The composer's «Perfecciona» mode (docs/adr/0010-refine-mode.md): a fourth mode
// with its options (rounds, budget in euros, word limit, editor, stopping by itself),
// which start from the saved defaults and stay as the owner changes them, like the other
// options; the turn carries them. The mode is never chosen by itself.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationSummary, RuntimeSettings } from '../lib/protocol';
import { FakeApi, FakeSocket } from '../lib/test-server';

const SAVED: RuntimeSettings = {
  revision: 3,
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

async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Composer } = await import('./Composer.svelte');
  return { app, render, cleanup, textOf, flushSync, Composer };
}

let server: FakeApi;
let env: Awaited<ReturnType<typeof load>> | null = null;

/** The composer with the saved settings loaded and the socket open, a question typed. */
async function ready() {
  const e = (env = await load());
  await e.app.init();
  FakeSocket.last().open();
  const root = e.render(e.Composer, {});
  const textarea = root.querySelector('textarea')!;
  textarea.value = 'Escriu un pla de llançament.';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  e.flushSync();
  return { e, root, textarea, socket: FakeSocket.last() };
}

const radio = (root: HTMLElement, value: string) => root.querySelector<HTMLInputElement>(`input[type=radio][value=${value}]`)!;
const popover = (root: HTMLElement) => root.ownerDocument.querySelector<HTMLElement>('.popover.refine-options')!;
const optionsButton = (root: HTMLElement) => root.querySelector<HTMLButtonElement>('button.refine-options-btn');
const field = (root: HTMLElement, label: string) =>
  [...popover(root).querySelectorAll<HTMLInputElement>('input')].find(
    (input) => input.closest('label')?.textContent?.includes(label) || input.getAttribute('aria-label')?.includes(label),
  )!;

function set(input: HTMLInputElement, value: string, event: 'input' | 'change' = 'input'): void {
  input.value = value;
  input.dispatchEvent(new Event(event, { bubbles: true }));
  env!.flushSync();
}

function click(input: HTMLElement): void {
  input.click();
  env!.flushSync();
}

function send(textarea: HTMLTextAreaElement): void {
  textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  env!.flushSync();
}

const lastStart = (socket: FakeSocket) => socket.sent.filter((m) => m.type === 'turn.start').at(-1)!;

beforeAll(async () => {
  await load(); // compiles the components once, outside the tests' time limit
});

beforeEach(() => {
  history.replaceState(null, '', '#/');
  server = new FakeApi(SAVED);
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  env?.cleanup();
  env?.app.toLogin();
  env = null;
  vi.unstubAllGlobals();
});

describe('Composer: the «Perfecciona» mode', () => {
  it('is a fourth mode, which says what it does', async () => {
    const { e, root } = await ready();
    expect([...root.querySelectorAll<HTMLInputElement>('.modes input[type=radio]')].map((r) => r.value)).toEqual([
      'solo',
      'duel',
      'debate',
      'refine',
    ]);
    const label = radio(root, 'refine').closest('label')!;
    expect(e.textOf(label)).toBe('Perfecciona');
    expect(label.title).toBe("Perfecciona: les dues IA milloren un sol document ronda rere ronda fins que l'aturis.");
    expect(optionsButton(root)).toBeNull();
  });

  it('shows its options, from the saved defaults, instead of the debate ones and the cache', async () => {
    const { e, root } = await ready();
    click(radio(root, 'refine'));
    expect(e.app.composer.mode).toBe('refine');
    expect(e.textOf(optionsButton(root))).toBe('12 rondes · 3 €');
    expect(optionsButton(root)!.getAttribute('aria-label')).toBe(
      "Opcions de Perfecciona: com a molt 12 rondes, pressupost de 3 €, límit de paraules automàtic, edita Claude, s'atura sol quan convergeix",
    );
    expect(root.querySelector('button.options-btn:not(.refine-options-btn)')).toBeNull();
    expect(root.querySelector('label.cache')).toBeNull(); // a refine turn never uses the turn cache
    expect(e.textOf(popover(root).querySelector('h3'))).toBe('Opcions de Perfecciona');
  });

  it('sends the options the owner set', async () => {
    const { e, root, textarea, socket } = await ready();
    click(radio(root, 'refine'));
    set(field(root, 'Rondes màximes'), '20');
    set(field(root, 'Pressupost'), '5,5', 'change');
    click(field(root, 'Límit automàtic'));
    set(field(root, 'Límit de paraules'), '800', 'change');
    click(popover(root).querySelector<HTMLInputElement>('input[type=radio][value=chatgpt]')!);
    set(field(root, 'Llindar de convergència'), '80');
    expect(e.textOf(optionsButton(root))).toBe('20 rondes · 5,5 €');
    send(textarea);
    expect(lastStart(socket)).toMatchObject({ mode: 'refine', text: 'Escriu un pla de llançament.' });
    expect((lastStart(socket).options as { refine: unknown }).refine).toEqual({
      max_rounds: 20, budget_eur: 5.5, max_words: 800, stop_on_convergence: true, convergence_threshold: 80, editor: 'chatgpt',
    });
  });

  it('can stop only when the owner says (no stopping by itself) and go back to the automatic word limit', async () => {
    const { e, root } = await ready();
    click(radio(root, 'refine'));
    click(field(root, "S'atura sol"));
    expect(e.app.composer.refineConverge).toBe(false);
    expect(field(root, 'Llindar de convergència')).toBeUndefined(); // only with stopping by itself
    click(field(root, 'Límit automàtic'));
    expect(e.app.composer.refineWords).toBe(1000);
    click(field(root, 'Límit automàtic'));
    expect(e.app.composer.refineWords).toBeNull();
    expect(field(root, 'Límit de paraules').disabled).toBe(true);
  });

  it('keeps every value within the limits the server accepts', async () => {
    const { e, root } = await ready();
    click(radio(root, 'refine'));
    set(field(root, 'Pressupost'), '500', 'change');
    expect(e.app.composer.refineBudget).toBe(100);
    set(field(root, 'Pressupost'), '0', 'change');
    expect(e.app.composer.refineBudget).toBe(0.1);
    set(field(root, 'Pressupost'), 'molt', 'change');
    expect(e.app.composer.refineBudget).toBe(0.1); // unreadable: the value stays
    expect(field(root, 'Pressupost').value).toBe('0,1');
    click(field(root, 'Límit automàtic'));
    set(field(root, 'Límit de paraules'), '50', 'change');
    expect(e.app.composer.refineWords).toBe(100);
    set(field(root, 'Límit de paraules'), '25000', 'change');
    expect(e.app.composer.refineWords).toBe(20_000);
  });

  it('remembers what the owner changed, like the other options, when the saved defaults change', async () => {
    const { e, root } = await ready();
    click(radio(root, 'refine'));
    set(field(root, 'Rondes màximes'), '30');
    server.saveElsewhere({ refine: { ...SAVED.refine, max_rounds: 8, budget_eur: 9 } });
    await e.app.loadSettings();
    e.flushSync();
    expect(e.app.composer.mode).toBe('refine');
    expect(e.textOf(optionsButton(root))).toBe('30 rondes · 9 €');
  });

  it('is never chosen by itself: a conversation whose last turn was refine opens with the default mode', async () => {
    const conversation: ConversationSummary = {
      id: 5, title: 'Pla', created_at: '2026-10-02T09:00:00Z', updated_at: '2026-10-02T09:30:00Z', last_mode: 'refine', message_count: 9,
    };
    server.conversations = [conversation];
    const { e, root } = await ready();
    await e.app.syncRoute({ name: 'chat', id: 5 });
    e.flushSync();
    expect(e.app.composer.mode).toBe('debate');
    expect(radio(root, 'debate').checked).toBe(true);
    expect(radio(root, 'refine').checked).toBe(false);
  });
});
