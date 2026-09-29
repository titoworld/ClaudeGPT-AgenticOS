// Conversation list and the open conversation (REST), mapped to turn views, and the
// searches of the sidebar and the command palette.

import { api, ApiError, RequestTimeoutError, type RequestOptions } from './api';
import { CONVERSATION_QUERY_MAX_LENGTH, type ConversationDetail, type ConversationSummary } from './protocol';
import { turnsFromMessages, type TurnView } from './turns.svelte';

const PAGE = 50;

/**
 * How many conversations one «Mostra'n més» steps back over when the last ones shown
 * were deleted in another tab or device; if there were more, the next one goes on.
 */
const GONE_CURSOR_STEPS = 10;

/** How long a search waits after the last keystroke before asking the server. */
export const SEARCH_DEBOUNCE_MS = 250;

const SEARCH_FAILED = "No s'ha pogut fer la cerca.";

export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 0 || err.status >= 500) return fallback;
    return err.message || fallback;
  }
  if (err instanceof RequestTimeoutError) return err.message;
  if (err instanceof TypeError) return 'No es pot connectar amb el servidor.';
  return fallback;
}

// Blank space as the browser (String#trim) and the server (Python's str.strip) see it.
const EDGE_SPACE = /^[\s\x1c-\x1f\x85]+|[\s\x1c-\x1f\x85]+$/g;
const SPACE_RUN = /[\s\x1c-\x1f\x85]+/g;

/**
 * What a search sends as `q`: the text typed without the spaces around it and with
 * its runs of spaces as one (the server compares it so, as titles are stored), cut to
 * the characters the server accepts ('' when there is nothing to search for).
 */
export function searchTerm(text: string): string {
  const chars = Array.from(text.replace(EDGE_SPACE, '').replace(SPACE_RUN, ' '));
  if (chars.length <= CONVERSATION_QUERY_MAX_LENGTH) return chars.join('');
  return chars.slice(0, CONVERSATION_QUERY_MAX_LENGTH).join('').replace(EDGE_SPACE, '');
}

/**
 * Case and accents folded about as the server's search does (NFKD, case folding,
 * combining marks dropped), for the matches shown before it answers.
 */
function fold(text: string): string {
  return text.normalize('NFKD').replace(/\p{Mn}/gu, '').toLowerCase().replace(SPACE_RUN, ' ');
}

/**
 * Whether `a` comes after `b` in the server's order: most recent activity first
 * (`updated_at`), then the highest id.
 */
function comesAfter(a: ConversationSummary, b: ConversationSummary): boolean {
  const ta = Date.parse(a.updated_at) || 0;
  const tb = Date.parse(b.updated_at) || 0;
  return ta < tb || (ta === tb && a.id < b.id);
}

/**
 * A fresh first page merged with the pages loaded before it (A13). What the first page
 * reaches is as the server has it now (moved to the top, renamed or gone); what it does
 * not reach keeps its place after it. So a refresh never shortens the list, and never
 * loses the conversation at a page boundary when it overlaps «Mostra'n més» (N12).
 */
export function mergeFirstPage(
  first: readonly ConversationSummary[],
  loaded: readonly ConversationSummary[],
  loadedHasMore: boolean,
  pageSize: number,
): { items: ConversationSummary[]; hasMore: boolean } {
  const boundary = first.at(-1);
  // A short first page is everything the server has.
  if (!boundary || first.length < pageSize) return { items: [...first], hasMore: false };
  const fresh = new Set(first.map((c) => c.id));
  const rest = loaded.filter((c) => !fresh.has(c.id) && comesAfter(c, boundary));
  return { items: [...first, ...rest], hasMore: rest.length > 0 ? loadedHasMore : true };
}

/** The conversations of `lists` (the first one wins for an id) whose title contains `term`, in the server's order. */
function matching(term: string, ...lists: readonly (readonly ConversationSummary[])[]): ConversationSummary[] {
  const needle = fold(term);
  const found = new Map<number, ConversationSummary>();
  for (const list of lists) {
    for (const c of list) {
      if (!found.has(c.id) && fold(c.title).includes(needle)) found.set(c.id, c);
    }
  }
  return [...found.values()].sort((a, b) => (comesAfter(a, b) ? 1 : comesAfter(b, a) ? -1 : 0));
}

/**
 * A list request under way: what to ask again if a change made here makes its answer
 * stale. `thenMore`: a «Mostra'n més» it superseded, to go on with afterwards.
 */
type ListRequest = { kind: 'first'; options: RequestOptions; thenMore: boolean } | { kind: 'more' };

/**
 * Pages of conversations, newest first: all of them (the sidebar's list) or, in a
 * search, those whose title contains `term`.
 *
 * Every request, and every change made here (a rename, a deletion, the end of the
 * session), starts a new generation; an answer or an error of an older generation is
 * dropped (A18). The server may have served it before the change, so it could bring
 * back a deleted conversation or an old title. A request that a change made stale is
 * asked again, so what it would have brought (e.g. a conversation a turn has just
 * created) is not lost.
 */
export class ConversationPages {
  items: ConversationSummary[] = $state.raw([]);
  hasMore = $state(false);
  /** A request is under way. */
  loading = $state(false);
  error: string | null = $state(null);
  /** The text searched for ('' for every conversation). */
  term = $state('');
  /** The `term` that `items` answers, once a first page has arrived (null until then). */
  loadedTerm: string | null = $state(null);
  readonly pageSize: number;

  #generation = 0;
  #inFlight: ListRequest | null = null;

  constructor(pageSize = PAGE) {
    this.pageSize = pageSize;
  }

  /**
   * The first page again, merged with the pages already loaded. A «Mostra'n més» under
   * way is superseded (its page was served before this one), and goes on once the
   * first page is in.
   */
  refresh(options: RequestOptions = {}): Promise<void> {
    const pending = this.#inFlight;
    return this.#first(options, pending?.kind === 'more' || (pending?.kind === 'first' && pending.thenMore));
  }

  async #first(options: RequestOptions, thenMore: boolean): Promise<void> {
    const term = this.term;
    const generation = this.#begin({ kind: 'first', options, thenMore });
    let applied = false;
    try {
      const page = await api.conversations(this.pageSize, undefined, term || undefined, options);
      if (generation !== this.#generation) return;
      const loaded = this.loadedTerm === term ? this.items : [];
      const merged = mergeFirstPage(page, loaded, this.hasMore, this.pageSize);
      this.items = merged.items;
      this.hasMore = merged.hasMore;
      this.loadedTerm = term;
      this.error = null;
      applied = true;
    } catch (err) {
      if (generation !== this.#generation) return;
      this.error = errorMessage(err, term ? SEARCH_FAILED : "No s'han pogut carregar les converses.");
    } finally {
      this.#end(generation);
    }
    if (applied && thenMore) await this.loadMore();
  }

  /**
   * The page after the last conversation shown («Mostra'n més»). The server has no page
   * after a conversation that no longer exists: when the last ones shown were deleted in
   * another tab or device, it goes on from the one before them (A13), and those its page
   * leaves out are dropped (gone, or moved to the top, where the next refresh has them).
   */
  async loadMore(): Promise<void> {
    const term = this.term;
    const items = this.items;
    if (!items.length || this.loading || this.loadedTerm !== term) return;
    const hadMore = this.hasMore;
    const generation = this.#begin({ kind: 'more' });
    try {
      let at = items.length - 1;
      let page = await api.conversations(this.pageSize, items[at]!.id, term || undefined);
      let steps = 0;
      while (generation === this.#generation && !page.length && hadMore && at > 0 && steps < GONE_CURSOR_STEPS) {
        steps++;
        at--;
        page = await api.conversations(this.pageSize, items[at]!.id, term || undefined);
      }
      if (generation !== this.#generation) return;
      // Out of steps with nothing found: the last one asked goes too (gone, or the end of
      // the list, which the next «Mostra'n més» brings back), and that one steps back further.
      const outOfSteps = !page.length && steps === GONE_CURSOR_STEPS && at > 0;
      const kept = items.slice(0, outOfSteps ? at : at + 1);
      const known = new Set(kept.map((c) => c.id));
      this.items = [...kept, ...page.filter((c) => !known.has(c.id))];
      this.hasMore = page.length === this.pageSize || outOfSteps;
      this.error = null;
    } catch (err) {
      if (generation !== this.#generation) return;
      this.error = errorMessage(
        err,
        term ? "No s'han pogut carregar més resultats." : "No s'han pogut carregar més converses.",
      );
    } finally {
      this.#end(generation);
    }
  }

  /** A conversation got a new title (renaming does not move it). */
  renamed(summary: ConversationSummary): void {
    if (this.items.some((c) => c.id === summary.id)) {
      this.items = this.items.map((c) => (c.id === summary.id ? summary : c));
    }
    this.#changed();
  }

  /** A conversation no longer exists. */
  removed(id: number): void {
    if (this.items.some((c) => c.id === id)) this.items = this.items.filter((c) => c.id !== id);
    this.#changed();
  }

  /** Drops the answer of the request under way, if any (e.g. it was for another text). */
  cancel(): void {
    this.#generation++;
    this.#inFlight = null;
    this.loading = false;
  }

  /** Forgets everything loaded, and drops what is still on its way. */
  reset(): void {
    this.cancel();
    this.items = [];
    this.hasMore = false;
    this.error = null;
    this.loadedTerm = null;
  }

  #begin(request: ListRequest): number {
    this.#inFlight = request;
    this.loading = true;
    return ++this.#generation;
  }

  #end(generation: number): void {
    if (generation !== this.#generation) return;
    this.#inFlight = null;
    this.loading = false;
  }

  /** A change made here after the request under way was sent: drop its answer and ask again. */
  #changed(): void {
    const stale = this.#inFlight;
    this.cancel();
    if (stale?.kind === 'first') {
      void this.#first(stale.options, stale.thenMore);
    } else if (this.items.length === 0 && this.hasMore && this.loadedTerm === this.term) {
      // Every conversation loaded is gone but the server has more: nothing to go on from.
      void this.#first({}, false);
    } else if (stale?.kind === 'more') {
      void this.loadMore();
    }
  }
}

/**
 * The sidebar's or the palette's search (A12). The server searches every conversation,
 * not only the pages loaded, a moment after the last keystroke, and an answer for an
 * older text is dropped. Until the answer for the text typed arrives, `shown` has the
 * known conversations that contain it and `pending` is true: nothing may say there is
 * no match yet.
 */
export class ConversationSearch extends ConversationPages {
  #query = $state('');
  #timer: ReturnType<typeof setTimeout> | undefined;
  readonly #convs: Conversations;

  constructor(convs: Conversations, pageSize = PAGE) {
    super(pageSize);
    this.#convs = convs;
  }

  /** The text typed (the search box binds to it). */
  get query(): string {
    return this.#query;
  }

  set query(text: string) {
    this.#query = text;
    const term = searchTerm(text);
    if (term === this.term) return;
    this.term = term;
    this.#stopTimer();
    this.cancel(); // an answer on its way is for another text
    this.error = null;
    if (term) {
      this.#timer = setTimeout(() => {
        this.#timer = undefined;
        void this.refresh();
      }, SEARCH_DEBOUNCE_MS);
    }
  }

  /** The server has answered for the text typed. */
  get answered(): boolean {
    return this.term !== '' && this.loadedTerm === this.term;
  }

  /** A text is typed and the server has neither answered for it nor failed. */
  get pending(): boolean {
    return this.term !== '' && this.loadedTerm !== this.term && this.error === null;
  }

  /**
   * What to show for the text typed: the server's answer once it has arrived; until
   * then, the conversations already known (the list loaded, the previous answer) whose
   * title contains it.
   */
  get shown(): ConversationSummary[] {
    if (!this.term) return [];
    if (this.loadedTerm === this.term) return this.items;
    return matching(this.term, this.#convs.list, this.items);
  }

  /** «Torna-ho a provar» after a search that failed. */
  retry(): void {
    if (!this.term) return;
    this.#stopTimer();
    this.error = null;
    void this.refresh();
  }

  /** The list is being refreshed (a turn ended...): the results are too. */
  follow(options: RequestOptions): void {
    if (this.term && this.#timer === undefined) void this.refresh(options);
  }

  override reset(): void {
    this.#stopTimer();
    this.#query = '';
    this.term = '';
    super.reset();
  }

  /** The search box is gone. */
  dispose(): void {
    this.reset();
    this.#convs.forget(this);
  }

  #stopTimer(): void {
    clearTimeout(this.#timer);
    this.#timer = undefined;
  }
}

export class Conversations {
  /** Every conversation, newest first: the sidebar's list. */
  readonly #all = new ConversationPages();
  /** Searches whose box is shown: they follow the changes made here. */
  readonly #searches = new Set<ConversationSearch>();

  currentId: number | null = $state(null);
  detail: ConversationDetail | null = $state.raw(null);
  storedTurns: TurnView[] = $state.raw([]);
  loading = $state(false);
  loadError: string | null = $state(null);

  #token = 0;

  get list(): ConversationSummary[] {
    return this.#all.items;
  }

  get hasMore(): boolean {
    return this.#all.hasMore;
  }

  get listLoading(): boolean {
    return this.#all.loading;
  }

  get listError(): string | null {
    return this.#all.error;
  }

  get currentTitle(): string {
    if (this.currentId == null) return 'Nova conversa';
    return this.list.find((c) => c.id === this.currentId)?.title || this.detail?.title || 'Conversa';
  }

  /**
   * The first page again, merged with the pages already loaded; the searches under way
   * are asked again too. `background`: the app refreshes by itself (after a turn), not
   * because the owner did something.
   */
  refresh(options: RequestOptions = {}): Promise<void> {
    for (const search of this.#searches) search.follow(options);
    return this.#all.refresh(options);
  }

  /** «Mostra'n més». */
  loadMore(): Promise<void> {
    return this.#all.loadMore();
  }

  /** A search for a search box (the sidebar's, the palette's); `dispose()` it when the box goes away. */
  search(pageSize = PAGE): ConversationSearch {
    const search = new ConversationSearch(this, pageSize);
    this.#searches.add(search);
    return search;
  }

  /** A disposed search stops following the list. */
  forget(search: ConversationSearch): void {
    this.#searches.delete(search);
  }

  /** Open a conversation; resolves false if it does not exist. `background`: the app reloads it by itself. */
  async open(id: number, { silent = false, background = false } = {}): Promise<boolean> {
    const token = ++this.#token;
    if (this.currentId !== id) {
      this.currentId = id;
      this.detail = null;
      this.storedTurns = [];
    }
    if (!silent) this.loading = true;
    this.loadError = null;
    try {
      const detail = await api.conversation(id, { background });
      if (token !== this.#token) return true;
      this.detail = detail;
      this.storedTurns = turnsFromMessages(detail.messages, detail.id);
      return true;
    } catch (err) {
      if (token !== this.#token) return true;
      if (err instanceof ApiError && err.status === 404) {
        this.#gone(id);
        return false;
      }
      this.loadError = errorMessage(err, "No s'ha pogut carregar la conversa.");
      return true;
    } finally {
      if (token === this.#token) this.loading = false;
    }
  }

  /** Reload the open conversation without a spinner (e.g. after turn.unknown, which passes `background`). */
  async reload({ background = false }: RequestOptions = {}): Promise<void> {
    if (this.currentId != null) await this.open(this.currentId, { silent: true, background });
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
    let summary: ConversationSummary;
    try {
      summary = await api.renameConversation(id, title);
    } catch (err) {
      // It no longer exists (deleted in another tab or device): out of the lists too.
      if (err instanceof ApiError && err.status === 404) this.#gone(id);
      throw err;
    }
    for (const pages of this.#pages()) pages.renamed(summary);
    if (this.detail?.id === id) this.detail = { ...this.detail, title: summary.title };
  }

  async remove(id: number): Promise<void> {
    try {
      await api.deleteConversation(id);
    } catch (err) {
      // Already gone (deleted in another tab or device): what the owner asked for.
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
    this.#gone(id);
  }

  /** The session ended: nothing of it stays, and answers still on their way are dropped. */
  clear(): void {
    this.startNew();
    for (const pages of this.#pages()) pages.reset();
  }

  #pages(): ConversationPages[] {
    return [this.#all, ...this.#searches];
  }

  #gone(id: number): void {
    for (const pages of this.#pages()) pages.removed(id);
  }
}
