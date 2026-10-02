// Requests the app cannot work without give up after a while (P3 review): a GET that
// never answers fails like a network error, so the app shows it and retries it instead
// of waiting until the page is reloaded.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, GATING_REQUEST_TIMEOUT_MS, RequestTimeoutError, setUnauthorizedHandler } from './api';
import { errorMessage } from './conversations.svelte';
import { BACKGROUND_HEADER, type Attachment } from './protocol';

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

describe('attachments (docs/PROTOCOL.md «Adjunts»)', () => {
  const ATTACHMENT: Attachment = {
    id: 12,
    name: 'informe final.pdf',
    kind: 'pdf',
    mime: 'application/pdf',
    size: 8,
    pages: 1,
    width: null,
    height: null,
    sha256: 'c'.repeat(64),
    created_at: '2026-09-28T10:00:00Z',
    has_thumbnail: false,
    text_available: true,
    estimated_tokens: 3600,
    pdf_notes: { no_text: [], garbled: [], hidden: [] },
  };

  /** A fetch that records every request and answers with `answer`. */
  function recording(answer: () => Response) {
    const seen: { url: string; init: RequestInit }[] = [];
    const fetch = vi.fn(async (url: RequestInfo | URL, init: RequestInit = {}) => {
      seen.push({ url: String(url), init });
      if (init.signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
      return answer();
    });
    vi.stubGlobal('fetch', fetch);
    return seen;
  }

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

  it('uploads the file itself (not JSON) with PUT /api/attachments and its name', async () => {
    const seen = recording(() => json(ATTACHMENT, 201));
    const file = new File(['%PDF-1.7'], 'informe final.pdf', { type: 'application/pdf' });
    expect(await api.uploadAttachment(file, 'informe final.pdf')).toEqual(ATTACHMENT);
    const [{ url, init }] = seen as [{ url: string; init: RequestInit }];
    expect(url).toBe('/api/attachments?name=informe+final.pdf');
    expect(init.method).toBe('PUT');
    expect(init.body).toBe(file);
    expect(init.credentials).toBe('same-origin');
    const headers = new Headers(init.headers);
    // The server takes the type from the content, never from this header.
    expect(headers.get('Content-Type')).toBe('application/octet-stream');
    expect(headers.get(BACKGROUND_HEADER)).toBeNull(); // the owner attached it
  });

  it('a refused upload is an ApiError with the reason the server gives', async () => {
    recording(() => json({ detail: 'El fitxer és massa gran: un PDF pot tenir com a molt 20 MB.' }, 413));
    const err = await api.uploadAttachment(new Blob(['x']), 'gran.pdf').catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 413, message: 'El fitxer és massa gran: un PDF pot tenir com a molt 20 MB.' });
  });

  it('an upload can be stopped', async () => {
    const seen = recording(() => json(ATTACHMENT, 201));
    const controller = new AbortController();
    controller.abort();
    await expect(api.uploadAttachment(new Blob(['x']), 'a.pdf', controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(seen[0]!.init.signal).toBe(controller.signal);
  });

  it('uploads a thumbnail as the body of PUT /api/attachments/{id}/thumbnail', async () => {
    const seen = recording(() => new Response(null, { status: 204 }));
    const thumbnail = new Blob([new Uint8Array([82, 73, 70, 70])], { type: 'image/webp' });
    await api.uploadThumbnail(12, thumbnail);
    expect(seen[0]!.url).toBe('/api/attachments/12/thumbnail');
    expect(seen[0]!.init.method).toBe('PUT');
    expect(seen[0]!.init.body).toBe(thumbnail);
  });

  it('reads the metadata and deletes an attachment never sent', async () => {
    const seen = recording(() => json(ATTACHMENT));
    expect(await api.attachment(12)).toEqual(ATTACHMENT);
    recording(() => new Response(null, { status: 204 }));
    await api.deleteAttachment(12);
    expect(seen[0]!.url).toBe('/api/attachments/12');
    expect(vi.mocked(fetch).mock.calls[0]![1]?.method).toBe('DELETE');
  });

  it('reads the text of an attachment, only its start when asked', async () => {
    const seen = recording(() => new Response('Primera línia\nSegona', { status: 206, headers: { 'Content-Type': 'text/plain; charset=utf-8' } }));
    expect(await api.attachmentText(9, { maxBytes: 4096 })).toBe('Primera línia\nSegona');
    expect(seen[0]!.url).toBe('/api/attachments/9/content');
    expect(new Headers(seen[0]!.init.headers).get('Range')).toBe('bytes=0-4095');

    // A multi-byte character cut by the range is left out, not garbled.
    const cut = new Uint8Array([...new TextEncoder().encode('Adéu'), 0xc3]);
    recording(() => new Response(cut, { status: 206 }));
    expect(await api.attachmentText(9, { maxBytes: 6 })).toBe('Adéu');

    const whole = recording(() => new Response('Tot', { status: 200 }));
    expect(await api.attachmentText(9)).toBe('Tot');
    expect(new Headers(whole[0]!.init.headers).get('Range')).toBeNull();

    recording(() => json({ detail: "L'adjunt no existeix." }, 404));
    await expect(api.attachmentText(9)).rejects.toMatchObject({ status: 404, message: "L'adjunt no existeix." });
  });
});
