// A fake of the REST API (docs/PROTOCOL.md) behind a `fetch` stub, and a WebSocket
// double, for tests that drive the real api.ts and app controller. Settings carry a
// revision and a save based on an older one gets 409 with the current settings, like
// server/routes_api.py. The browser's session cookie is a flag: a login sets it, a
// logout the server confirms (204) clears it, and /api/auth/state reports it. Never
// imported by application code.
import {
  BACKGROUND_HEADER,
  type ConversationDetail,
  type ConversationSummary,
  type ModelCatalog,
  type MonthSpend,
  type Pricing,
  type ProviderStatus,
  type RuntimeSettings,
  type ServerMessage,
} from './protocol';

export const CONFLICT_DETAIL =
  'La configuració ha canviat en una altra pestanya o dispositiu. Revisa-la i torna-la a desar.';

export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const unavailable = (): Response => json({ detail: 'No disponible en aquesta prova.' }, 503);

export class FakeApi {
  /** The stored settings, with their revision. */
  settings: RuntimeSettings;
  /**
   * Answers GET /api/settings instead of the stored settings: return a pending
   * promise to delay it, throw (e.g. a TypeError) to make the request fail.
   */
  settingsGet: (() => RuntimeSettings | Promise<RuntimeSettings>) | null = null;
  pricing: Pricing = { fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' }, prices: [] };
  /** GET /api/providers. */
  providers: ProviderStatus[] = [];
  /** GET /api/conversations (every page) and, by id, GET/PATCH/DELETE /api/conversations/{id}. */
  conversations: ConversationSummary[] = [];
  /** GET /api/spend; null answers 503. */
  spend: MonthSpend | null = null;
  /** GET /api/models; null answers 503. */
  catalog: ModelCatalog | null = null;
  /** Whether the browser's session cookie names a live session. */
  session = true;
  /**
   * Answers POST /api/auth/logout instead of ending the session: return an error
   * (e.g. a 502 from the proxy) or throw a TypeError (the network failed).
   */
  logoutAnswer: (() => Response | Promise<Response>) | null = null;
  /** Every request, as "METHOD /path". */
  readonly calls: string[] = [];
  /** Every request with whether it said the app made it by itself (BACKGROUND_HEADER). */
  readonly requests: { call: string; background: boolean }[] = [];
  /** Bodies of every PUT /api/settings. */
  readonly puts: RuntimeSettings[] = [];

  constructor(settings: RuntimeSettings) {
    this.settings = structuredClone(settings);
  }

  count(call: string): number {
    return this.calls.filter((c) => c === call).length;
  }

  /** Calls that carried BACKGROUND_HEADER (`background`) or did not. */
  made(background: boolean): string[] {
    return this.requests.filter((r) => r.background === background).map((r) => r.call);
  }

  /** Another tab or device saves: the revision moves on. */
  saveElsewhere(change: Partial<RuntimeSettings>): void {
    this.settings = { ...structuredClone(this.settings), ...structuredClone(change), revision: this.settings.revision + 1 };
  }

  readonly fetch = (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const answer = this.#answer(input, init);
    const signal = init.signal;
    if (!signal) return answer;
    // Like a browser: an aborted request rejects with an AbortError at once.
    return new Promise<Response>((resolve, reject) => {
      const abort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
      if (signal.aborted) return abort();
      signal.addEventListener('abort', abort, { once: true });
      answer.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    });
  };

  async #answer(input: RequestInfo | URL, init: RequestInit): Promise<Response> {
    const method = init.method ?? 'GET';
    const path = new URL(String(input), 'https://aos.test').pathname;
    const call = `${method} ${path}`;
    this.calls.push(call);
    this.requests.push({ call, background: new Headers(init.headers).get(BACKGROUND_HEADER) === '1' });
    const one = /^\/api\/conversations\/(\d+)$/.exec(path);
    if (one) return this.#conversation(method, Number(one[1]), init);
    switch (call) {
      case 'GET /api/auth/state':
        return json({ authenticated: this.session, setup_required: false });
      case 'POST /api/auth/login':
        this.session = true;
        return new Response(null, { status: 204 });
      case 'POST /api/auth/logout':
        if (this.logoutAnswer) return this.logoutAnswer();
        if (!this.session) return json({ detail: 'Cal iniciar sessió.' }, 401);
        this.session = false;
        return new Response(null, { status: 204 });
      case 'GET /api/settings':
        return json(this.settingsGet ? await this.settingsGet() : this.settings);
      case 'PUT /api/settings':
        return this.#put(JSON.parse(String(init.body)) as RuntimeSettings);
      case 'GET /api/pricing':
        return json(this.pricing);
      case 'GET /api/providers':
        return json(this.providers);
      case 'GET /api/conversations':
        return json(this.conversations);
      case 'GET /api/spend':
        return this.spend ? json(this.spend) : unavailable();
      case 'GET /api/models':
        return this.catalog ? json(this.catalog) : unavailable();
      default:
        return unavailable();
    }
  }

  #conversation(method: string, id: number, init: RequestInit): Response {
    const summary = this.conversations.find((c) => c.id === id);
    if (!summary) return json({ detail: 'La conversa no existeix.' }, 404);
    switch (method) {
      case 'GET':
        return json({ ...summary, summary: null, messages: [] } satisfies ConversationDetail);
      case 'PATCH': {
        const { title } = JSON.parse(String(init.body)) as { title: string };
        const renamed = { ...summary, title };
        this.conversations = this.conversations.map((c) => (c.id === id ? renamed : c));
        return json(renamed);
      }
      case 'DELETE':
        this.conversations = this.conversations.filter((c) => c.id !== id);
        return new Response(null, { status: 204 });
      default:
        return unavailable();
    }
  }

  #put(body: RuntimeSettings): Response {
    this.puts.push(structuredClone(body));
    const revision: unknown = body.revision;
    if (typeof revision !== 'number' || !Number.isInteger(revision) || revision < 0) {
      return json({ detail: '«revision» ha de ser un nombre enter ≥ 0.' }, 422);
    }
    if (revision !== this.settings.revision) return json({ detail: CONFLICT_DETAIL, settings: this.settings }, 409);
    this.settings = { ...structuredClone(body), revision: revision + 1 };
    return json(this.settings);
  }
}

/** WebSocket double: the app's Connection creates one on every connect. */
export class FakeSocket {
  static all: FakeSocket[] = [];
  readyState = 0;
  readonly sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }

  static last(): FakeSocket {
    const socket = FakeSocket.all.at(-1);
    if (!socket) throw new Error('No WebSocket was opened');
    return socket;
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }

  close(): void {
    this.readyState = 3;
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  receive(msg: ServerMessage): void {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

/** The first message of every connection. */
export const hello = (): ServerMessage => ({
  type: 'hello',
  version: '0.2.0',
  providers: [],
  fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' },
  active_turns: [],
});

/** jsdom has no modal API for <dialog>: enough of it for the drawers. */
export function polyfillDialog(): void {
  const proto = HTMLDialogElement.prototype as unknown as Record<string, unknown>;
  if (typeof proto.showModal !== 'function') {
    proto.showModal = function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    };
  }
  if (typeof proto.close !== 'function') {
    proto.close = function (this: HTMLDialogElement) {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
}
