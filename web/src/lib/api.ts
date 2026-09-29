// Typed REST client (docs/PROTOCOL.md). Same-origin, cookie session.
import {
  BACKGROUND_HEADER,
  type AuthState,
  type ConversationDetail,
  type ConversationSummary,
  type ModelCatalog,
  type MonthSpend,
  type Pricing,
  type ProviderStatus,
  type RuntimeSettings,
  type Stats,
} from './protocol';

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;
  /** The decoded JSON body of the answer (a 409 of PUT /api/settings carries the current settings), or null. */
  readonly body: unknown;

  constructor(status: number, message: string, retryAfter: number | null = null, body: unknown = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryAfter = retryAfter;
    this.body = body;
  }
}

/**
 * GETs the app cannot work without (the settings and the prices) give up after this
 * long: a request that never answers (e.g. a stalled mobile connection) fails, and the
 * app shows it and retries it instead of waiting until the page is reloaded. So does
 * the logout: the lock screen then says it failed and offers to try again.
 */
export const GATING_REQUEST_TIMEOUT_MS = 15_000;

/** A request that did not answer within its time limit. */
export class RequestTimeoutError extends Error {
  constructor() {
    super('El servidor no ha respost a temps.');
    this.name = 'RequestTimeoutError';
  }
}

export interface RequestOptions {
  /**
   * The app makes this request by itself (a refresh after `hello` or a reconnection, a
   * retry...), not because the owner did something: it carries BACKGROUND_HEADER and
   * the server does not count it as activity (docs/PROTOCOL.md).
   */
  background?: boolean;
}

interface SendOptions extends RequestOptions {
  /** The request (body included) is aborted after this long and rejects with RequestTimeoutError. */
  timeoutMs?: number;
}

/** Called on any 401 so the app can go back to the login screen. */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/** A 401 to these says nothing new: a failed login, or a logout with no session left (its caller decides). */
const OWN_401 = new Set(['/api/auth/login', '/api/auth/logout']);

async function request<T>(method: string, path: string, body?: unknown, options: SendOptions = {}): Promise<T> {
  const { timeoutMs, background = false } = options;
  const controller = timeoutMs === undefined ? null : new AbortController();
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : undefined;
  try {
    return await send<T>(method, path, body, background, controller?.signal ?? null);
  } catch (err) {
    if (controller?.signal.aborted) throw new RequestTimeoutError();
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function send<T>(
  method: string,
  path: string,
  body: unknown,
  background: boolean,
  signal: AbortSignal | null,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (background) headers[BACKGROUND_HEADER] = '1';
  const init: RequestInit = {
    method,
    credentials: 'same-origin',
    headers,
    body: body === undefined ? null : JSON.stringify(body),
    signal,
  };
  const res = await fetch(path, init);
  if (res.status === 204) return undefined as T;
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch (err) {
    if (signal?.aborted) throw err; // the time limit, not a body that is not JSON
    payload = null;
  }
  if (!res.ok) {
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string'
        ? payload.detail
        : `Error ${res.status}`;
    const retryHeader = res.headers.get('Retry-After');
    const retryAfter = retryHeader ? Number.parseInt(retryHeader, 10) : null;
    if (res.status === 401 && !OWN_401.has(path)) onUnauthorized?.();
    throw new ApiError(res.status, detail, Number.isFinite(retryAfter) ? retryAfter : null, payload);
  }
  return payload as T;
}

const GATING: SendOptions = { timeoutMs: GATING_REQUEST_TIMEOUT_MS };

export const api = {
  authState: () => request<AuthState>('GET', '/api/auth/state'),
  login: (password: string, totp: string) => request<void>('POST', '/api/auth/login', { password, totp }),
  /** Never counts as activity (a failed logout must not extend the session), and gives up after a while. */
  logout: () => request<void>('POST', '/api/auth/logout', undefined, { ...GATING, background: true }),
  providers: (options?: RequestOptions) => request<ProviderStatus[]>('GET', '/api/providers', undefined, options),
  models: (refresh = false) => request<ModelCatalog>('GET', `/api/models${refresh ? '?refresh=1' : ''}`),
  pricing: () => request<Pricing>('GET', '/api/pricing', undefined, GATING),
  spend: (options?: RequestOptions) => request<MonthSpend>('GET', '/api/spend', undefined, options),
  settings: (options?: RequestOptions) =>
    request<RuntimeSettings>('GET', '/api/settings', undefined, { ...options, ...GATING }),
  /** `settings.revision` is the revision the edit is based on: 409 (SettingsConflict) if they changed since. */
  saveSettings: (settings: RuntimeSettings) => request<RuntimeSettings>('PUT', '/api/settings', settings),
  /**
   * A page of conversations, newest first. `before`: the id of the last one of the
   * previous page. `q`: only those whose title contains it (ignoring case and accents).
   */
  conversations: (limit = 50, before?: number, q?: string, options?: RequestOptions) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (before !== undefined) query.set('before', String(before));
    if (q) query.set('q', q);
    return request<ConversationSummary[]>('GET', `/api/conversations?${query}`, undefined, options);
  },
  conversation: (id: number, options?: RequestOptions) =>
    request<ConversationDetail>('GET', `/api/conversations/${id}`, undefined, options),
  renameConversation: (id: number, title: string) =>
    request<ConversationSummary>('PATCH', `/api/conversations/${id}`, { title }),
  deleteConversation: (id: number) => request<void>('DELETE', `/api/conversations/${id}`),
  stats: (days = 30) => request<Stats>('GET', `/api/stats?days=${days}`),
};
