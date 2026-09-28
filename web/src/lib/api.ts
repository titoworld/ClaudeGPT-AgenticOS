// Typed REST client (docs/PROTOCOL.md). Same-origin, cookie session.
import type {
  AuthState,
  ConversationDetail,
  ConversationSummary,
  ModelCatalog,
  MonthSpend,
  Pricing,
  ProviderStatus,
  RuntimeSettings,
  Stats,
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
 * app shows it and retries it instead of waiting until the page is reloaded.
 */
export const GATING_REQUEST_TIMEOUT_MS = 15_000;

/** A request that did not answer within its time limit. */
export class RequestTimeoutError extends Error {
  constructor() {
    super('El servidor no ha respost a temps.');
    this.name = 'RequestTimeoutError';
  }
}

/** Called on any 401 so the app can go back to the login screen. */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/** With `timeoutMs`, the request (body included) is aborted after that long and rejects with RequestTimeoutError. */
async function request<T>(method: string, path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const controller = timeoutMs === undefined ? null : new AbortController();
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : undefined;
  try {
    return await send<T>(method, path, body, controller?.signal ?? null);
  } catch (err) {
    if (controller?.signal.aborted) throw new RequestTimeoutError();
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function send<T>(method: string, path: string, body: unknown, signal: AbortSignal | null): Promise<T> {
  const init: RequestInit = {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
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
    if (res.status === 401 && path !== '/api/auth/login') onUnauthorized?.();
    throw new ApiError(res.status, detail, Number.isFinite(retryAfter) ? retryAfter : null, payload);
  }
  return payload as T;
}

export const api = {
  authState: () => request<AuthState>('GET', '/api/auth/state'),
  login: (password: string, totp: string) => request<void>('POST', '/api/auth/login', { password, totp }),
  logout: () => request<void>('POST', '/api/auth/logout'),
  providers: () => request<ProviderStatus[]>('GET', '/api/providers'),
  models: (refresh = false) => request<ModelCatalog>('GET', `/api/models${refresh ? '?refresh=1' : ''}`),
  pricing: () => request<Pricing>('GET', '/api/pricing', undefined, GATING_REQUEST_TIMEOUT_MS),
  spend: () => request<MonthSpend>('GET', '/api/spend'),
  settings: () => request<RuntimeSettings>('GET', '/api/settings', undefined, GATING_REQUEST_TIMEOUT_MS),
  /** `settings.revision` is the revision the edit is based on: 409 (SettingsConflict) if they changed since. */
  saveSettings: (settings: RuntimeSettings) => request<RuntimeSettings>('PUT', '/api/settings', settings),
  conversations: (limit = 50, before?: number) =>
    request<ConversationSummary[]>(
      'GET',
      `/api/conversations?limit=${limit}${before === undefined ? '' : `&before=${before}`}`,
    ),
  conversation: (id: number) => request<ConversationDetail>('GET', `/api/conversations/${id}`),
  renameConversation: (id: number, title: string) =>
    request<ConversationSummary>('PATCH', `/api/conversations/${id}`, { title }),
  deleteConversation: (id: number) => request<void>('DELETE', `/api/conversations/${id}`),
  stats: (days = 30) => request<Stats>('GET', `/api/stats?days=${days}`),
};
