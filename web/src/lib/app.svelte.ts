// Application controller: authentication, the WebSocket session, live turns,
// conversations and composer state. Components read its reactive fields and
// call its methods; nothing else talks to the network.

import { api, ApiError, setUnauthorizedHandler } from './api';
import { Conversations, errorMessage } from './conversations.svelte';
import type {
  Agent,
  ClientMessage,
  ProviderStatus,
  RuntimeSettings,
  ServerMessage,
  TurnEvent,
  TurnMode,
  TurnOptions,
} from './protocol';
import { router, type Route } from './router.svelte';
import { sceneHost } from './scene-host.svelte';
import { DEFAULT_SETTINGS } from './settings';
import { toasts } from './toasts.svelte';
import { createLiveTurn, isTerminal, mergeTurns, TurnRegistry, type TurnView } from './turns.svelte';
import { uuid } from './uuid';
import { Connection } from './ws.svelte';

export type AuthPhase = 'checking' | 'login' | 'setup' | 'ready' | 'unreachable';

export interface ComposerState {
  mode: TurnMode;
  target: Agent;
  rounds: number;
  threshold: number;
  synthesizer: Agent;
  useCache: boolean;
  draft: string;
}

function cloneSettings(s: RuntimeSettings): RuntimeSettings {
  return { ...s, debate: { ...s.debate } };
}

class App {
  auth: AuthPhase = $state('checking');
  /** Unrecoverable problem (WebSocket origin rejected). */
  fatal: string | null = $state(null);
  providers: ProviderStatus[] = $state([]);
  settings: RuntimeSettings = $state(cloneSettings(DEFAULT_SETTINGS));
  composer: ComposerState = $state({
    mode: DEFAULT_SETTINGS.default_mode,
    target: DEFAULT_SETTINGS.default_target,
    rounds: DEFAULT_SETTINGS.debate.rounds,
    threshold: DEFAULT_SETTINGS.debate.consensus_threshold,
    synthesizer: DEFAULT_SETTINGS.debate.synthesizer,
    useCache: DEFAULT_SETTINGS.use_cache,
    draft: '',
  });
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

  #providersTimer: ReturnType<typeof setTimeout> | undefined;

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
    this.paletteOpen = false;
    this.settingsOpen = false;
    this.auth = 'login';
  }

  async #enter(): Promise<void> {
    this.auth = 'ready';
    this.fatal = null;
    this.conn.connect();
    // The open conversation follows the route (App.svelte effect on auth + route).
    await Promise.allSettled([this.#loadSettings(), this.refreshProviders(), this.convs.refresh()]);
  }

  async #loadSettings(): Promise<void> {
    try {
      const s = await api.settings();
      this.settings = cloneSettings(s);
      this.applyDefaults(s);
    } catch {
      // keep defaults; the settings drawer reports errors when saving
    }
  }

  applyDefaults(s: RuntimeSettings): void {
    this.composer.mode = s.default_mode;
    this.composer.target = s.default_target;
    this.composer.rounds = s.debate.rounds;
    this.composer.threshold = s.debate.consensus_threshold;
    this.composer.synthesizer = s.debate.synthesizer;
    this.composer.useCache = s.use_cache;
  }

  async saveSettings(s: RuntimeSettings): Promise<void> {
    const saved = await api.saveSettings(s);
    this.settings = cloneSettings(saved);
    this.applyDefaults(saved);
  }

  async refreshProviders(): Promise<void> {
    try {
      this.providers = await api.providers();
    } catch {
      // hello messages also carry provider status
    }
  }

  #refreshProvidersSoon(): void {
    clearTimeout(this.#providersTimer);
    this.#providersTimer = setTimeout(() => void this.refreshProviders(), 1500);
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
    return this.conn.status === 'open' && !this.runningTurn;
  }

  send(text: string): boolean {
    const question = text.trim();
    if (!question || this.runningTurn) return false;
    if (this.conn.status !== 'open') {
      toasts.push('Sense connexió amb el servidor. Espera que es reconnecti.', 'error');
      return false;
    }
    const c = this.composer;
    const requestId = uuid();
    const options: TurnOptions = {
      debate: { rounds: c.rounds, consensus_threshold: c.threshold, synthesizer: c.synthesizer },
      use_cache: c.useCache,
    };
    const conversationId = this.convs.currentId;
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
        this.#resubscribe(msg.active_turns);
        return;
      case 'turn.unknown':
        void this.#onUnknown(msg.request_id);
        return;
      case 'error': {
        const turn = msg.request_id ? this.turns.get(msg.request_id) : undefined;
        if (turn && turn.status === 'pending') {
          turn.status = 'failed';
          turn.error = { kind: 'server', message: msg.message };
        } else {
          toasts.push(msg.message, 'error');
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
        if (!turn.question) turn.question = this.convs.questionFor(ev.turn_id) ?? '';
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
      case 'turn.completed':
        void this.convs.refresh();
        this.#refreshProvidersSoon();
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

  #resubscribe(active: { request_id: string; conversation_id: number; last_seq: number }[]): void {
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

  /** Live turns replayed after a reload have no question text until the conversation loads. */
  #fillQuestions(): void {
    for (const t of this.turns.forConversation(this.convs.currentId)) {
      if (!t.question && t.turnId != null) t.question = this.convs.questionFor(t.turnId) ?? '';
    }
  }
}

export const app = new App();

export { ApiError };
