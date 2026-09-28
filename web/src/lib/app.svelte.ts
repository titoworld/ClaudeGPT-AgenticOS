// Application controller: authentication, the WebSocket session, live turns,
// conversations and composer state. Components read its reactive fields and
// call its methods; nothing else talks to the network.

import { api, ApiError, setUnauthorizedHandler } from './api';
import { ComposerState } from './composer.svelte';
import { Conversations, errorMessage } from './conversations.svelte';
import { inferCostBasis } from './costs';
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
import { Connection } from './ws.svelte';

export type AuthPhase = 'checking' | 'login' | 'setup' | 'ready' | 'unreachable';

/** Whether the server's settings are loaded: `ready` once a load has worked. */
export type SettingsStatus = 'loading' | 'ready' | 'error';

/** Waits between automatic retries of a failed settings load (the last one repeats). */
const SETTINGS_RETRY_MS: readonly number[] = [1_000, 2_000, 5_000, 10_000, 30_000];

const SETTINGS_NOT_LOADED = "La configuració encara no s'ha carregat.";

const SETTINGS_CONFLICT =
  'La configuració ha canviat en una altra pestanya o dispositiu. Revisa-la i torna-la a desar.';

/** A save based on settings that changed elsewhere (409): `app.settings` holds the current ones now. */
export class SettingsConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SettingsConflictError';
  }
}

class App {
  auth: AuthPhase = $state('checking');
  /** Unrecoverable problem (WebSocket origin rejected). */
  fatal: string | null = $state(null);
  providers: ProviderStatus[] = $state([]);
  settings: RuntimeSettings = $state(normalizeSettings(DEFAULT_SETTINGS));
  /**
   * Whether `settings` holds the server's settings. Until it does nothing is sent or
   * saved, since the built-in defaults would stand in for the owner's (audit A11).
   */
  settingsStatus: SettingsStatus = $state('loading');
  /** Why the last load of the settings failed (Catalan). */
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
    onUnauthorized: () => this.toLogin(),
    onForbidden: () => {
      this.fatal =
        "El servidor ha rebutjat la connexió en temps real perquè l'origen d'aquesta pàgina no és a la llista permesa (codi 4403).";
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

  // ------------------------------------------------------------ auth

  async init(): Promise<void> {
    setUnauthorizedHandler(() => this.toLogin());
    await this.checkAuth();
  }

  async checkAuth(): Promise<void> {
    this.auth = 'checking';
    try {
      const state = await api.authState();
      if (state.authenticated) await this.#enter();
      else this.auth = state.setup_required ? 'setup' : 'login';
    } catch {
      this.auth = 'unreachable';
    }
  }

  /** Throws ApiError for the login form to display. */
  async login(password: string, totp: string): Promise<void> {
    await api.login(password, totp);
    await this.#enter();
  }

  async logout(): Promise<void> {
    try {
      await api.logout();
    } catch {
      // the session is dropped locally anyway
    }
    this.toLogin();
  }

  toLogin(): void {
    if (this.auth === 'login' || this.auth === 'setup') return;
    this.conn.disconnect();
    this.turns.clear();
    this.convs.clear();
    this.providers = [];
    this.catalog = null;
    this.catalogStale = false;
    this.#catalogTicket++;
    this.#catalogRequest = null;
    this.catalogLoading = false;
    this.pricing = null;
    this.spend = null;
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
    this.auth = 'login';
  }

  async #enter(): Promise<void> {
    this.auth = 'ready';
    this.fatal = null;
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
  loadSettings(): Promise<boolean> {
    if (this.#settingsRequest) return this.#settingsRequest;
    const request = this.#fetchSettings().finally(() => {
      if (this.#settingsRequest !== request) return;
      this.#settingsRequest = null;
      this.settingsLoading = false;
    });
    this.#settingsRequest = request;
    this.settingsLoading = true;
    return request;
  }

  async #fetchSettings(): Promise<boolean> {
    clearTimeout(this.#settingsRetry);
    const ticket = this.#settingsTicket;
    try {
      const settings = normalizeSettings(await api.settings());
      // Stale: the session ended (not ready), or a save answered with newer settings.
      if (ticket !== this.#settingsTicket) return this.settingsStatus === 'ready';
      this.#takeSettings(settings);
      return true;
    } catch (err) {
      if (ticket !== this.#settingsTicket) return this.settingsStatus === 'ready';
      this.settingsError = errorMessage(err, 'El servidor ha respost amb un error.');
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
    this.#settingsRetry = setTimeout(() => void this.loadSettings(), delay);
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
    if (this.settingsStatus !== 'ready') throw new Error(SETTINGS_NOT_LOADED);
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
        this.catalogError = errorMessage(err, "No s'ha pogut obtenir la llista de models.");
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
    try {
      const pricing = await api.pricing();
      this.pricing = pricing;
      this.fx = pricing.fx;
      this.pricingError = null;
    } catch (err) {
      this.pricingError = errorMessage(err, "No s'han pogut carregar els preus.");
    }
  }

  /** Concurrent calls (login and the first hello) share one request. */
  refreshSpend(): Promise<void> {
    this.#spendRequest ??= api
      .spend()
      .then(
        (spend) => {
          if (this.auth !== 'ready') return;
          this.spend = spend;
          this.fx = spend.fx;
        },
        () => {
          // the bars keep the last known values
        },
      )
      .finally(() => (this.#spendRequest = null));
    return this.#spendRequest;
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

  async refreshProviders(): Promise<void> {
    try {
      this.providers = await api.providers();
    } catch {
      // hello messages also carry provider status
    }
  }

  /** Usage windows and month spend move after a turn; the server needs a moment to account it. */
  #refreshUsageSoon(): void {
    clearTimeout(this.#providersTimer);
    this.#providersTimer = setTimeout(() => {
      void this.refreshProviders();
      void this.refreshSpend();
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
      toasts.push('Aquesta conversa ja no existeix.', 'error');
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
      toasts.push('Conversa eliminada.', 'success');
      if (this.convs.currentId === id) this.newConversation();
    } catch (err) {
      toasts.push(errorMessage(err, "No s'ha pogut eliminar la conversa."), 'error');
    }
  }

  async renameConversation(id: number, title: string): Promise<boolean> {
    const clean = title.trim();
    if (!clean) return false;
    try {
      await this.convs.rename(id, clean);
      return true;
    } catch (err) {
      toasts.push(errorMessage(err, "No s'ha pogut canviar el nom."), 'error');
      return false;
    }
  }

  // ------------------------------------------------------------ turns

  canSend(): boolean {
    return this.conn.status === 'open' && !this.runningTurn && this.settingsStatus === 'ready';
  }

  send(text: string): boolean {
    const question = text.trim();
    if (!question || this.runningTurn) return false;
    if (this.conn.status !== 'open') {
      toasts.push('Sense connexió amb el servidor. Espera que es reconnecti.', 'error');
      return false;
    }
    if (this.settingsStatus !== 'ready') {
      // Never with the built-in defaults instead of the owner's (A11); the composer waits too.
      toasts.push(
        this.settingsStatus === 'error'
          ? "No s'ha pogut carregar la configuració: fins que no es carregui no es pot enviar cap pregunta."
          : 'Encara es carrega la configuració. Torna-ho a provar en un moment.',
        'error',
      );
      return false;
    }
    const c = this.composer;
    const requestId = uuid();
    const options: TurnOptions = {
      debate: { rounds: c.rounds, consensus_threshold: c.threshold, synthesizer: c.synthesizer },
      use_cache: c.useCache,
    };
    const conversationId = this.convs.currentId;
    const models = modelOverridesPayload(prefs.models, c.mode === 'solo' ? [c.target] : AGENTS);
    this.turns.add(
      createLiveTurn({
        requestId,
        question,
        mode: c.mode,
        target: c.mode === 'solo' ? c.target : null,
        options,
        conversationId,
      }),
    );
    const msg: ClientMessage = {
      type: 'turn.start',
      request_id: requestId,
      text: question,
      mode: c.mode,
      target: c.target,
      conversation_id: conversationId,
      options,
      ...(models ? { models } : {}),
    };
    if (!this.conn.send(msg)) {
      this.turns.remove(requestId);
      toasts.push("No s'ha pogut enviar la pregunta.", 'error');
      return false;
    }
    return true;
  }

  cancel(): void {
    const turn = this.runningTurn;
    if (!turn?.requestId) return;
    if (!this.conn.send({ type: 'turn.cancel', request_id: turn.requestId })) {
      toasts.push("Sense connexió: no s'ha pogut aturar el torn.", 'error');
    }
  }

  // ------------------------------------------------------------ server messages

  #onMessage(msg: ServerMessage): void {
    switch (msg.type) {
      case 'hello':
        this.providers = msg.providers;
        if (msg.fx) this.fx = msg.fx;
        this.#resubscribe(msg.active_turns);
        void this.refreshSpend();
        if (this.settingsStatus !== 'ready') {
          // The server answers again: retry a failed load now, and soon after if it fails.
          this.#settingsAttempt = 0;
          void this.loadSettings();
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
          if (turn.turnId == null && !this.composer.draft.trim()) this.composer.draft = turn.question;
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
        if (ev.new_conversation) void this.convs.refresh();
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
      case 'turn.completed':
        void this.convs.refresh();
        this.#refreshUsageSoon();
        if (ev.consensus?.reached) sceneHost.flash('consensus');
        break;
      case 'turn.failed':
        sceneHost.flash('error');
        if (turn.conversationId !== this.convs.currentId) {
          toasts.push(`Un torn ha fallat: ${ev.error.message}`, 'error');
        }
        break;
      default:
        break;
    }
  }

  #resubscribe(active: { request_id: string; conversation_id: number | null; last_seq: number }[]): void {
    for (const t of this.turns.unfinished()) {
      if (t.requestId) this.conn.send({ type: 'turn.subscribe', request_id: t.requestId, after_seq: t.lastSeq });
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

  async #onUnknown(requestId: string): Promise<void> {
    const turn = this.turns.get(requestId);
    if (!turn) return;
    if (turn.turnId == null) {
      // The server never saved it: offer the text back.
      turn.status = 'failed';
      turn.error = { kind: 'lost', message: "La connexió es va tallar abans d'enviar la pregunta. Torna-ho a provar." };
      if (!this.composer.draft.trim()) this.composer.draft = turn.question;
      return;
    }
    // The server forgot it (finished long ago or restarted): trust the stored version.
    const conversationId = turn.conversationId;
    this.turns.remove(requestId);
    if (conversationId != null && conversationId === this.convs.currentId) {
      await this.convs.reload();
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
  return isObject(body) && typeof body.detail === 'string' && body.detail ? body.detail : SETTINGS_CONFLICT;
}

export const app = new App();

export { ApiError };
