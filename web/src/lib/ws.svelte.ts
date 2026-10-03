// Single persistent WebSocket to /api/ws with automatic reconnection.
//
// - Exponential backoff with jitter (0.5 s -> 10 s cap); the attempt counter
//   resets only after the connection has been stable for a few seconds, so a
//   flapping server does not cause a tight loop.
// - Immediate retry on the browser `online` event and when the tab becomes
//   visible again (laptops waking up, phones switching apps).
// - Application ping every 20 s measures the round trip and detects half-open
//   sockets: a ping with no answer (nor any other message) for 30 s -> close and
//   reconnect. Liveness is never judged from silence alone: a hidden tab runs its
//   timers late (Chrome: once a minute after 5 minutes hidden), and a healthy socket
//   must not be dropped and reopened in a loop (audit N7). A tab that becomes
//   visible again checks at once.
// - Close code 4401 (no session) and 4403 (origin rejected) stop retrying.
//
// Svelte 5 pitfall: connect() reads $state (status, attempt). Called from a
// component $effect, those reads would become dependencies of that effect and
// every retry would re-run it (close + instant reconnect, bypassing backoff).
// All public entry points therefore run inside untrack().

import { untrack } from 'svelte';
import { i18n } from './i18n/index.svelte';
import {
  WS_CLOSE_FORBIDDEN_ORIGIN,
  WS_CLOSE_UNAUTHORIZED,
  type ClientMessage,
  type ServerMessage,
} from './protocol';

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'offline' | 'closed';

export const BACKOFF_MIN_MS = 500;
export const BACKOFF_MAX_MS = 10_000;
export const PING_INTERVAL_MS = 20_000;
/** A ping with no answer (nor any other message) for longer than this means the socket is dead. */
export const PONG_TIMEOUT_MS = 30_000;
/** Connection must stay open this long before the backoff resets. */
export const STABLE_AFTER_MS = 5_000;
/** Extra ping shortly after connecting: the first one often lands while the
 *  page is still busy (scene shaders compiling) and would show a bogus RTT. */
export const WARMUP_PING_MS = 5_000;

/** Delay before reconnect attempt number `attempt` (0-based): 0.5 s, 1 s, 2 s... capped at 10 s, +/-20 % jitter. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** Math.max(0, Math.min(attempt, 16)));
  const jittered = base * (0.8 + 0.4 * random());
  return Math.round(Math.min(BACKOFF_MAX_MS, jittered));
}

/** The socket of this page, in the interface's language (the server writes its texts in it). */
export function defaultWsUrl(): string {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws?lang=${i18n.locale}`;
}

export interface ConnectionOptions {
  onMessage: (msg: ServerMessage) => void;
  /** Close code 4401: the session is gone. */
  onUnauthorized?: () => void;
  /** Close code 4403: the origin is not allowed (configuration problem). */
  onForbidden?: () => void;
  url?: () => string;
  random?: () => number;
  /** Injection point for tests. */
  createSocket?: (url: string) => WebSocket;
}

const OPEN = 1;

export class Connection {
  status: ConnectionStatus = $state('idle');
  /** Last measured round trip in ms. */
  rttMs: number | null = $state(null);
  /** Consecutive failed attempts (drives the backoff). */
  attempt = $state(0);
  /** Epoch ms of the next scheduled retry, if any. */
  nextRetryAt: number | null = $state(null);

  #opts: ConnectionOptions;
  #ws: WebSocket | null = null;
  #wanted = false;
  #retryTimer: ReturnType<typeof setTimeout> | undefined;
  #pingTimer: ReturnType<typeof setInterval> | undefined;
  #stableTimer: ReturnType<typeof setTimeout> | undefined;
  #warmupTimer: ReturnType<typeof setTimeout> | undefined;
  /** When the oldest ping that has had no answer yet was sent (null: all answered). */
  #pingSentAt: number | null = null;
  #listening = false;

  constructor(opts: ConnectionOptions) {
    this.#opts = opts;
  }

  get isOpen(): boolean {
    return this.status === 'open';
  }

  connect(): void {
    untrack(() => {
      this.#wanted = true;
      this.#listen(true);
      if (this.#ws) return;
      this.#open();
    });
  }

  disconnect(): void {
    untrack(() => {
      this.#wanted = false;
      this.#listen(false);
      this.#clearTimers();
      const ws = this.#ws;
      this.#ws = null;
      if (ws) this.#detach(ws, 1000, 'client closing');
      this.status = 'closed';
      this.nextRetryAt = null;
      this.rttMs = null;
      this.attempt = 0;
    });
  }

  /** Retry right away (manual "retry" button, `online`, tab visible). */
  retryNow(): void {
    untrack(() => {
      if (!this.#wanted || this.#ws) return;
      this.#open();
    });
  }

  /** Sends only when open; returns false otherwise (callers decide what to do). */
  send(msg: ClientMessage): boolean {
    return untrack(() => {
      const ws = this.#ws;
      if (!ws || ws.readyState !== OPEN || this.status !== 'open') return false;
      try {
        ws.send(JSON.stringify(msg));
        return true;
      } catch {
        return false;
      }
    });
  }

  // ------------------------------------------------------------ internals

  #open(): void {
    this.#clearTimers();
    this.nextRetryAt = null;
    this.status = this.attempt === 0 && this.status !== 'reconnecting' ? 'connecting' : 'reconnecting';
    let ws: WebSocket;
    try {
      const url = (this.#opts.url ?? defaultWsUrl)();
      ws = this.#opts.createSocket ? this.#opts.createSocket(url) : new WebSocket(url);
    } catch {
      this.#scheduleRetry();
      return;
    }
    this.#ws = ws;
    ws.onopen = () => this.#onOpen(ws);
    ws.onmessage = (ev: MessageEvent) => this.#onMessage(ws, ev);
    ws.onclose = (ev: CloseEvent) => this.#onClose(ws, ev.code);
    ws.onerror = () => {
      /* followed by close */
    };
  }

  #onOpen(ws: WebSocket): void {
    if (this.#ws !== ws) return;
    this.status = 'open';
    this.#pingSentAt = null;
    this.#stableTimer = setTimeout(() => {
      this.attempt = 0;
    }, STABLE_AFTER_MS);
    this.#pingTimer = setInterval(this.#tick, PING_INTERVAL_MS);
    this.#warmupTimer = setTimeout(() => this.#ping(), WARMUP_PING_MS);
    this.#ping();
  }

  #onMessage(ws: WebSocket, ev: MessageEvent): void {
    if (this.#ws !== ws) return;
    // Anything from the server answers the pings sent before it: the socket works.
    this.#pingSentAt = null;
    if (typeof ev.data !== 'string') return;
    let msg: ServerMessage;
    try {
      msg = JSON.parse(ev.data) as ServerMessage;
    } catch {
      return;
    }
    if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') return;
    if (msg.type === 'pong') {
      if (typeof msg.t === 'number') this.rttMs = Math.max(0, Date.now() - msg.t);
      return;
    }
    this.#opts.onMessage(msg);
  }

  #onClose(ws: WebSocket, code: number): void {
    if (this.#ws !== ws) return; // superseded or closed on purpose
    this.#ws = null;
    this.#clearTimers();
    this.rttMs = null;
    if (code === WS_CLOSE_UNAUTHORIZED || code === WS_CLOSE_FORBIDDEN_ORIGIN) {
      this.#wanted = false;
      this.#listen(false);
      this.status = 'closed';
      if (code === WS_CLOSE_UNAUTHORIZED) this.#opts.onUnauthorized?.();
      else this.#opts.onForbidden?.();
      return;
    }
    if (this.#wanted) this.#scheduleRetry();
    else this.status = 'closed';
  }

  #scheduleRetry(): void {
    const delay = backoffDelay(this.attempt, this.#opts.random);
    this.attempt += 1;
    this.status = typeof navigator !== 'undefined' && navigator.onLine === false ? 'offline' : 'reconnecting';
    this.nextRetryAt = Date.now() + delay;
    this.#retryTimer = setTimeout(() => {
      this.#retryTimer = undefined;
      if (this.#wanted && !this.#ws) this.#open();
    }, delay);
  }

  /** Every PING_INTERVAL_MS, or much later in a hidden tab. */
  #tick = (): void => this.#checkAlive();

  /** Drops the socket if a ping has had no answer for too long; otherwise pings. */
  #checkAlive(): void {
    const ws = this.#ws;
    if (!ws || ws.readyState !== OPEN) return;
    if (this.#pingSentAt !== null && Date.now() - this.#pingSentAt > PONG_TIMEOUT_MS) {
      // Half-open socket: drop it and reconnect through the normal path.
      this.#ws = null;
      this.#detach(ws, 4000, 'stale');
      this.#clearTimers();
      this.rttMs = null;
      if (this.#wanted) this.#scheduleRetry();
      return;
    }
    this.#ping();
  }

  #ping(): void {
    const ws = this.#ws;
    if (!ws || ws.readyState !== OPEN) return;
    const now = Date.now();
    this.#pingSentAt ??= now; // before sending, so even an answer delivered at once clears it
    try {
      ws.send(JSON.stringify({ type: 'ping', t: now } satisfies ClientMessage));
    } catch {
      /* close will follow */
    }
  }

  #detach(ws: WebSocket, code: number, reason: string): void {
    ws.onopen = null;
    ws.onmessage = null;
    ws.onclose = null;
    ws.onerror = null;
    try {
      ws.close(code, reason);
    } catch {
      /* already closed */
    }
  }

  #onOnline = (): void => {
    if (this.status === 'offline') this.status = 'reconnecting';
    this.retryNow();
  };

  #onOffline = (): void => {
    if (!this.#ws) this.status = 'offline';
  };

  #onVisibility = (): void => {
    if (document.visibilityState !== 'visible') return;
    // At once: while hidden (or asleep) the timers may not have run for minutes.
    if (this.#ws) this.#checkAlive();
    else this.retryNow();
  };

  #listen(on: boolean): void {
    if (on === this.#listening || typeof window === 'undefined') return;
    this.#listening = on;
    if (on) {
      window.addEventListener('online', this.#onOnline);
      window.addEventListener('offline', this.#onOffline);
      document.addEventListener('visibilitychange', this.#onVisibility);
    } else {
      window.removeEventListener('online', this.#onOnline);
      window.removeEventListener('offline', this.#onOffline);
      document.removeEventListener('visibilitychange', this.#onVisibility);
    }
  }

  #clearTimers(): void {
    clearTimeout(this.#retryTimer);
    clearInterval(this.#pingTimer);
    clearTimeout(this.#stableTimer);
    clearTimeout(this.#warmupTimer);
    this.#retryTimer = undefined;
    this.#pingTimer = undefined;
    this.#stableTimer = undefined;
    this.#warmupTimer = undefined;
  }
}
