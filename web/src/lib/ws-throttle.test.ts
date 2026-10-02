// The socket of a tab in the background (audit N7). Chrome runs the timers of a hidden
// page once a second and, once it has been hidden for 5 minutes, chained timers (every
// setInterval tick) only once a minute, on the minute; network events are not throttled
// (developer.chrome.com/blog/timer-throttling-in-chrome-88). A check like "nothing
// received for 45 s" then sees a healthy socket as dead on every tick and reconnects
// every few minutes, and every reconnection used to count as owner activity, so an
// unused tab kept the session alive until the 30-day cap. Liveness is judged from a ping
// that got no answer, so throttled timers only make the checks rarer.
//
// ChromeClock replaces the timers and Date.now with a model of that policy and drives the
// real Connection with a fake server socket; nothing else is simulated.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ClientMessage, ServerMessage } from './protocol';
import { Connection, PONG_TIMEOUT_MS } from './ws.svelte';

const SECOND = 1000;
const MINUTE = 60 * SECOND;

interface SimTimer {
  id: number;
  run: () => void;
  due: number;
  every: number | null;
  /** Chrome's chain count: +1 for a timer set from a timer task, and on every interval tick. */
  chain: number;
}

class ChromeClock {
  now = 1_790_000_080_000; // 40 s after a whole minute
  hiddenSince: number | null = null;
  readonly #timers = new Map<number, SimTimer>();
  #nextId = 1;
  /** Chain count of the timer task running now (null: not a timer task). */
  #chain: number | null = null;
  #network: { at: number; order: number; run: () => void }[] = [];
  #order = 0;
  #restore: (() => void) | null = null;

  install(): void {
    const saved = {
      setTimeout: globalThis.setTimeout,
      setInterval: globalThis.setInterval,
      clearTimeout: globalThis.clearTimeout,
      clearInterval: globalThis.clearInterval,
      now: Date.now,
    };
    const g = globalThis as unknown as Record<string, unknown>;
    g.setTimeout = (run: () => void, delay = 0) => this.#add(run, delay, false);
    g.setInterval = (run: () => void, delay = 0) => this.#add(run, delay, true);
    g.clearTimeout = g.clearInterval = (id: number | undefined) => {
      if (id !== undefined) this.#timers.delete(id);
    };
    Date.now = () => this.now;
    let state: DocumentVisibilityState = 'visible';
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
    this.#setVisibility = (next) => (state = next);
    this.#restore = () => {
      Object.assign(globalThis, {
        setTimeout: saved.setTimeout,
        setInterval: saved.setInterval,
        clearTimeout: saved.clearTimeout,
        clearInterval: saved.clearInterval,
      });
      Date.now = saved.now;
      delete (document as unknown as Record<string, unknown>).visibilityState;
    };
  }

  uninstall(): void {
    this.#restore?.();
    this.#restore = null;
  }

  #setVisibility: (state: DocumentVisibilityState) => void = () => {};

  #add(run: () => void, delay: number, repeat: boolean): number {
    const id = this.#nextId++;
    const wait = Math.max(0, delay);
    this.#timers.set(id, { id, run, due: this.now + wait, every: repeat ? wait : null, chain: (this.#chain ?? 0) + 1 });
    return id;
  }

  /** A network event (a socket opening, a message arriving) `delay` ms from now: never throttled. */
  network(delay: number, run: () => void): void {
    this.#network.push({ at: this.now + delay, order: this.#order++, run });
  }

  hide(): void {
    this.hiddenSince = this.now;
    this.#setVisibility('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
  }

  show(): void {
    this.hiddenSince = null;
    this.#setVisibility('visible');
    document.dispatchEvent(new Event('visibilitychange'));
  }

  /** When a timer due at `t.due` actually runs. */
  #runsAt(t: SimTimer): number {
    if (this.hiddenSince === null) return t.due;
    let at = Math.ceil(t.due / SECOND) * SECOND; // hidden: aligned to the second
    if (t.chain >= 5 && at - this.hiddenSince > 5 * MINUTE) at = Math.ceil(at / MINUTE) * MINUTE; // intensive
    return at;
  }

  /** Runs every timer and network event due in the next `ms`, in time order. */
  runFor(ms: number): void {
    const end = this.now + ms;
    for (;;) {
      let timer: SimTimer | null = null;
      let timerAt = Infinity;
      for (const t of this.#timers.values()) {
        const at = this.#runsAt(t);
        if (at < timerAt) [timer, timerAt] = [t, at];
      }
      this.#network.sort((a, b) => a.at - b.at || a.order - b.order);
      const net = this.#network[0];
      const netAt = net?.at ?? Infinity;
      if (Math.min(timerAt, netAt) > end) break;
      if (net && netAt <= timerAt) {
        this.#network.shift();
        this.now = Math.max(this.now, netAt);
        net.run();
        continue;
      }
      if (!timer) break;
      this.now = Math.max(this.now, timerAt);
      if (timer.every === null) this.#timers.delete(timer.id);
      else timer.due = this.now + timer.every;
      this.#chain = timer.chain;
      if (timer.every !== null) timer.chain++;
      try {
        timer.run();
      } finally {
        this.#chain = null;
      }
    }
    this.now = end;
  }
}

const clock = new ChromeClock();

/** The server end of a socket: it opens, says hello and answers every ping 40 ms later. */
class ServerSocket {
  static all: ServerSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  pings = 0;
  closedWith: number | null = null;
  closedAt: number | null = null;
  /** False once the network path is gone: nothing gets through, and the browser does not notice. */
  alive = true;

  constructor(readonly url: string) {
    ServerSocket.all.push(this);
    clock.network(30, () => {
      if (!this.alive || this.readyState !== 0) return;
      this.readyState = 1;
      this.onopen?.();
      this.#deliver(5, {
        type: 'hello',
        version: '0.2.0',
        providers: [],
        fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' },
        active_turns: [],
      });
    });
  }

  send(data: string): void {
    const msg = JSON.parse(data) as ClientMessage;
    if (msg.type !== 'ping') return;
    this.pings++;
    this.#deliver(40, { type: 'pong', t: msg.t });
  }

  close(code = 1000): void {
    this.closedWith = code;
    this.closedAt = clock.now;
    this.readyState = 3;
  }

  #deliver(delay: number, msg: ServerMessage): void {
    if (!this.alive) return;
    clock.network(delay, () => {
      if (this.alive && this.readyState === 1) this.onmessage?.({ data: JSON.stringify(msg) });
    });
  }
}

const connections: Connection[] = [];

function connect(): Connection {
  const conn = new Connection({
    url: () => 'ws://test/api/ws',
    random: () => 0.5,
    createSocket: (url) => new ServerSocket(url) as unknown as WebSocket,
    onMessage: () => {},
  });
  connections.push(conn);
  conn.connect();
  return conn;
}

beforeEach(() => {
  ServerSocket.all = [];
  clock.install();
});

afterEach(() => {
  for (const conn of connections.splice(0)) conn.disconnect();
  clock.uninstall();
});

describe('a tab in the background (N7)', () => {
  it('keeps a healthy socket for two hours: no reconnect loop', () => {
    const conn = connect();
    clock.runFor(10 * SECOND);
    clock.hide();
    clock.runFor(2 * 60 * MINUTE);

    expect(ServerSocket.all).toHaveLength(1);
    expect(ServerSocket.all[0]!.closedWith).toBeNull();
    expect(conn.status).toBe('open');
    // It went on checking, about once a minute once throttled.
    expect(ServerSocket.all[0]!.pings).toBeGreaterThanOrEqual(110);
    expect(conn.rttMs).toBe(40);
  });

  it('still finds out that a socket died, within a few throttled ticks, and replaces it once', () => {
    const conn = connect();
    clock.runFor(10 * SECOND);
    clock.hide();
    clock.runFor(30 * MINUTE);
    const first = ServerSocket.all[0]!;
    const brokeAt = clock.now;
    first.alive = false; // e.g. a NAT mapping expired: half-open

    clock.runFor(90 * MINUTE);
    expect(first.closedWith).toBe(4000);
    expect(first.closedAt! - brokeAt).toBeLessThanOrEqual(3 * MINUTE);
    expect(ServerSocket.all).toHaveLength(2); // the new one is healthy and stays
    expect(ServerSocket.all[1]!.closedWith).toBeNull();
    expect(conn.status).toBe('open');
  });

  it('pings at once when the tab is shown again', () => {
    const conn = connect();
    clock.runFor(10 * SECOND);
    clock.hide();
    clock.runFor(20 * MINUTE + 25 * SECOND);
    const socket = ServerSocket.all[0]!;
    const pings = socket.pings;
    clock.show();
    expect(socket.pings).toBe(pings + 1);
    clock.runFor(SECOND);
    expect(ServerSocket.all).toHaveLength(1);
    expect(conn.status).toBe('open');
  });

  it('shown again after a ping went unanswered for too long, it reconnects at once', () => {
    const conn = connect();
    clock.runFor(10 * SECOND);
    clock.hide();
    clock.runFor(20 * MINUTE);
    const first = ServerSocket.all[0]!;
    first.alive = false;
    // The next throttled tick sends a ping that gets no answer...
    const pings = first.pings;
    for (let i = 0; i < 120 && first.pings === pings; i++) clock.runFor(SECOND);
    expect(first.pings).toBe(pings + 1);
    // ...and the tab is shown before the next tick could notice.
    clock.runFor(PONG_TIMEOUT_MS + 5 * SECOND);
    expect(first.closedWith).toBeNull();
    clock.show();
    expect(first.closedWith).toBe(4000);
    clock.runFor(SECOND); // visible again: the retry runs on time
    expect(ServerSocket.all).toHaveLength(2);
    expect(conn.status).toBe('open');
  });
});
