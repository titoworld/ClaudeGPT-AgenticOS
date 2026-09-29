// Requests the app cannot work without give up after a while (P3 review): a GET that
// never answers fails like a network error, so the app shows it and retries it instead
// of waiting until the page is reloaded.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, GATING_REQUEST_TIMEOUT_MS, RequestTimeoutError, setUnauthorizedHandler } from './api';
import { errorMessage } from './conversations.svelte';
import { BACKGROUND_HEADER } from './protocol';

/** A fetch that answers only when told to, and rejects as a browser does when aborted. */
function stalledFetch(stallBody = false) {
  const signals: (AbortSignal | undefined)[] = [];
  const fetch = vi.fn((_input: RequestInfo | URL, init: RequestInit = {}) => {
    const signal = init.signal ?? undefined;
    signals.push(signal);
    return new Promise<Response>((resolve, reject) => {
      const abort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
      signal?.addEventListener('abort', abort, { once: true });
      if (stallBody) {
        // Headers arrive, the body never does.
        const body = new ReadableStream({
          start(controller) {
            signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')), {
              once: true,
            });
          },
        });
        resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
    });
  });
  return { fetch, signals };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('a gating GET that never answers', () => {
  it.each([
    ['the settings', () => api.settings()],
    ['the prices', () => api.pricing()],
  ])('%s fails after the time limit', async (_name, call) => {
    vi.useFakeTimers();
    const { fetch, signals } = stalledFetch();
    vi.stubGlobal('fetch', fetch);
    const result = call();
    const outcome = result.then(
      () => 'answered',
      (err: unknown) => err,
    );
    await vi.advanceTimersByTimeAsync(GATING_REQUEST_TIMEOUT_MS - 1);
    expect(signals[0]?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const err = await outcome;
    expect(err).toBeInstanceOf(RequestTimeoutError);
    expect(errorMessage(err, 'fallback')).toBe('El servidor no ha respost a temps.');
    expect(signals[0]?.aborted).toBe(true);
  });

  it('fails too when only the body stalls', async () => {
    vi.useFakeTimers();
    const { fetch } = stalledFetch(true);
    vi.stubGlobal('fetch', fetch);
    const outcome = api.settings().then(
      () => 'answered',
      (err: unknown) => err,
    );
    await vi.advanceTimersByTimeAsync(GATING_REQUEST_TIMEOUT_MS);
    expect(await outcome).toBeInstanceOf(RequestTimeoutError);
  });

  it('the logout gives up after the time limit too (the lock screen then offers a retry)', async () => {
    vi.useFakeTimers();
    const { fetch, signals } = stalledFetch();
    vi.stubGlobal('fetch', fetch);
    const outcome = api.logout().then(
      () => 'answered',
      (err: unknown) => err,
    );
    await vi.advanceTimersByTimeAsync(GATING_REQUEST_TIMEOUT_MS);
    expect(await outcome).toBeInstanceOf(RequestTimeoutError);
    expect(signals[0]?.aborted).toBe(true);
  });

  it('does not limit an answer that arrives in time, nor other requests', async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    vi.stubGlobal('fetch', fetch);
    await expect(api.providers()).resolves.toEqual([]);
    expect(fetch.mock.calls[0]?.[1]?.signal ?? null).toBeNull();
    await expect(api.pricing()).resolves.toEqual([]);
    expect(vi.getTimerCount()).toBe(0); // the time limit is cleared once answered
  });
});

// Requests the app makes by itself say so (audit N7): the server then checks the
// session without counting them as owner activity.
describe('the background marker', () => {
  function recordingFetch(status = 200) {
    return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      status === 204
        ? new Response(null, { status })
        : new Response(JSON.stringify(status === 200 ? [] : { detail: 'Cal iniciar sessió.' }), { status }),
    );
  }
  const headerOf = (fetch: ReturnType<typeof recordingFetch>, call = 0) =>
    new Headers(fetch.mock.calls[call]?.[1]?.headers).get(BACKGROUND_HEADER);

  it('is sent only when asked for', async () => {
    const fetch = recordingFetch();
    vi.stubGlobal('fetch', fetch);
    await api.spend({ background: true });
    await api.spend();
    await api.providers({ background: true });
    await api.conversations(50, undefined, undefined, { background: true });
    await api.conversation(7, { background: true });
    await api.settings({ background: true });
    expect(fetch.mock.calls.map((_, i) => headerOf(fetch, i))).toEqual(['1', null, '1', '1', '1', '1']);
  });

  it('keeps the JSON content type of a body', async () => {
    const fetch = recordingFetch();
    vi.stubGlobal('fetch', fetch);
    await api.renameConversation(7, 'Títol');
    const headers = new Headers(fetch.mock.calls[0]?.[1]?.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get(BACKGROUND_HEADER)).toBeNull();
  });

  it('a logout always carries it: a failed one must not extend the session', async () => {
    const fetch = recordingFetch(204);
    vi.stubGlobal('fetch', fetch);
    await api.logout();
    expect(headerOf(fetch)).toBe('1');
  });

  it('the conversation search goes in `q`, encoded, with the page and the cursor (A12)', async () => {
    const fetch = recordingFetch();
    vi.stubGlobal('fetch', fetch);
    await api.conversations(50, 71, 'Pressupost & 50% zebra', { background: true });
    await api.conversations(12, undefined, 'àvia');
    await api.conversations();
    const urls = fetch.mock.calls.map((c) => new URL(String(c[0]), 'https://aos.test'));
    expect(urls.map((u) => u.pathname)).toEqual(Array(3).fill('/api/conversations'));
    expect(urls.map((u) => Object.fromEntries(u.searchParams))).toEqual([
      { limit: '50', before: '71', q: 'Pressupost & 50% zebra' },
      { limit: '12', q: 'àvia' },
      { limit: '50' },
    ]);
    expect(headerOf(fetch, 0)).toBe('1');
    expect(headerOf(fetch, 1)).toBeNull();
  });

  it('a 401 to the logout is its caller’s business, not a session that just ended elsewhere', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', recordingFetch(401));
    await expect(api.logout()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).not.toHaveBeenCalled();
    await expect(api.spend()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledOnce();
    setUnauthorizedHandler(null);
  });
});
