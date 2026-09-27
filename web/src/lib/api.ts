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

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** Called on any 401 so the app can go back to the login screen. */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? null : JSON.stringify(body),
  };
  const res = await fetch(path, init);
  if (res.status === 204) return undefined as T;
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
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
    throw new ApiError(res.status, detail, Number.isFinite(retryAfter) ? retryAfter : null);
  }
  return payload as T;
}

export const api = {
  authState: () => request<AuthState>('GET', '/api/auth/state'),
  login: (password: string, totp: string) => request<void>('POST', '/api/auth/login', { password, totp }),
  logout: () => request<void>('POST', '/api/auth/logout'),
  providers: () => request<ProviderStatus[]>('GET', '/api/providers'),
  models: (refresh = false) => request<ModelCatalog>('GET', `/api/models${refresh ? '?refresh=1' : ''}`),
  pricing: () => request<Pricing>('GET', '/api/pricing'),
  spend: () => request<MonthSpend>('GET', '/api/spend'),
  settings: () => request<RuntimeSettings>('GET', '/api/settings'),
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
