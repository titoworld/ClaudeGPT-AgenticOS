// A fake of the conversation routes of docs/PROTOCOL.md behind a `fetch` stub, for the
// tests of the list, the search and the sidebar: GET /api/conversations pages like
// SqliteStore.list_conversations (newest first by `updated_at`, then `id`; `before` is
// the last id of the previous page, and a cursor that no longer exists gives nothing),
// and `q` keeps the titles that contain it ignoring case and accents, literally. A list
// answer can be held back to reproduce races: its page is what the server had when the
// request arrived, delivered only when the test releases it. Anything else goes to
// `fallback` (e.g. a FakeApi of test-server.ts). Never imported by application code.
import { BACKGROUND_HEADER, type ConversationDetail, type ConversationSummary } from './protocol';

export interface ListRequest {
  limit: number;
  before: number | null;
  q: string | null;
  background: boolean;
}

export interface HeldList {
  readonly request: ListRequest;
  /** The page the server computed when the request arrived. */
  readonly page: ConversationSummary[];
  /** Delivers the page. */
  release(): void;
  /** Answers with an error instead (e.g. a 401 once the session is gone). */
  fail(status: number, detail: string): void;
}

const BASE = Date.UTC(2026, 8, 28, 8, 0, 0);

/** Minutes after 2026-09-28 08:00 UTC, as the server writes timestamps. */
export const at = (minutes: number): string => new Date(BASE + minutes * 60_000).toISOString();

/** Conversation `id`, last updated `id` minutes after the base time (a higher id is newer). */
export function conversation(id: number, title = `Conversa número ${id}`): ConversationSummary {
  return { id, title, created_at: at(id), updated_at: at(id), last_mode: 'solo', message_count: 2 };
}

/** Conversations 1..n, with the titles given for some of them. */
export function conversations(n: number, titles: Record<number, string> = {}): ConversationSummary[] {
  return Array.from({ length: n }, (_, i) => conversation(i + 1, titles[i + 1]));
}

/** Case and accents folded as the server does (NFKD, combining marks dropped). */
const fold = (s: string): string => s.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

/** The server's order: most recent activity first, then the highest id. */
export function newestFirst(list: readonly ConversationSummary[]): ConversationSummary[] {
  return [...list].sort((a, b) => b.updated_at.localeCompare(a.updated_at) || b.id - a.id);
}

export class ConversationServer {
  /** What the server stores. */
  conversations: ConversationSummary[];
  /** Every GET /api/conversations, in order. */
  readonly lists: ListRequest[] = [];
  /** Every request to a conversation route, as "METHOD /path". */
  readonly calls: string[] = [];
  /** While true, list answers wait in `held` until released. */
  holdLists = false;
  readonly held: HeldList[] = [];

  constructor(
    list: ConversationSummary[] = [],
    readonly fallback: ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) | null = null,
  ) {
    this.conversations = structuredClone(list);
  }

  /** The stored conversations in the server's order. */
  ordered(): ConversationSummary[] {
    return newestFirst(this.conversations);
  }

  /** A turn ended in conversation `id` (in this tab or elsewhere): it moves to the top. */
  touch(id: number, minutes: number): void {
    this.conversations = this.conversations.map((c) => (c.id === id ? { ...c, updated_at: at(minutes) } : c));
  }

  /** Another tab or device changed the stored conversations. */
  add(c: ConversationSummary): void {
    this.conversations = [...this.conversations, structuredClone(c)];
  }

  deleteElsewhere(id: number): void {
    this.conversations = this.conversations.filter((c) => c.id !== id);
  }

  page(request: ListRequest): ConversationSummary[] {
    let all = this.ordered();
    const q = fold(request.q?.trim() ?? '');
    if (q) all = all.filter((c) => fold(c.title).includes(q));
    if (request.before !== null) {
      const cursor = this.conversations.find((c) => c.id === request.before);
      if (!cursor) return [];
      all = all.filter(
        (c) => c.updated_at < cursor.updated_at || (c.updated_at === cursor.updated_at && c.id < cursor.id),
      );
    }
    return structuredClone(all.slice(0, request.limit));
  }

  readonly fetch = (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(String(input), 'https://aos.test');
    const method = init.method ?? 'GET';
    if (url.pathname === '/api/conversations' && method === 'GET') return this.#list(url, init);
    const one = /^\/api\/conversations\/(\d+)$/.exec(url.pathname);
    if (one) return Promise.resolve(this.#one(method, Number(one[1]), init));
    if (this.fallback) return this.fallback(input, init);
    return Promise.resolve(json({ detail: 'No disponible en aquesta prova.' }, 503));
  };

  #list(url: URL, init: RequestInit): Promise<Response> {
    const before = url.searchParams.get('before');
    const request: ListRequest = {
      limit: Number(url.searchParams.get('limit') ?? '50'),
      before: before === null ? null : Number(before),
      q: url.searchParams.get('q'),
      background: new Headers(init.headers).get(BACKGROUND_HEADER) === '1',
    };
    this.lists.push(request);
    this.calls.push(`GET ${url.pathname}`);
    // Trimmed; a blank one is no search, and more than 200 characters is refused.
    if (request.q !== null && [...request.q.trim()].length > 200) {
      return Promise.resolve(json({ detail: 'La cerca no pot tenir més de 200 caràcters.' }, 422));
    }
    const page = this.page(request);
    if (!this.holdLists) return Promise.resolve(json(page));
    return new Promise<Response>((resolve) => {
      this.held.push({
        request,
        page,
        release: () => resolve(json(page)),
        fail: (status, detail) => resolve(json({ detail }, status)),
      });
    });
  }

  #one(method: string, id: number, init: RequestInit): Response {
    this.calls.push(`${method} /api/conversations/${id}`);
    const summary = this.conversations.find((c) => c.id === id);
    if (!summary) return json({ detail: 'La conversa no existeix.' }, 404);
    switch (method) {
      case 'GET':
        return json({ ...summary, summary: null, messages: [] } satisfies ConversationDetail);
      case 'PATCH': {
        const { title } = JSON.parse(String(init.body)) as { title: string };
        const renamed = { ...summary, title }; // renaming keeps updated_at
        this.conversations = this.conversations.map((c) => (c.id === id ? renamed : c));
        return json(renamed);
      }
      case 'DELETE':
        this.conversations = this.conversations.filter((c) => c.id !== id);
        return new Response(null, { status: 204 });
      default:
        return json({ detail: 'No disponible en aquesta prova.' }, 503);
    }
  }
}
