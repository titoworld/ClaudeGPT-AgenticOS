// Requests the app cannot work without give up after a while (P3 review): a GET that
// never answers fails like a network error, so the app shows it and retries it instead
// of waiting until the page is reloaded.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, GATING_REQUEST_TIMEOUT_MS, RequestTimeoutError } from './api';
import { errorMessage } from './conversations.svelte';

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
