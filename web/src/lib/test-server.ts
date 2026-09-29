// A fake of the REST API (docs/PROTOCOL.md) behind a `fetch` stub, and a WebSocket
// double, for tests that drive the real api.ts and app controller. Settings carry a
// revision and a save based on an older one gets 409 with the current settings, like
// server/routes_api.py. The browser's session cookie is a flag: a login sets it, a
// logout the server confirms (204) clears it, and /api/auth/state reports it. Never
// imported by application code.
import {
  BACKGROUND_HEADER,
  type Attachment,
  type AttachmentKind,
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

const NOT_FOUND = "L'adjunt no existeix.";

/** An uploaded file of the fake server, with its thumbnail. */
export interface StoredAttachment {
  attachment: Attachment;
  body: Uint8Array;
  thumbnail: Uint8Array | null;
}

/** What the first bytes of an upload are, roughly as the server sniffs them. */
function uploadKind(body: Uint8Array): { kind: AttachmentKind; mime: string } {
  const head = String.fromCharCode(...body.subarray(0, 12));
  if (head.startsWith('\x89PNG')) return { kind: 'image', mime: 'image/png' };
  if (head.startsWith('\xff\xd8\xff')) return { kind: 'image', mime: 'image/jpeg' };
  if (head.startsWith('GIF8')) return { kind: 'image', mime: 'image/gif' };
  if (head.startsWith('RIFF')) return { kind: 'image', mime: 'image/webp' };
  if (head.startsWith('%PDF-')) return { kind: 'pdf', mime: 'application/pdf' };
  return { kind: 'text', mime: 'text/plain' };
}

async function bodyBytes(body: BodyInit | null | undefined): Promise<Uint8Array> {
  if (body instanceof Blob) return new Uint8Array(await body.arrayBuffer());
  if (body == null) return new Uint8Array();
  return new Uint8Array(await new Response(body).arrayBuffer());
}

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
  /** Requests aborted by the client (a removed upload...), as "METHOD /path". */
  readonly aborted: string[] = [];
  /** Uploaded attachments by id (PUT /api/attachments, or `addAttachment`). */
  readonly attachments = new Map<number, StoredAttachment>();
  /** Every PUT /api/attachments: the `name` and the body. */
  readonly uploads: { name: string; body: Uint8Array }[] = [];
  /** Every PUT /api/attachments/{id}/thumbnail. */
  readonly thumbnailPuts: { id: number; body: Uint8Array }[] = [];
  /** The `Range` header of every GET /api/attachments/{id}/content (null without one). */
  readonly ranges: (string | null)[] = [];
  /**
   * Answers PUT /api/attachments instead of storing the file: return a Response (a 413...),
   * or a promise of one to hold the upload; null (now or later) stores it as usual. Throw
   * a TypeError to make the request fail as a lost connection does.
   */
  uploadAnswer: ((name: string, body: Uint8Array) => Response | null | Promise<Response | null>) | null = null;
  /** Answers PUT /api/attachments/{id}/thumbnail instead of storing it (null stores it). */
  thumbnailAnswer: ((id: number, body: Uint8Array) => Response | null | Promise<Response | null>) | null = null;
  /** What the server says of an upload beyond its type and size (pages, estimated tokens...). */
  describeUpload: ((name: string, body: Uint8Array) => Partial<Attachment>) | null = null;
  #nextAttachment = 1;

  constructor(settings: RuntimeSettings) {
    this.settings = structuredClone(settings);
  }

  /** An attachment the server already has (sent in an earlier turn, say). */
  addAttachment(partial: Partial<Attachment> & Pick<Attachment, 'id'>, body: Uint8Array): Attachment {
    const attachment = { ...this.#describe(partial.name ?? `fitxer-${partial.id}`, body, partial.id), ...partial };
    this.attachments.set(attachment.id, { attachment, body, thumbnail: null });
    this.#nextAttachment = Math.max(this.#nextAttachment, attachment.id + 1);
    return attachment;
  }

  #describe(name: string, body: Uint8Array, id: number): Attachment {
    const { kind, mime } = uploadKind(body);
    return {
      id,
      name,
      kind,
      mime,
      size: body.length,
      pages: kind === 'pdf' ? 1 : null,
      width: kind === 'image' ? 640 : null,
      height: kind === 'image' ? 480 : null,
      sha256: id.toString(16).padStart(64, '0'),
      created_at: '2026-09-29T10:00:00Z',
      has_thumbnail: false,
      text_available: kind !== 'image',
      estimated_tokens: kind === 'image' ? 414 : kind === 'pdf' ? 3600 : Math.ceil(body.length / 4),
      ...this.describeUpload?.(name, body),
    };
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
      const abort = () => {
        this.aborted.push(`${init.method ?? 'GET'} ${new URL(String(input), 'https://aos.test').pathname}`);
        reject(new DOMException('The operation was aborted.', 'AbortError'));
      };
      if (signal.aborted) return abort();
      signal.addEventListener('abort', abort, { once: true });
      answer.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    });
  };

  async #answer(input: RequestInfo | URL, init: RequestInit): Promise<Response> {
    const method = init.method ?? 'GET';
    const url = new URL(String(input), 'https://aos.test');
    const path = url.pathname;
    const call = `${method} ${path}`;
    this.calls.push(call);
    this.requests.push({ call, background: new Headers(init.headers).get(BACKGROUND_HEADER) === '1' });
    const one = /^\/api\/conversations\/(\d+)$/.exec(path);
    if (one) return this.#conversation(method, Number(one[1]), init);
    const attachment = /^\/api\/attachments(?:\/(\d+)(?:\/(content|thumbnail))?)?$/.exec(path);
    if (attachment) {
      const id = attachment[1] ? Number(attachment[1]) : null;
      return this.#attachment(method, id, attachment[2] ?? null, url, init);
    }
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

  /** /api/attachments and its sub-routes, like server/routes_attachments.py. */
  async #attachment(method: string, id: number | null, sub: string | null, url: URL, init: RequestInit): Promise<Response> {
    if (id === null) {
      if (method !== 'PUT') return unavailable();
      const name = url.searchParams.get('name') ?? '';
      const body = await bodyBytes(init.body);
      this.uploads.push({ name, body });
      const answer = await this.uploadAnswer?.(name, body);
      if (answer) return answer;
      const attachment = this.#describe(name, body, this.#nextAttachment++);
      this.attachments.set(attachment.id, { attachment, body, thumbnail: null });
      return json(attachment, 201);
    }
    const stored = this.attachments.get(id);
    if (!stored) return json({ detail: NOT_FOUND }, 404);
    if (sub === 'content' && method === 'GET') {
      const range = new Headers(init.headers).get('Range');
      this.ranges.push(range);
      const type = stored.attachment.kind === 'text' ? 'text/plain; charset=utf-8' : stored.attachment.mime;
      const part = range ? /^bytes=0-(\d+)$/.exec(range) : null;
      if (part) return new Response(stored.body.slice(0, Number(part[1]) + 1), { status: 206, headers: { 'Content-Type': type } });
      return new Response(stored.body.slice(), { status: 200, headers: { 'Content-Type': type } });
    }
    if (sub === 'thumbnail' && method === 'PUT') {
      const body = await bodyBytes(init.body);
      this.thumbnailPuts.push({ id, body });
      const answer = await this.thumbnailAnswer?.(id, body);
      if (answer) return answer;
      stored.thumbnail = body;
      stored.attachment = { ...stored.attachment, has_thumbnail: true };
      return new Response(null, { status: 204 });
    }
    if (sub === 'thumbnail' && method === 'GET') {
      if (!stored.thumbnail) return json({ detail: 'Aquest adjunt no té miniatura.' }, 404);
      return new Response(stored.thumbnail.slice(), { status: 200, headers: { 'Content-Type': 'image/webp' } });
    }
    if (sub === null && method === 'GET') return json(stored.attachment);
    if (sub === null && method === 'DELETE') {
      this.attachments.delete(id);
      return new Response(null, { status: 204 });
    }
    return unavailable();
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
