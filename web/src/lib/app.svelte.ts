// Application controller: authentication, the WebSocket session, live turns,
// conversations and composer state. Components read its reactive fields and
// call its methods; nothing else talks to the network.

import { api, ApiError, setUnauthorizedHandler, type RequestOptions } from './api';
import { forgetLines } from './attachment-lines';
import { i18n, type Locale } from './i18n/index.svelte';
import { missingAttachment } from './attachments';
import { charCount, ComposerState, MAX_MESSAGE_CHARS, MAX_QUESTION_CHARS } from './composer.svelte';
import { Conversations, errorMessage } from './conversations.svelte';
import { inferCostBasis } from './costs';
import { formatInt } from './format';
import { clearLogoutPending, isLogoutPending, LOGOUT_PENDING_KEY, markLogoutPending } from './logout-pending';
import { modelOverridesPayload } from './models';
import { prefs } from './prefs.svelte';
import {
  AGENTS,
  type Agent,
  type ClientMessage,
  type FxRate,
  type ModelCatalog,
  type MonthSpend,
  type Pricing,
  type ProviderStatus,
  type RuntimeSettings,
  type ServerMessage,
  type TurnEvent,
  type TurnOptions,
} from './protocol';
import { router, type Route } from './router.svelte';
import { sceneHost } from './scene-host.svelte';
import { DEFAULT_SETTINGS, normalizeSettings } from './settings';
import { wsErrorText } from './text';
import { toasts } from './toasts.svelte';
import { createLiveTurn, isTerminal, mergeTurns, TurnRegistry, type TurnView } from './turns.svelte';
import { uuid } from './uuid';
import { viewer } from './viewer.svelte';
import { Connection } from './ws.svelte';

/**
 * `locked`: a logout the server has not confirmed yet (A4). Nothing of the session is in
 * memory, and the app does not open until the server confirms the logout or the owner
 * logs in again. The lock is local: on the server the session is still open.
 */
export type AuthPhase = 'checking' | 'login' | 'setup' | 'ready' | 'unreachable' | 'locked';

/** Whether the server's settings are loaded: `ready` once a load has worked. */
export type SettingsStatus = 'loading' | 'ready' | 'error';

/** Waits between automatic retries of a failed settings load (the last one repeats). */
const SETTINGS_RETRY_MS: readonly number[] = [1_000, 2_000, 5_000, 10_000, 30_000];

/** The texts of this controller (toasts, errors), in the language in force when they are made. */
const messages = () => i18n.m.app.messages;

/**
 * Requests the app makes by itself (refreshes after `hello`, after turn events, retries):
 * the server does not count them as owner activity, so an unused tab does not keep the
 * session alive (N7).
 */
const BACKGROUND: RequestOptions = { background: true };

/** A save based on settings that changed elsewhere (409): `app.settings` holds the current ones now. */
export class SettingsConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SettingsConflictError';
  }
}

class App {
  auth: AuthPhase = $state('checking');
  /** A POST /api/auth/logout is under way (the lock screen says so and waits). */
  logoutBusy = $state(false);
  /** Why the last attempt to end the session on the server failed, for the lock screen. */
  logoutError: string | null = $state(null);
  /** Unrecoverable problem (WebSocket origin rejected). */
  fatal: string | null = $state(null);
  providers: ProviderStatus[] = $state([]);
  settings: RuntimeSettings = $state(normalizeSettings(DEFAULT_SETTINGS));
  /**
   * Whether `settings` holds the server's settings. Until it does nothing is sent or
   * saved, since the built-in defaults would stand in for the owner's (audit A11).
   */
  settingsStatus: SettingsStatus = $state('loading');
  /** Why the last load of the settings failed. */
  settingsError: string | null = $state(null);
  /** A request for the settings is under way. */
  settingsLoading = $state(false);
  /** Models each agent can use (live from the vendor when possible), cached per session. */
  catalog: ModelCatalog | null = $state(null);
  /**
   * The catalog's default and fast models include the saved choice: after a save
   * that changed it, they are stale until the catalog is fetched again.
   */
  catalogStale = $state(false);
  catalogLoading = $state(false);
  catalogError: string | null = $state(null);
  /** Price table and exchange rate (loaded by the settings drawer). */
  pricing: Pricing | null = $state(null);
  pricingError: string | null = $state(null);
  /** USD -> EUR rate announced by the server (hello, /api/pricing, /api/spend). */
  fx: FxRate | null = $state(null);
  /** Month-to-date spend for the sidebar bars. */
  spend: MonthSpend | null = $state(null);
  readonly composer = new ComposerState();
  paletteOpen = $state(false);
  settingsOpen = $state(false);
  /** Mobile drawer state (desktop collapse lives in prefs). */
  sidebarOpen = $state(false);
  /** Bumped to ask the composer to take focus. */
  focusComposerTick = $state(0);

  readonly turns = new TurnRegistry();
  readonly convs = new Conversations();
  readonly conn = new Connection({
    onMessage: (msg) => this.#onMessage(msg),
    onUnauthorized: () => this.#sessionGone(),
    onForbidden: () => {
      this.fatal = i18n.m.app.root.originRejected;
    },
  });

  /** Turns of the open conversation: stored ones plus live ones of this session. */
  viewTurns: TurnView[] = $derived(
    mergeTurns(this.convs.storedTurns, this.turns.forConversation(this.convs.currentId)),
  );
  /** The running turn of the open conversation, if any. */
  runningTurn: TurnView | null = $derived(
    this.viewTurns.filter((t) => !isTerminal(t.status)).at(-1) ?? null,
  );
  /** Turn that drives the 3D scene: the visible running one, else any running one. */
  focusTurn: TurnView | null = $derived(this.runningTurn ?? this.turns.unfinished().at(-1) ?? null);

  /** Euros per dollar for every € shown in the UI. */
  eurPerUsd: number = $derived(this.fx?.eur_per_usd ?? this.settings.fx.eur_per_usd);

  #providersTimer: ReturnType<typeof setTimeout> | undefined;
  #catalogRequest: Promise<void> | null = null;
  #catalogTicket = 0;
  #spendRequest: Promise<void> | null = null;
  #settingsRequest: Promise<boolean> | null = null;
  #settingsRetry: ReturnType<typeof setTimeout> | undefined;
  #settingsAttempt = 0;
  /** Bumped when the session ends or a save answers: a request started before is stale. */
  #settingsTicket = 0;
  /** Bumped whenever the session ends here: answers to requests of before change nothing. */
  #epoch = 0;
  #logoutRequest: Promise<boolean> | null = null;
  #watchingTabs = false;
  /** Turns asked about after the server did not read a message (too_large, N19). */
  readonly #unread = new Set<string>();

  // ------------------------------------------------------------ auth

  async init(): Promise<void> {
    setUnauthorizedHandler(() => this.#sessionGone());
    if (!this.#watchingTabs && typeof window !== 'undefined') {
      this.#watchingTabs = true;
      window.addEventListener('storage', (e) => this.#onStorage(e));
    }
    await this.checkAuth();
  }

  /**
   * Opens the app if this browser has a session. A pending logout is finished first:
   * until the server confirms it, /api/auth/state still says the session works (A4).
   */
  async checkAuth(): Promise<void> {
    if (isLogoutPending()) await this.retryLogout();
    else await this.#askServer();
  }

  async #askServer(): Promise<void> {
    this.auth = 'checking';
    try {
      const state = await api.authState();
      if (state.authenticated && isLogoutPending()) {
        // Another tab started a logout meanwhile: never open the app on our own.
        this.#lock();
        return;
      }
      if (state.authenticated) await this.#enter();
      else this.auth = state.setup_required ? 'setup' : 'login';
    } catch {
      this.auth = 'unreachable';
    }
  }

  /** Throws ApiError for the login form to display. */
  async login(password: string, totp: string): Promise<void> {
    await api.login(password, totp);
    // The login ended the session the cookie presented: nothing is left to log out.
    clearLogoutPending();
    await this.#enter();
  }

  /**
   * Ends the session. Only the server can (the cookie is HttpOnly): until it confirms
   * (204, or 401: there was none), this browser stays locked, with nothing of the
   * session in memory and the socket closed, and a reload finishes the logout before
   * anything else (A4). The marker is set before asking, in case the page goes away.
   */
  async logout(): Promise<void> {
    markLogoutPending();
    this.#lock();
    if (await this.#endServerSession()) this.toLogin();
  }

  /** «Try again» on the lock screen, and every page load while a logout is pending. */
  async retryLogout(): Promise<void> {
    this.#lock();
    if (await this.#endServerSession()) await this.#askServer();
  }

  /** POST /api/auth/logout (one at a time): true once the server has no session for this browser. */
  #endServerSession(): Promise<boolean> {
    if (this.#logoutRequest) return this.#logoutRequest;
    const request = this.#sendLogout().finally(() => {
      if (this.#logoutRequest === request) this.#logoutRequest = null;
    });
    this.#logoutRequest = request;
    return request;
  }

  async #sendLogout(): Promise<boolean> {
    this.logoutBusy = true;
    this.logoutError = null;
    let ended: boolean;
    try {
      await api.logout();
      ended = true;
    } catch (err) {
      ended = err instanceof ApiError && err.status === 401;
      if (!ended) this.logoutError = errorMessage(err, messages().serverError);
    } finally {
      this.logoutBusy = false;
    }
    // Logged in again meanwhile (which ended that session too): this answer is old news.
    if (this.auth !== 'locked') return false;
    if (ended) clearLogoutPending();
    return ended;
  }

  toLogin(): void {
    if (this.auth === 'login' || this.auth === 'setup') return;
    this.#endSession();
    this.auth = 'login';
  }

  /** The lock screen: the session's data leaves this page and its traffic stops. */
  #lock(): void {
    this.#endSession();
    this.settings = normalizeSettings(DEFAULT_SETTINGS);
    this.composer.draft = '';
    this.composer.waitingUploads = false;
    this.composer.attachments.clear(); // their uploads stop; the server deletes what was never sent
    this.auth = 'locked';
  }

  /** Forgets what the session showed and stops its traffic: socket, timers and answers still to come. */
  #endSession(): void {
    this.#epoch++;
    this.conn.disconnect();
    clearTimeout(this.#providersTimer);
    this.turns.clear();
    this.convs.clear();
    this.providers = [];
    this.catalog = null;
    this.catalogStale = false;
    this.catalogError = null;
    this.#catalogTicket++;
    this.#catalogRequest = null;
    this.catalogLoading = false;
    this.pricing = null;
    this.pricingError = null;
    this.spend = null;
    this.#spendRequest = null;
    this.fx = null;
    this.#settingsTicket++;
    this.#settingsRequest = null;
    clearTimeout(this.#settingsRetry);
    this.#settingsAttempt = 0;
    this.settingsStatus = 'loading';
    this.settingsError = null;
    this.settingsLoading = false;
    this.paletteOpen = false;
    this.settingsOpen = false;
    this.sidebarOpen = false;
    viewer.close();
    forgetLines();
  }

  /** A 401, or the socket closed with 4401: the server has no session for this browser. */
  #sessionGone(): void {
    clearLogoutPending(); // nothing left to log out
    this.toLogin();
  }

  /** Another tab of this browser started or finished a logout (A4). */
  #onStorage(e: StorageEvent): void {
    if (e.key !== LOGOUT_PENDING_KEY) return;
    if (e.newValue !== null) {
      // It is logging out: this tab locks too, and helps to finish it.
      if (this.auth === 'ready') void this.retryLogout();
    } else if (this.auth === 'locked' && !this.logoutBusy) {
      // It is done (the server confirmed it, or the owner logged in again there).
      clearLogoutPending();
      void this.#askServer();
    }
  }

  async #enter(): Promise<void> {
    this.auth = 'ready';
    this.fatal = null;
    this.logoutError = null;
    this.conn.connect();
    // The open conversation follows the route (App.svelte effect on auth + route).
    void this.loadModels();
    await Promise.allSettled([
      this.loadSettings(),
      this.refreshProviders(),
      this.refreshSpend(),
      this.convs.refresh(),
    ]);
  }

  // ------------------------------------------------------------ settings

  /**
   * Fetches the saved settings (one request at a time). Until they have loaded, a
   * failure is shown and retried with backoff, and at once on every `hello`.
   * Resolves true when `settings` holds what the server has now.
   */
  loadSettings(options: RequestOptions = {}): Promise<boolean> {
    if (this.#settingsRequest) return this.#settingsRequest;
    const request = this.#fetchSettings(options).finally(() => {
      if (this.#settingsRequest !== request) return;
      this.#settingsRequest = null;
      this.settingsLoading = false;
    });
    this.#settingsRequest = request;
    this.settingsLoading = true;
    return request;
  }

  async #fetchSettings(options: RequestOptions): Promise<boolean> {
    clearTimeout(this.#settingsRetry);
    const ticket = this.#settingsTicket;
    try {
      const settings = normalizeSettings(await api.settings(options));
      // Stale: the session ended (not ready), or a save answered with newer settings.
      if (ticket !== this.#settingsTicket) return this.settingsStatus === 'ready';
      this.#takeSettings(settings);
      return true;
    } catch (err) {
      if (ticket !== this.#settingsTicket) return this.settingsStatus === 'ready';
      this.settingsError = errorMessage(err, messages().serverError);
      if (this.settingsStatus !== 'ready') {
        this.settingsStatus = 'error';
        this.#retrySettingsLater();
      }
      return false;
    }
  }

  #retrySettingsLater(): void {
    clearTimeout(this.#settingsRetry);
    if (this.auth !== 'ready') return;
    const delay = SETTINGS_RETRY_MS[Math.min(this.#settingsAttempt, SETTINGS_RETRY_MS.length - 1)];
    this.#settingsAttempt++;
    this.#settingsRetry = setTimeout(() => void this.loadSettings(BACKGROUND), delay);
  }

  /** The server's current settings become the ones this tab works with. */
  #takeSettings(next: RuntimeSettings): void {
    const first = this.settingsStatus !== 'ready';
    const before = this.settings;
    this.settings = next;
    this.settingsStatus = 'ready';
    this.settingsError = null;
    this.#settingsAttempt = 0;
    clearTimeout(this.#settingsRetry);
    // Saved defaults never overwrite an option the owner changed in this tab (A11, N11).
    this.composer.applyDefaults(next);
    if (!first && savedModelsChanged(before, next)) {
      this.catalogStale = true;
      void this.reloadModels();
    }
  }

  /**
   * Saves settings edited from revision `s.revision` and returns the stored ones.
   * If they changed elsewhere since (409), the current ones become this tab's and
   * SettingsConflictError says so: nothing is overwritten.
   */
  async saveSettings(s: RuntimeSettings): Promise<RuntimeSettings> {
    if (this.settingsStatus !== 'ready') throw new Error(messages().settingsNotLoaded);
    const ticket = this.#settingsTicket;
    let saved: RuntimeSettings;
    try {
      saved = normalizeSettings(await api.saveSettings(s));
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 409) throw err;
      if (ticket === this.#settingsTicket) await this.#takeCurrent(err.body);
      throw new SettingsConflictError(conflictDetail(err.body));
    }
    if (ticket !== this.#settingsTicket) return saved; // the session ended meanwhile
    this.#settingsTicket++; // a load started before this save may answer with older settings
    this.#takeSettings(saved);
    this.#afterSettingsChange();
    return saved;
  }

  /** The settings a 409 answer carries (fetched again if it has none) become this tab's. */
  async #takeCurrent(body: unknown): Promise<void> {
    const current = conflictSettings(body);
    if (current) {
      this.#settingsTicket++;
      this.#takeSettings(current);
    } else {
      await this.loadSettings();
    }
    this.#afterSettingsChange();
  }

  /** Prices, the exchange rate mode and budgets change what these report. */
  #afterSettingsChange(): void {
    void this.loadPricing();
    void this.refreshSpend();
  }

  /**
   * Loads the model catalog once per session, and again while it is stale (a
   * fetch after saving other models failed); `refresh` asks the server to query
   * the vendors again.
   */
  loadModels(refresh = false): Promise<void> {
    if (this.#catalogRequest && !refresh) return this.#catalogRequest;
    if (this.catalog && !this.catalogStale && !refresh) return Promise.resolve();
    return this.#fetchModels(refresh);
  }

  /** Fetches the catalog again (the server's cached lists, without asking the vendors). */
  reloadModels(): Promise<void> {
    return this.#fetchModels(false);
  }

  #fetchModels(refresh: boolean): Promise<void> {
    this.catalogLoading = true;
    const ticket = ++this.#catalogTicket;
    const request = api.models(refresh).then(
      (catalog) => {
        if (ticket !== this.#catalogTicket) return;
        this.catalog = catalog;
        this.catalogStale = false;
        this.catalogError = null;
      },
      (err: unknown) => {
        if (ticket !== this.#catalogTicket) return;
        this.catalogError = errorMessage(err, messages().modelsFailed);
      },
    );
    this.#catalogRequest = request.finally(() => {
      if (ticket !== this.#catalogTicket) return;
      this.#catalogRequest = null;
      this.catalogLoading = false;
    });
    return this.#catalogRequest;
  }

  async loadPricing(): Promise<void> {
    const epoch = this.#epoch;
    try {
      const pricing = await api.pricing();
      if (epoch !== this.#epoch) return; // the session ended meanwhile
      this.pricing = pricing;
      this.fx = pricing.fx;
      this.pricingError = null;
    } catch (err) {
      if (epoch !== this.#epoch) return;
      this.pricingError = errorMessage(err, messages().pricesFailed);
    }
  }

  /** Concurrent calls (login and the first hello) share one request. */
  refreshSpend(options: RequestOptions = {}): Promise<void> {
    if (this.#spendRequest) return this.#spendRequest;
    const epoch = this.#epoch;
    const request = api
      .spend(options)
      .then(
        (spend) => {
          if (epoch !== this.#epoch || this.auth !== 'ready') return;
          this.spend = spend;
          this.fx = spend.fx;
        },
        () => {
          // the bars keep the last known values
        },
      )
      .finally(() => {
        if (this.#spendRequest === request) this.#spendRequest = null;
      });
    this.#spendRequest = request;
    return request;
  }

  /** Model an agent will use in the next turn of this tab. */
  modelFor(agent: Agent): string | null {
    return prefs.models[agent] ?? this.defaultModel(agent);
  }

  /** Model an agent uses when this tab picks none: the saved one, else the provider's. */
  defaultModel(agent: Agent): string | null {
    const saved = this.settings.models[agent];
    if (saved) return saved;
    const provider = this.providers.find((p) => p.agent === agent)?.model || null;
    const catalog = this.catalogStale ? null : this.catalog?.[agent].default_model;
    return catalog || provider;
  }

  /**
   * The owner picks the interface's language. The server writes its texts in the language
   * of each request and of the socket: the socket reconnects in the new one (running turns
   * resume on it), and the texts it already sent (the agents' status, the models) come
   * again.
   */
  setLocale(locale: Locale): void {
    if (locale === i18n.locale) return;
    i18n.set(locale);
    if (this.auth !== 'ready') return;
    this.conn.disconnect();
    this.conn.connect();
    void this.refreshProviders(BACKGROUND);
    if (this.catalog) void this.reloadModels();
  }

  async refreshProviders(options: RequestOptions = {}): Promise<void> {
    const epoch = this.#epoch;
    try {
      const providers = await api.providers(options);
      if (epoch === this.#epoch) this.providers = providers;
    } catch {
      // hello messages also carry provider status
    }
  }

  /** Usage windows and month spend move after a turn; the server needs a moment to account it. */
  #refreshUsageSoon(): void {
    clearTimeout(this.#providersTimer);
    this.#providersTimer = setTimeout(() => {
      void this.refreshProviders(BACKGROUND);
      void this.refreshSpend(BACKGROUND);
    }, 1500);
  }

  // ------------------------------------------------------------ navigation

  async syncRoute(route: Route): Promise<void> {
    if (this.auth !== 'ready') return;
    this.sidebarOpen = false;
    if (route.name !== 'chat') return;
    if (route.id == null) {
      if (this.convs.currentId != null) this.newConversation(false);
      return;
    }
    if (route.id === this.convs.currentId) {
      // Already shown, being loaded, or just created by a live turn (nothing stored to fetch yet).
      if (this.convs.detail || this.convs.loading || this.turns.forConversation(route.id).length) return;
    }
    const exists = await this.convs.open(route.id);
    if (!exists) {
      toasts.push(messages().conversationGone, 'error');
      router.replace({ name: 'chat', id: null });
      this.newConversation(false);
      return;
    }
    this.#fillQuestions();
  }

  newConversation(navigate = true): void {
    // Drop drafts that never started (failed before the server saved them).
    for (const t of this.turns.forConversation(null)) {
      if (isTerminal(t.status) && t.requestId) this.turns.remove(t.requestId);
    }
    this.convs.startNew();
    if (navigate) router.go({ name: 'chat', id: null });
    this.focusComposer();
  }

  openConversation(id: number): void {
    router.go({ name: 'chat', id });
  }

  focusComposer(): void {
    this.focusComposerTick++;
  }

  async deleteConversation(id: number): Promise<void> {
    try {
      await this.convs.remove(id);
      toasts.push(messages().conversationDeleted, 'success');
      if (this.convs.currentId === id) this.newConversation();
    } catch (err) {
      toasts.push(errorMessage(err, messages().deleteFailed), 'error');
    }
  }

  async renameConversation(id: number, title: string): Promise<boolean> {
    const clean = title.trim();
    if (!clean) return false;
    try {
      await this.convs.rename(id, clean);
      return true;
    } catch (err) {
      toasts.push(errorMessage(err, messages().renameFailed), 'error');
      return false;
    }
  }

  // ------------------------------------------------------------ turns

  canSend(): boolean {
    return this.conn.status === 'open' && !this.runningTurn && this.settingsStatus === 'ready';
  }

  /**
   * The composer's send: its draft, with its attachments. While some are still uploading
   * it waits for them (the composer says so) and sends once they are all up; a second
   * call stops waiting.
   */
  sendDraft(): void {
    const c = this.composer;
    if (c.waitingUploads) {
      c.waitingUploads = false;
      return;
    }
    if (!c.attachments.busy) {
      if (this.send(c.draft)) c.draft = '';
      return;
    }
    c.waitingUploads = true;
    void c.attachments.settled().then(() => {
      if (!c.waitingUploads) return; // stopped, or the session ended
      c.waitingUploads = false;
      if (this.send(c.draft)) c.draft = '';
    });
  }

  /**
   * Sends `text` as a question in the open conversation, with the composer's attachments.
   * False when it was not sent (the composer then keeps it): never a question or a message
   * the server would refuse (N19).
   */
  send(text: string): boolean {
    const question = text.trim();
    if (!question || this.runningTurn) return false;
    if (charCount(question) > MAX_QUESTION_CHARS) {
      toasts.push(messages().questionTooLong(formatInt(MAX_QUESTION_CHARS)), 'error');
      return false;
    }
    const tray = this.composer.attachments;
    if (tray.failed || tray.busy) {
      toasts.push(tray.failed ? messages().attachmentsFailed : messages().attachmentsUploading, 'error');
      return false;
    }
    if (this.conn.status !== 'open') {
      toasts.push(messages().offline, 'error');
      return false;
    }
    if (this.settingsStatus !== 'ready') {
      // Never with the built-in defaults instead of the owner's (A11); the composer waits too.
      toasts.push(
        this.settingsStatus === 'error'
          ? messages().settingsFailed
          : messages().settingsLoading,
        'error',
      );
      return false;
    }
    const c = this.composer;
    const requestId = uuid();
    const options: TurnOptions = {
      debate: { rounds: c.rounds, consensus_threshold: c.threshold, synthesizer: c.synthesizer },
      // Only a refine turn uses them: the server would validate them for any turn.
      ...(c.mode === 'refine' ? { refine: c.refineOptions() } : {}),
      use_cache: c.useCache,
    };
    const conversationId = this.convs.currentId;
    const models = modelOverridesPayload(prefs.models, c.mode === 'solo' ? [c.target] : AGENTS);
    const attachments = tray.ready();
    const msg: ClientMessage = {
      type: 'turn.start',
      request_id: requestId,
      text: question,
      mode: c.mode,
      target: c.target,
      conversation_id: conversationId,
      options,
      ...(models ? { models } : {}),
      ...(attachments.length ? { attachments: attachments.map((a) => a.id) } : {}),
    };
    if (charCount(JSON.stringify(msg)) > MAX_MESSAGE_CHARS) {
      // The server would not read it, nor could it say which turn went unread.
      toasts.push(messages().messageTooLarge, 'error');
      return false;
    }
    this.turns.add(
      createLiveTurn({
        requestId,
        question,
        mode: c.mode,
        target: c.mode === 'solo' ? c.target : null,
        options,
        conversationId,
        attachments,
      }),
    );
    if (!this.conn.send(msg)) {
      this.turns.remove(requestId);
      toasts.push(messages().sendFailed, 'error');
      return false;
    }
    tray.take(); // they go with the question now
    return true;
  }

  cancel(): void {
    const turn = this.runningTurn;
    if (!turn?.requestId) return;
    if (!this.conn.send({ type: 'turn.cancel', request_id: turn.requestId })) {
      toasts.push(messages().stopFailed, 'error');
    }
  }

  /**
   * «Stop after this round»: the running refine turn ends after the round in course
   * (turn.stop), with its last version; it says so until turn.stopping names that round.
   * Only a refine turn has rounds to finish (any turn stops at once with `cancel`).
   */
  stopAfterRound(): void {
    const turn = this.runningTurn;
    if (!turn?.requestId || turn.mode !== 'refine' || turn.stopRequested || turn.stoppingRound != null) return;
    if (!this.conn.send({ type: 'turn.stop', request_id: turn.requestId })) {
      toasts.push(messages().stopUnsent, 'error');
      return;
    }
    turn.stopRequested = true;
  }

  // ------------------------------------------------------------ server messages

  #onMessage(msg: ServerMessage): void {
    switch (msg.type) {
      case 'hello':
        this.providers = msg.providers;
        if (msg.fx) this.fx = msg.fx;
        this.#resubscribe(msg.active_turns);
        void this.refreshSpend(BACKGROUND);
        if (this.settingsStatus !== 'ready') {
          // The server answers again: retry a failed load now, and soon after if it fails.
          this.#settingsAttempt = 0;
          void this.loadSettings(BACKGROUND);
        }
        return;
      case 'turn.unknown':
        void this.#onUnknown(msg.request_id);
        return;
      case 'error': {
        const text = wsErrorText(msg.code, msg.message);
        const turn = msg.request_id ? this.turns.get(msg.request_id) : undefined;
        if (turn && turn.status === 'pending') {
          // A rejected turn.start (busy, invalid model...): never saved, so offer the text back.
          turn.status = 'failed';
          turn.error = { kind: msg.code ?? 'server', message: text };
          if (turn.turnId == null) this.composer.restore(turn.question, turn.attachments);
        } else if (turn?.stopRequested) {
          // A refused turn.stop: the turn goes on as it was.
          turn.stopRequested = false;
          toasts.push(text, 'error');
        } else if (msg.code === 'too_large' && !msg.request_id && this.#askAboutUnread()) {
          // The server's answers say which turn it was (#onUnknown), and that turn says why.
        } else {
          toasts.push(text, 'error');
        }
        return;
      }
      case 'pong':
        return;
      default:
        this.#onTurnEvent(msg);
    }
  }

  #onTurnEvent(ev: TurnEvent): void {
    const before = this.turns.get(ev.request_id);
    if (!before) return;
    this.#unread.delete(ev.request_id); // the server has it: it read the message
    const wasDraft = before.conversationId == null;
    const turn = this.turns.apply(ev);
    if (!turn) return;

    switch (ev.type) {
      case 'turn.started':
        this.#fillFromStored(turn);
        if (wasDraft && this.convs.currentId == null && router.route.name === 'chat' && router.route.id == null) {
          this.convs.adopt(ev.conversation_id);
          router.replace({ name: 'chat', id: ev.conversation_id });
        }
        if (ev.new_conversation) void this.convs.refresh(BACKGROUND);
        break;
      case 'stream.delta': {
        const stream = turn.streams.find((s) => s.id === ev.stream_id);
        if (stream) sceneHost.pulse(stream.agent, ev.text.length);
        break;
      }
      case 'stream.completed': {
        // The server says how the cost was obtained; older servers only imply it
        // through the provider mode.
        const stream = turn.streams.find((s) => s.id === ev.stream_id);
        if (stream && !stream.costBasis) {
          const mode = this.providers.find((p) => p.agent === stream.agent)?.mode;
          stream.costBasis = ev.cost_basis ?? inferCostBasis(mode, ev.usage);
        }
        break;
      }
      case 'pdf.check': {
        // Claude's calls check the PDF: a real cost in API mode, a value with the subscription.
        const check = turn.pdfChecks.find((c) => c.attachmentId === ev.attachment_id);
        if (check?.usage && !check.costBasis) {
          const mode = this.providers.find((p) => p.agent === 'claude')?.mode;
          check.costBasis = inferCostBasis(mode, check.usage);
        }
        break;
      }
      case 'turn.completed':
        this.#turnEnded();
        // A refine turn whose reviewers found nothing left to improve is a consensus too.
        if (ev.consensus?.reached || ev.stop_reason === 'converged' || ev.stop_reason === 'unchanged') {
          sceneHost.flash('consensus');
        }
        break;
      case 'turn.failed':
        this.#turnEnded();
        sceneHost.flash('error');
        // Rejected before it started (too long, the conversation is gone...): the server
        // stored nothing, so the question goes back to the composer (N19), with its attachments;
        // one the server no longer has says so on its card.
        if (turn.turnId == null) {
          this.composer.restore(turn.question, turn.attachments);
          const gone = missingAttachment(ev.error);
          if (gone !== null) this.composer.attachments.markGone(gone);
        }
        if (turn.conversationId !== this.convs.currentId) {
          toasts.push(messages().turnFailed(ev.error.message), 'error');
        }
        break;
      case 'turn.cancelled':
        this.#turnEnded();
        break;
      default:
        break;
    }
  }

  /**
   * A turn ended, however it did: a failed or cancelled one may have billed calls too
   * (A15). Its conversation moved in the list, and the usage windows and the month spend
   * change a moment later.
   */
  #turnEnded(): void {
    void this.convs.refresh(BACKGROUND);
    this.#refreshUsageSoon();
  }

  #resubscribe(active: { request_id: string; conversation_id: number | null; last_seq: number }[]): void {
    for (const t of this.turns.unfinished()) {
      if (!t.requestId) continue;
      this.conn.send({ type: 'turn.subscribe', request_id: t.requestId, after_seq: t.lastSeq });
      // A turn.stop the connection may have lost: the server announces it once whatever it gets.
      if (t.stopRequested) this.conn.send({ type: 'turn.stop', request_id: t.requestId });
    }
    // Turns running on the server that this tab does not know (e.g. after a reload): replay them.
    for (const a of active) {
      if (this.turns.get(a.request_id)) continue;
      const turn = this.turns.add(
        createLiveTurn({ requestId: a.request_id, question: '', mode: 'debate', conversationId: a.conversation_id }),
      );
      turn.status = 'running';
      this.conn.send({ type: 'turn.subscribe', request_id: a.request_id, after_seq: 0 });
    }
  }

  /**
   * The server did not read a message because it was too large (N19). The error cannot say
   * which one, and only a turn.start can be that long: the app asks about each turn it sent
   * that has had no answer yet. The server answers turn.unknown for the one it never read,
   * and nothing for the others, which this connection already follows. False if none.
   */
  #askAboutUnread(): boolean {
    const unanswered = this.turns.unfinished().filter((t) => t.status === 'pending' && t.requestId);
    for (const t of unanswered) {
      const requestId = t.requestId!;
      this.#unread.add(requestId);
      this.conn.send({ type: 'turn.subscribe', request_id: requestId, after_seq: t.lastSeq });
    }
    return unanswered.length > 0;
  }

  async #onUnknown(requestId: string): Promise<void> {
    const turn = this.turns.get(requestId);
    const unread = this.#unread.delete(requestId);
    if (!turn) return;
    if (turn.turnId == null) {
      // The server never saved it: offer the text back.
      turn.status = 'failed';
      turn.error = unread
        ? { kind: 'too_large', message: messages().unreadMessage }
        : { kind: 'lost', message: messages().connectionLost };
      this.composer.restore(turn.question, turn.attachments);
      return;
    }
    // The server forgot it (finished long ago or restarted): trust the stored version.
    const conversationId = turn.conversationId;
    this.turns.remove(requestId);
    if (conversationId != null && conversationId === this.convs.currentId) {
      await this.convs.reload(BACKGROUND);
      this.#fillQuestions();
    }
  }

  /**
   * Live turns replayed after a reload (or started in another tab) only know what
   * the events say: the question text, options and target come from the stored
   * question once the conversation is loaded.
   */
  #fillQuestions(): void {
    for (const t of this.turns.forConversation(this.convs.currentId)) this.#fillFromStored(t);
  }

  #fillFromStored(turn: TurnView): void {
    if (turn.turnId == null) return;
    const stored = this.convs.storedTurns.find((t) => t.turnId === turn.turnId);
    if (!stored) return;
    if (!turn.question) turn.question = stored.question;
    if (!turn.attachments.length) turn.attachments = stored.attachments;
    turn.options ??= stored.options;
    turn.target ??= stored.target;
  }
}

/** Whether new settings changed the saved default or fast model of any agent. */
function savedModelsChanged(before: RuntimeSettings, after: RuntimeSettings): boolean {
  return AGENTS.some(
    (a) => before.models[a] !== after.models[a] || before.fast_models[a] !== after.fast_models[a],
  );
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** The current settings in the body of a 409 answer to PUT /api/settings, if any. */
function conflictSettings(body: unknown): RuntimeSettings | null {
  if (!isObject(body) || !isObject(body.settings)) return null;
  return normalizeSettings(body.settings as Partial<RuntimeSettings>);
}

function conflictDetail(body: unknown): string {
  return isObject(body) && typeof body.detail === 'string' && body.detail ? body.detail : messages().settingsConflict;
}

export const app = new App();

export { ApiError };
