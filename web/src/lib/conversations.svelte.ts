// Conversation list and the open conversation (REST), mapped to turn views.

import { api, ApiError, RequestTimeoutError } from './api';
import type { ConversationDetail, ConversationSummary } from './protocol';
import { turnsFromMessages, type TurnView } from './turns.svelte';

const PAGE = 50;

export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 0 || err.status >= 500) return fallback;
    return err.message || fallback;
  }
  if (err instanceof RequestTimeoutError) return err.message;
  if (err instanceof TypeError) return 'No es pot connectar amb el servidor.';
  return fallback;
}

export class Conversations {
  list: ConversationSummary[] = $state([]);
  hasMore = $state(false);
  listLoading = $state(false);
  listError: string | null = $state(null);

  currentId: number | null = $state(null);
  detail: ConversationDetail | null = $state.raw(null);
  storedTurns: TurnView[] = $state.raw([]);
  loading = $state(false);
  loadError: string | null = $state(null);

  #token = 0;

  get currentTitle(): string {
    if (this.currentId == null) return 'Nova conversa';
    return this.list.find((c) => c.id === this.currentId)?.title || this.detail?.title || 'Conversa';
  }

  async refresh(): Promise<void> {
    this.listLoading = true;
    try {
      const page = await api.conversations(PAGE);
      this.list = page;
      this.hasMore = page.length === PAGE;
      this.listError = null;
    } catch (err) {
      this.listError = errorMessage(err, "No s'han pogut carregar les converses.");
    } finally {
      this.listLoading = false;
    }
  }

  async loadMore(): Promise<void> {
    const lastItem = this.list.at(-1);
    if (!lastItem || this.listLoading) return;
    this.listLoading = true;
    try {
      const page = await api.conversations(PAGE, lastItem.id);
      const known = new Set(this.list.map((c) => c.id));
      this.list = [...this.list, ...page.filter((c) => !known.has(c.id))];
      this.hasMore = page.length === PAGE;
    } catch (err) {
      this.listError = errorMessage(err, "No s'han pogut carregar més converses.");
    } finally {
      this.listLoading = false;
    }
  }

  /** Open a conversation; resolves false if it does not exist. */
  async open(id: number, { silent = false } = {}): Promise<boolean> {
    const token = ++this.#token;
    if (this.currentId !== id) {
      this.currentId = id;
      this.detail = null;
      this.storedTurns = [];
    }
    if (!silent) this.loading = true;
    this.loadError = null;
    try {
      const detail = await api.conversation(id);
      if (token !== this.#token) return true;
      this.detail = detail;
      this.storedTurns = turnsFromMessages(detail.messages, detail.id);
      return true;
    } catch (err) {
      if (token !== this.#token) return true;
      if (err instanceof ApiError && err.status === 404) {
        this.list = this.list.filter((c) => c.id !== id);
        return false;
      }
      this.loadError = errorMessage(err, "No s'ha pogut carregar la conversa.");
      return true;
    } finally {
      if (token === this.#token) this.loading = false;
    }
  }

  /** Reload the open conversation in the background (e.g. after turn.unknown). */
  async reload(): Promise<void> {
    if (this.currentId != null) await this.open(this.currentId, { silent: true });
  }

  startNew(): void {
    this.#token++;
    this.currentId = null;
    this.detail = null;
    this.storedTurns = [];
    this.loading = false;
    this.loadError = null;
  }

  /** The draft conversation just got an id from the server (turn.started). */
  adopt(id: number): void {
    this.#token++;
    this.currentId = id;
    this.detail = null;
    this.storedTurns = [];
    this.loading = false;
  }

  async rename(id: number, title: string): Promise<void> {
    const summary = await api.renameConversation(id, title);
    this.list = this.list.map((c) => (c.id === id ? summary : c));
    if (this.detail?.id === id) this.detail = { ...this.detail, title: summary.title };
  }

  async remove(id: number): Promise<void> {
    await api.deleteConversation(id);
    this.list = this.list.filter((c) => c.id !== id);
  }

  clear(): void {
    this.startNew();
    this.list = [];
    this.hasMore = false;
    this.listError = null;
  }
}
