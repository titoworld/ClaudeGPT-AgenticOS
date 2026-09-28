// A fake of the REST API (docs/PROTOCOL.md) behind a `fetch` stub, and a WebSocket
// double, for tests that drive the real api.ts and app controller. Settings carry a
// revision and a save based on an older one gets 409 with the current settings, like
// server/routes_api.py. Never imported by application code.
import type { Pricing, RuntimeSettings, ServerMessage } from './protocol';

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

export class FakeApi {
  /** The stored settings, with their revision. */
  settings: RuntimeSettings;
  /**
   * Answers GET /api/settings instead of the stored settings: return a pending
   * promise to delay it, throw (e.g. a TypeError) to make the request fail.
   */
  settingsGet: (() => RuntimeSettings | Promise<RuntimeSettings>) | null = null;
  pricing: Pricing = { fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' }, prices: [] };
  /** Every request, as "METHOD /path". */
  readonly calls: string[] = [];
  /** Bodies of every PUT /api/settings. */
  readonly puts: RuntimeSettings[] = [];

  constructor(settings: RuntimeSettings) {
    this.settings = structuredClone(settings);
  }

  count(call: string): number {
    return this.calls.filter((c) => c === call).length;
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
    switch (call) {
      case 'GET /api/auth/state':
        return json({ authenticated: true, setup_required: false });
      case 'GET /api/settings':
        return json(this.settingsGet ? await this.settingsGet() : this.settings);
      case 'PUT /api/settings':
        return this.#put(JSON.parse(String(init.body)) as RuntimeSettings);
      case 'GET /api/pricing':
        return json(this.pricing);
      case 'GET /api/providers':
      case 'GET /api/conversations':
        return json([]);
      case 'POST /api/auth/logout':
        return new Response(null, { status: 204 });
      default:
        return json({ detail: 'No disponible en aquesta prova.' }, 503);
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
