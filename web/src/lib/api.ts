// Typed REST client (docs/PROTOCOL.md). Same-origin, cookie session.
import { i18n } from './i18n/index.svelte';
import {
  BACKGROUND_HEADER,
  type Attachment,
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
  /** Stops the request (it then rejects with an AbortError), e.g. an upload the owner cancelled. */
  signal?: AbortSignal;
  /** A body sent as it is (a file), instead of JSON. */
  raw?: Blob;
}

/** Called on any 401 so the app can go back to the login screen. */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/** A 401 to these says nothing new: a failed login, or a logout with no session left (its caller decides). */
const OWN_401 = new Set(['/api/auth/login', '/api/auth/logout']);

async function request<T>(method: string, path: string, body?: unknown, options: SendOptions = {}): Promise<T> {
  const { timeoutMs, background = false, signal, raw } = options;
  const controller = timeoutMs === undefined ? null : new AbortController();
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : undefined;
  try {
    return await send<T>(method, path, raw ?? body, raw !== undefined, background, controller?.signal ?? signal ?? null);
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
  raw: boolean,
  background: boolean,
  signal: AbortSignal | null,
): Promise<T> {
  // The server answers in the interface's language (its errors, its texts).
  const headers: Record<string, string> = { 'Accept-Language': i18n.locale };
  // A file goes as it is: the server takes its type from the content, never from this.
  if (body !== undefined) headers['Content-Type'] = raw ? 'application/octet-stream' : 'application/json';
  if (background) headers[BACKGROUND_HEADER] = '1';
  const init: RequestInit = {
    method,
    credentials: 'same-origin',
    headers,
    body: body === undefined ? null : raw ? (body as Blob) : JSON.stringify(body),
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
  if (!res.ok) throw failure(res, path, payload);
  return payload as T;
}

/** The ApiError of an answer that is not OK (a 401 also sends the app to the login). */
function failure(res: Response, path: string, payload: unknown): ApiError {
  const detail =
    payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string'
      ? payload.detail
      : `Error ${res.status}`;
  const retryHeader = res.headers.get('Retry-After');
  const retryAfter = retryHeader ? Number.parseInt(retryHeader, 10) : null;
  if (res.status === 401 && !OWN_401.has(path)) onUnauthorized?.();
  return new ApiError(res.status, detail, Number.isFinite(retryAfter) ? retryAfter : null, payload);
}

/**
 * The text of an attachment (a text file is UTF-8). With `maxBytes`, only its start: a
 * `Range` request, and a character the cut leaves incomplete is dropped.
 */
async function attachmentText(id: number, options: { maxBytes?: number; signal?: AbortSignal } = {}): Promise<string> {
  const { maxBytes, signal } = options;
  const path = `/api/attachments/${id}/content`;
  const headers: Record<string, string> = { 'Accept-Language': i18n.locale };
  if (maxBytes !== undefined) headers.Range = `bytes=0-${maxBytes - 1}`;
  const res = await fetch(path, { credentials: 'same-origin', headers, signal: signal ?? null });
  if (!res.ok) {
    let payload: unknown = null;
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }
    throw failure(res, path, payload);
  }
  const bytes = new Uint8Array(await res.arrayBuffer());
  const cut = maxBytes !== undefined && (res.status === 206 || bytes.length > maxBytes);
  // `stream` keeps back the bytes of a character that the cut left incomplete.
  return new TextDecoder('utf-8').decode(maxBytes === undefined ? bytes : bytes.subarray(0, maxBytes), { stream: cut });
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
  /**
   * Uploads a file (the body is the file itself, not multipart): 201 with the Attachment;
   * 413, 415, 422 or 507 with the reason (docs/PROTOCOL.md «Adjunts»).
   */
  uploadAttachment: (file: Blob, name: string, signal?: AbortSignal) =>
    request<Attachment>('PUT', `/api/attachments?${new URLSearchParams({ name })}`, undefined, { raw: file, signal }),
  /** The thumbnail the browser made of an attachment: a PNG or WebP (100 kB, 512 px at most). */
  uploadThumbnail: (id: number, thumbnail: Blob, signal?: AbortSignal) =>
    request<void>('PUT', `/api/attachments/${id}/thumbnail`, undefined, { raw: thumbnail, signal }),
  attachment: (id: number) => request<Attachment>('GET', `/api/attachments/${id}`),
  /** Deletes an attachment never sent (409 once a question has it). */
  deleteAttachment: (id: number) => request<void>('DELETE', `/api/attachments/${id}`),
  attachmentText,
};
