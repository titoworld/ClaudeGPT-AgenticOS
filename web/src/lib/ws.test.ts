import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  BACKOFF_MAX_MS,
  Connection,
  PING_INTERVAL_MS,
  PONG_TIMEOUT_MS,
  STABLE_AFTER_MS,
  WARMUP_PING_MS,
  backoffDelay,
} from './ws.svelte';
import type { ServerMessage } from './protocol';

/** Minimal controllable WebSocket double. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  closedWith: number | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(code = 1000): void {
    this.closedWith = code;
    this.readyState = 3;
  }
  // helpers driven by the test
  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }
  fail(code = 1006): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  receive(msg: object): void {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

const last = () => FakeSocket.instances.at(-1)!;

const created: Connection[] = [];

function make(overrides: Partial<ConstructorParameters<typeof Connection>[0]> = {}) {
  const messages: ServerMessage[] = [];
  const conn = new Connection({
    url: () => 'ws://test/api/ws',
    random: () => 0.5, // no jitter: exact schedule
    createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
    onMessage: (m) => messages.push(m),
    ...overrides,
  });
  created.push(conn);
  return { conn, messages };
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-27T12:00:00Z'));
});

afterEach(() => {
  for (const c of created.splice(0)) c.disconnect(); // never leak window listeners between tests
  vi.useRealTimers();
});

describe('backoffDelay', () => {
  it('doubles from 0.5 s up to a 10 s cap', () => {
    const schedule = Array.from({ length: 8 }, (_, i) => backoffDelay(i, () => 0.5));
    expect(schedule).toEqual([500, 1000, 2000, 4000, 8000, 10_000, 10_000, 10_000]);
  });

  it('adds +/-20 % jitter and never exceeds the cap', () => {
    expect(backoffDelay(0, () => 0)).toBe(400);
    expect(backoffDelay(0, () => 1)).toBe(600);
    expect(backoffDelay(4, () => 1)).toBe(9600);
    expect(backoffDelay(5, () => 1)).toBe(BACKOFF_MAX_MS);
    for (let i = 0; i < 50; i++) expect(backoffDelay(i)).toBeLessThanOrEqual(BACKOFF_MAX_MS);
  });
});

describe('Connection', () => {
  it('connects and reports open', () => {
    const { conn } = make();
    conn.connect();
    expect(conn.status).toBe('connecting');
    expect(last().url).toBe('ws://test/api/ws');
    last().open();
    expect(conn.status).toBe('open');
    conn.disconnect();
  });

  it('retries with the backoff schedule while the server is down', () => {
    const { conn } = make();
    conn.connect();
    const delays: number[] = [];
    for (let i = 0; i < 7; i++) {
      const before = FakeSocket.instances.length;
      const t0 = Date.now();
      last().fail();
      expect(conn.status).toBe('reconnecting');
      // advance until a new socket appears
      while (FakeSocket.instances.length === before) vi.advanceTimersByTime(50);
      delays.push(Date.now() - t0);
    }
    expect(delays).toEqual([500, 1000, 2000, 4000, 8000, 10_000, 10_000]);
    conn.disconnect();
  });

  it('resets the backoff only after a stable connection', () => {
    const { conn } = make();
    conn.connect();
    last().fail();
    vi.advanceTimersByTime(500);
    last().fail();
    vi.advanceTimersByTime(1000);
    expect(conn.attempt).toBe(2);
    last().open();
    vi.advanceTimersByTime(STABLE_AFTER_MS - 1);
    expect(conn.attempt).toBe(2);
    vi.advanceTimersByTime(1);
    expect(conn.attempt).toBe(0);
    conn.disconnect();
  });

  it('retries immediately when the browser comes back online', () => {
    const { conn } = make();
    conn.connect();
    last().fail();
    vi.advanceTimersByTime(500);
    last().fail(); // next retry in 1 s
    const count = FakeSocket.instances.length;
    window.dispatchEvent(new Event('online'));
    expect(FakeSocket.instances.length).toBe(count + 1);
    conn.disconnect();
  });

  it('retries immediately when the tab becomes visible', () => {
    const { conn } = make();
    conn.connect();
    last().fail();
    const count = FakeSocket.instances.length;
    document.dispatchEvent(new Event('visibilitychange'));
    expect(FakeSocket.instances.length).toBe(count + 1);
    conn.disconnect();
  });

  it('pings every 20 s and measures the round trip', () => {
    const { conn, messages } = make();
    conn.connect();
    last().open();
    const pings = () => last().sent.map((s) => JSON.parse(s) as { type: string; t: number });
    expect(pings()).toHaveLength(1); // immediate ping on open
    const t = pings()[0]!.t;
    vi.advanceTimersByTime(42);
    last().receive({ type: 'pong', t });
    expect(conn.rttMs).toBe(42);
    expect(messages).toHaveLength(0); // pongs are not forwarded
    vi.advanceTimersByTime(WARMUP_PING_MS);
    expect(pings()).toHaveLength(2); // warm-up ping
    last().receive({ type: 'pong', t: pings()[1]!.t });
    vi.advanceTimersByTime(PING_INTERVAL_MS - WARMUP_PING_MS);
    expect(pings().filter((p) => p.type === 'ping')).toHaveLength(3);
    vi.advanceTimersByTime(PING_INTERVAL_MS);
    expect(pings()).toHaveLength(4);
    conn.disconnect();
  });

  it('reconnects when the socket goes silent (half-open)', () => {
    const { conn } = make();
    conn.connect();
    last().open();
    const first = last();
    vi.advanceTimersByTime(PING_INTERVAL_MS * 3 + 1000);
    expect(first.closedWith).toBe(4000);
    expect(FakeSocket.instances.length).toBeGreaterThan(1);
    conn.disconnect();
  });

  it('forwards server messages and ignores garbage', () => {
    const { conn, messages } = make();
    conn.connect();
    last().open();
    last().onmessage?.({ data: 'not json' });
    last().receive({ type: 'turn.unknown', request_id: 'x' });
    expect(messages).toEqual([{ type: 'turn.unknown', request_id: 'x' }]);
    conn.disconnect();
  });

  it('only sends while open', () => {
    const { conn } = make();
    conn.connect();
    expect(conn.send({ type: 'turn.cancel', request_id: 'a' })).toBe(false);
    last().open();
    expect(conn.send({ type: 'turn.cancel', request_id: 'a' })).toBe(true);
    conn.disconnect();
  });

  it('stops on 4401 and asks for login', () => {
    const onUnauthorized = vi.fn();
    const { conn } = make({ onUnauthorized });
    conn.connect();
    last().fail(4401);
    expect(onUnauthorized).toHaveBeenCalledOnce();
    expect(conn.status).toBe('closed');
    const count = FakeSocket.instances.length;
    vi.advanceTimersByTime(60_000);
    window.dispatchEvent(new Event('online'));
    expect(FakeSocket.instances.length).toBe(count);
  });

  it('stops on 4403 and reports the fatal error', () => {
    const onForbidden = vi.fn();
    const { conn } = make({ onForbidden });
    conn.connect();
    last().open();
    last().fail(4403);
    expect(onForbidden).toHaveBeenCalledOnce();
    expect(conn.status).toBe('closed');
  });

  it('does not reconnect after an explicit disconnect', () => {
    const { conn } = make();
    conn.connect();
    last().open();
    const count = FakeSocket.instances.length;
    conn.disconnect();
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances.length).toBe(count);
    expect(conn.status).toBe('closed');
  });
});

// Liveness comes from pings that get no answer, never from silence (audit N7): a hidden
// tab runs its timers late (Chrome: once a minute), and silence between two late ticks
// says nothing about the socket. See ws-throttle.test.ts for a model of Chrome's policy.
describe('Connection liveness (N7)', () => {
  /** The server answers every ping, a moment later (message events are always asynchronous). */
  function answerPings(socket: FakeSocket): void {
    const send = socket.send.bind(socket);
    socket.send = (data: string) => {
      send(data);
      const msg = JSON.parse(data) as { type: string; t: number };
      if (msg.type === 'ping') queueMicrotask(() => socket.receive({ type: 'pong', t: msg.t }));
    };
  }

  const pingCount = (socket: FakeSocket) =>
    socket.sent.filter((s) => (JSON.parse(s) as { type: string }).type === 'ping').length;

  it('keeps a socket that answers its pings, however late the timers run', async () => {
    const { conn } = make();
    conn.connect();
    const socket = last();
    answerPings(socket);
    socket.open();
    await vi.advanceTimersByTimeAsync(WARMUP_PING_MS);
    for (let i = 0; i < 30; i++) {
      // Like a hidden Chrome tab: each tick runs a minute after the one before.
      vi.setSystemTime(Date.now() + 60_000 - PING_INTERVAL_MS);
      await vi.advanceTimersByTimeAsync(PING_INTERVAL_MS);
    }
    expect(socket.closedWith).toBeNull();
    expect(FakeSocket.instances).toHaveLength(1);
    expect(conn.status).toBe('open');
    expect(pingCount(socket)).toBeGreaterThanOrEqual(32);
  });

  it('drops a socket once a ping has had no answer for longer than PONG_TIMEOUT_MS', () => {
    const { conn } = make();
    conn.connect();
    const socket = last();
    socket.open(); // pings at once; nothing ever answers
    vi.advanceTimersByTime(PING_INTERVAL_MS);
    expect(PING_INTERVAL_MS).toBeLessThanOrEqual(PONG_TIMEOUT_MS);
    expect(socket.closedWith).toBeNull(); // 20 s without an answer: not yet
    vi.advanceTimersByTime(PING_INTERVAL_MS);
    expect(socket.closedWith).toBe(4000); // 40 s
    expect(conn.status).toBe('reconnecting');
  });

  it('counts any message as an answer to the pings sent before it', () => {
    const { conn } = make();
    conn.connect();
    const socket = last();
    socket.open();
    vi.advanceTimersByTime(25_000);
    socket.receive({ type: 'turn.unknown', request_id: 'x' });
    vi.advanceTimersByTime(15_000); // 40 s: the ping of 0 s was answered at 25 s
    expect(socket.closedWith).toBeNull();
    vi.advanceTimersByTime(40_000); // 80 s: the ping of 40 s has had no answer for 40 s
    expect(socket.closedWith).toBe(4000);
    expect(conn.status).toBe('reconnecting');
  });

  it('checks at once when the tab becomes visible: a ping', async () => {
    const { conn } = make();
    conn.connect();
    const socket = last();
    answerPings(socket);
    socket.open();
    await vi.advanceTimersByTimeAsync(PING_INTERVAL_MS + 3_000);
    const before = pingCount(socket);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(pingCount(socket)).toBe(before + 1);
    expect(socket.closedWith).toBeNull();
    expect(conn.status).toBe('open');
  });

  it('checks at once when the tab becomes visible: a ping unanswered for too long drops it', () => {
    const { conn } = make();
    conn.connect();
    const socket = last();
    socket.open(); // this ping never gets an answer
    vi.advanceTimersByTime(PONG_TIMEOUT_MS + 5_000); // the next check (a tick) would be at 40 s
    expect(socket.closedWith).toBeNull();
    document.dispatchEvent(new Event('visibilitychange'));
    expect(socket.closedWith).toBe(4000);
    expect(conn.status).toBe('reconnecting');
  });
});
