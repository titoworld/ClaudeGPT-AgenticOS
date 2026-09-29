// The conversation list (audit A13, A18, N12): a refresh (after every turn) merges the
// fresh first page with the pages already loaded instead of going back to 50, a refresh
// that overlaps «Mostra'n més» never loses the conversation at the page boundary,
// «Mostra'n més» goes on when the last conversation loaded was deleted elsewhere, and an
// answer older than a change made here (a deletion, a rename, the end of the session)
// is dropped instead of bringing back what the change removed.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Conversations, SEARCH_DEBOUNCE_MS, searchTerm } from './conversations.svelte';
import type { ConversationSummary, RuntimeSettings } from './protocol';
import { at, conversation, conversations, ConversationServer } from './test-conversations';
import { sequence, usage } from './test-fixtures';
import { FakeApi, FakeSocket } from './test-server';

let server: ConversationServer;

function serve(list: ConversationSummary[]): ConversationServer {
  server = new ConversationServer(list);
  vi.stubGlobal('fetch', server.fetch);
  return server;
}

const ids = (list: readonly ConversationSummary[]) => list.map((c) => c.id);

/** The list is the start of what the server has, in its order: nothing skipped, nothing twice. */
function expectServerPrefix(convs: Conversations): void {
  expect(ids(convs.list)).toEqual(ids(server.ordered().slice(0, convs.list.length)));
}

/** Loads every page. */
async function loadAll(convs: Conversations): Promise<void> {
  for (let i = 0; convs.hasMore && i < 20; i++) await convs.loadMore();
}

/** Lets answers already released reach the store (and whatever they start). */
async function settled(convs: Conversations): Promise<void> {
  await vi.waitFor(() => {
    expect(convs.listLoading).toBe(false);
    expect(server.held).toEqual([]);
  });
}

/** Releases the held list answer made for `before` (null: a first page). */
function release(before: number | null): void {
  const i = server.held.findIndex((h) => h.request.before === before);
  expect(i, `a held list answer for before=${before}`).toBeGreaterThanOrEqual(0);
  server.held.splice(i, 1)[0]!.release();
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a refresh keeps the pages already loaded (A13)', () => {
  it('with 120 conversations, a refresh after two «Mostra\'n més» still has all 120', async () => {
    serve(conversations(120));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    await convs.loadMore();
    expect(convs.list).toHaveLength(120);
    expect(convs.hasMore).toBe(false);

    // A turn ends in conversation 7 (third page): it moves to the top.
    server.touch(7, 1000);
    await convs.refresh();
    expect(convs.list).toHaveLength(120);
    expect(convs.list[0]!.id).toBe(7);
    expect(convs.hasMore).toBe(false);
    expectServerPrefix(convs);
  });

  it('a new conversation joins at the top and nothing falls off the end', async () => {
    serve(conversations(60));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    expect(convs.list).toHaveLength(60);

    server.add({ ...conversation(61, 'Nova'), updated_at: at(1000) });
    await convs.refresh();
    expect(convs.list).toHaveLength(61);
    expect(convs.list[0]!.title).toBe('Nova');
    expect(convs.hasMore).toBe(false);
    expectServerPrefix(convs);
  });

  it('what the first page reaches is as the server has it now: renamed or gone elsewhere', async () => {
    serve(conversations(120));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    expect(convs.list).toHaveLength(100);
    expect(convs.hasMore).toBe(true);

    // Another device renames 110 and deletes 115 and 100 (all in the first page).
    server.conversations = server.conversations
      .filter((c) => c.id !== 115 && c.id !== 100)
      .map((c) => (c.id === 110 ? { ...c, title: 'Reanomenada en un altre dispositiu' } : c));
    await convs.refresh();
    expect(convs.list.find((c) => c.id === 110)?.title).toBe('Reanomenada en un altre dispositiu');
    expect(ids(convs.list)).not.toContain(115);
    expect(ids(convs.list)).not.toContain(100);
    expect(convs.list).toHaveLength(98);
    expect(convs.hasMore).toBe(true);
    expectServerPrefix(convs);

    // «Mostra'n més» goes on after the last one: the rest, once each.
    await loadAll(convs);
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
  });

  it('a first page shorter than a page is everything the server has', async () => {
    serve(conversations(60));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    for (let id = 41; id <= 55; id++) server.deleteElsewhere(id);
    await convs.refresh();
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
    expect(convs.list).toHaveLength(45);
    expect(convs.hasMore).toBe(false);
  });
});

describe('a refresh that overlaps «Mostra\'n més» (N12)', () => {
  /** 120 conversations, the first page loaded (120..71), then «Mostra'n més» and a refresh both under way. */
  async function overlap() {
    serve(conversations(120));
    const convs = new Conversations();
    await convs.refresh();
    expect(convs.list.at(-1)!.id).toBe(71);
    server.holdLists = true;
    const more = convs.loadMore(); // served now: after 71 -> 70..21
    // A turn ends in conversation 40 (second page): it moves to the top, and the app
    // refreshes the list while «Mostra'n més» is still on its way.
    server.touch(40, 1000);
    const fresh = convs.refresh(); // served now: 40, 120..72 (71 is no longer in the first page)
    server.holdLists = false;
    return { convs, more, fresh };
  }

  it('the fresh first page first, then the older next page: 71 is still there', async () => {
    const { convs, more, fresh } = await overlap();
    release(null);
    await fresh;
    release(71);
    await more;
    await settled(convs);
    expect(ids(convs.list)).toContain(71);
    expectServerPrefix(convs);
    await loadAll(convs);
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
    expect(convs.hasMore).toBe(false);
  });

  it('the older next page first, then the fresh first page: 71 is still there', async () => {
    const { convs, more, fresh } = await overlap();
    release(71);
    await more;
    release(null);
    await fresh;
    await settled(convs);
    expect(ids(convs.list)).toContain(71);
    expectServerPrefix(convs);
    await loadAll(convs);
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
  });

  it('the «Mostra\'n més» that the refresh superseded goes on by itself, after the boundary', async () => {
    const { convs, more, fresh } = await overlap();
    release(null);
    await fresh;
    release(71);
    await more;
    await settled(convs);
    // 50 fresh + 71 kept + the next 50 after 71, asked again without another click.
    expect(convs.list).toHaveLength(101);
    expect(server.lists.at(-1)).toMatchObject({ before: 71, q: null, background: false });
    expectServerPrefix(convs);
    expect(convs.hasMore).toBe(true);
  });
});

describe('answers older than a change made here are dropped (A18)', () => {
  const three = () => [conversation(1, 'Alfa'), conversation(2, 'Beta'), conversation(3, 'Gamma')];

  it('a deleted conversation does not come back with a list served before the deletion', async () => {
    serve(three());
    const convs = new Conversations();
    await convs.refresh();
    server.holdLists = true;
    const stale = convs.refresh(); // served now: Beta still exists
    server.holdLists = false;
    await convs.remove(2);
    expect(ids(convs.list)).toEqual([3, 1]);
    release(null);
    await stale;
    await settled(convs);
    expect(ids(convs.list)).toEqual([3, 1]);
  });

  it('a renamed conversation keeps its new title, in the list and in the top bar', async () => {
    serve(three());
    const convs = new Conversations();
    await convs.refresh();
    await convs.open(3);
    server.holdLists = true;
    const stale = convs.refresh(); // served now: «Gamma»
    server.holdLists = false;
    await convs.rename(3, 'Gamma revisada');
    release(null);
    await stale;
    await settled(convs);
    expect(convs.list.map((c) => c.title)).toEqual(['Gamma revisada', 'Beta', 'Alfa']);
    expect(convs.currentTitle).toBe('Gamma revisada');
  });

  it('a list answer that a change made stale is asked again: what it brought is not lost', async () => {
    serve(three());
    const convs = new Conversations();
    await convs.refresh();
    // A turn created conversation 4 and the app refreshes the list...
    server.add({ ...conversation(4, 'Delta'), updated_at: at(1000) });
    server.holdLists = true;
    const stale = convs.refresh();
    server.holdLists = false;
    // ...while the owner renames another one.
    await convs.rename(1, 'Alfa nova');
    release(null);
    await stale;
    await settled(convs);
    expect(convs.list.map((c) => c.title)).toEqual(['Delta', 'Gamma', 'Beta', 'Alfa nova']);
  });

  it('«Mostra\'n més» served before a deletion does not bring the deleted conversation', async () => {
    serve(conversations(60));
    const convs = new Conversations();
    await convs.refresh();
    server.holdLists = true;
    const more = convs.loadMore(); // served now: 10..1, conversation 5 included
    server.holdLists = false;
    await convs.remove(5); // e.g. found with the search
    release(11);
    await more;
    await settled(convs);
    expect(ids(convs.list)).not.toContain(5);
    expectServerPrefix(convs);
  });

  it('an error of an older request does not stay on screen', async () => {
    serve(three());
    const convs = new Conversations();
    await convs.refresh();
    server.holdLists = true;
    const old = convs.refresh();
    server.holdLists = false;
    await convs.refresh(); // a newer one works
    server.held.splice(0, 1)[0]!.fail(503, 'Servei no disponible.');
    await old;
    expect(convs.listError).toBeNull();
    expect(convs.listLoading).toBe(false);
    expect(ids(convs.list)).toEqual([3, 2, 1]);
  });

  it('after the end of the session, nothing refills the list, not even a late 401', async () => {
    serve(three());
    const convs = new Conversations();
    await convs.refresh();
    server.holdLists = true;
    const first = convs.refresh();
    const second = convs.refresh();
    convs.clear(); // logout
    server.held[0]!.release();
    server.held[1]!.fail(401, 'Cal iniciar sessió.');
    await Promise.all([first, second]);
    expect(convs.list).toEqual([]);
    expect(convs.hasMore).toBe(false);
    expect(convs.listLoading).toBe(false);
    expect(convs.listError).toBeNull();
  });

  it('deleting a conversation that is already gone (404) drops it from the list too', async () => {
    serve(three());
    const convs = new Conversations();
    await convs.refresh();
    server.deleteElsewhere(2); // e.g. from another tab
    await expect(convs.remove(2)).resolves.toBeUndefined();
    expect(ids(convs.list)).toEqual([3, 1]);
  });

  it('once every conversation loaded is deleted, the next ones are shown', async () => {
    serve(conversations(60));
    const convs = new Conversations();
    await convs.refresh();
    for (const c of [...convs.list]) await convs.remove(c.id);
    await settled(convs);
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
    expect(convs.list).toHaveLength(10);
    expect(convs.hasMore).toBe(false);
  });
});

describe('«Mostra\'n més» after the last conversations loaded were deleted elsewhere (A13)', () => {
  // The server has no page after a conversation that no longer exists (it answers []).

  it('goes on after them: nothing becomes unreachable (120 conversations, 100 loaded)', async () => {
    serve(conversations(120));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    expect(convs.list.at(-1)!.id).toBe(21);
    server.deleteElsewhere(21); // e.g. from the phone
    await convs.loadMore();
    expect(ids(convs.list)).toEqual(ids(server.ordered())); // 20..1 after 22, and 21 gone
    expect(convs.hasMore).toBe(false);
    // A turn ends: the refresh keeps them all.
    server.touch(120, 2000);
    await convs.refresh();
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
  });

  it('keeps «Mostra\'n més» while there is more, also after a refresh (200 conversations)', async () => {
    serve(conversations(200));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    server.deleteElsewhere(101);
    await convs.loadMore();
    expect(ids(convs.list)).not.toContain(101);
    expect(convs.list).toHaveLength(149);
    expect(convs.hasMore).toBe(true);
    expectServerPrefix(convs);

    server.touch(150, 2000);
    await convs.refresh();
    expect(convs.hasMore).toBe(true);
    await loadAll(convs);
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
  });

  it.each([
    ['as it was', []],
    ['without its last conversation, deleted elsewhere', [1]],
  ] as [string, number[]][])('ends where the list really ends, after a full page (%s)', async (_how, deleted) => {
    serve(conversations(100));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    expect(convs.hasMore).toBe(true); // the second page was full: there may be more
    for (const id of deleted) server.deleteElsewhere(id);
    await convs.loadMore();
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
    expect(convs.hasMore).toBe(false);
  });

  it('steps back over a few of them per «Mostra\'n més», and the next one goes on', async () => {
    serve(conversations(200));
    const convs = new Conversations();
    await convs.refresh();
    await convs.loadMore();
    await convs.loadMore();
    expect(convs.list.at(-1)!.id).toBe(51);
    for (let id = 51; id <= 65; id++) server.deleteElsewhere(id); // the last 15 loaded
    const sent = server.lists.length;
    await convs.loadMore();
    // A few requests, not one for every conversation loaded; some of the deleted ones are dropped.
    expect(server.lists.length - sent).toBeLessThanOrEqual(11);
    expect(ids(convs.list)).not.toContain(51);
    expect(convs.hasMore).toBe(true);
    await loadAll(convs);
    expect(ids(convs.list)).toEqual(ids(server.ordered()));
  });

  it('in a search too', async () => {
    vi.useFakeTimers();
    try {
      // Odd ids are «Informe N», even ones «Nota N».
      const titles = Object.fromEntries(
        Array.from({ length: 300 }, (_, i) => [i + 1, i % 2 ? `Nota ${i + 1}` : `Informe ${i + 1}`]),
      );
      serve(conversations(300, titles));
      const convs = new Conversations();
      await convs.refresh();
      const search = convs.search();
      search.query = 'informe';
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS);
      await vi.waitFor(() => expect(search.answered).toBe(true));
      expect(search.items.at(-1)!.id).toBe(201);
      expect(search.hasMore).toBe(true);

      server.deleteElsewhere(201);
      await search.loadMore();
      const found = server.ordered().filter((c) => c.title.startsWith('Informe'));
      expect(ids(search.items)).toEqual(ids(found.slice(0, 99))); // 299..203, then 199..101
      expect(search.hasMore).toBe(true); // 99..1 are still to come
      search.dispose();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('the app when a turn ends (A13)', () => {
  const SETTINGS: RuntimeSettings = {
    revision: 1,
    default_mode: 'solo',
    default_target: 'claude',
    debate: { rounds: 1, consensus_threshold: 80, synthesizer: 'claude' },
    use_cache: false,
    compaction_threshold_tokens: 6000,
    models: { claude: null, chatgpt: null },
    fast_models: { claude: null, chatgpt: null },
    prices: {},
    fx: { mode: 'manual', eur_per_usd: 0.9 },
    budgets_eur: { claude: null, chatgpt: null },
    plans_eur: { claude: null, chatgpt: null },
    pdf_in_revisions: 'text',
  };

  it('keeps the pages loaded, and the old conversation that is open stays in the list', async () => {
    server = new ConversationServer(conversations(120), new FakeApi(SETTINGS).fetch);
    vi.stubGlobal('fetch', server.fetch);
    vi.stubGlobal('WebSocket', FakeSocket);
    FakeSocket.all = [];
    history.replaceState(null, '', '#/');
    vi.resetModules();
    const { app } = await import('./app.svelte');
    try {
      await app.init();
      await vi.waitFor(() => expect(app.convs.list).toHaveLength(50));
      await app.convs.loadMore();
      await app.convs.loadMore();
      expect(app.convs.list).toHaveLength(120);
      await app.syncRoute({ name: 'chat', id: 7 }); // from the third page

      // A turn that was running in conversation 90 (e.g. started before a reload) ends.
      const socket = FakeSocket.last();
      socket.open();
      socket.receive({
        type: 'hello', version: '0.2.0', providers: [], fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' },
        active_turns: [{ request_id: 'r90', conversation_id: 90, last_seq: 0 }],
      });
      server.touch(90, 1000);
      for (const ev of sequence('r90', [
        { type: 'turn.started', conversation_id: 90, turn_id: 900, mode: 'solo', new_conversation: false },
        {
          type: 'turn.completed', conversation_id: 90, turn_id: 900, final_message_ids: [901], usage: usage(10, 5),
          savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null },
          consensus: null, cached: false,
        },
      ])) {
        socket.receive(ev);
      }
      await vi.waitFor(() => expect(app.convs.list[0]!.id).toBe(90));
      expect(app.convs.list).toHaveLength(120);
      expect(app.convs.hasMore).toBe(false);
      expect(app.convs.list.some((c) => c.id === 7)).toBe(true);
      expectServerPrefix(app.convs);
    } finally {
      app.toLogin();
    }
  });
});

describe('the text a search sends (A12)', () => {
  it('is trimmed as the server trims it, and cut to 200 characters', () => {
    expect(searchTerm('  zebra\t')).toBe('zebra');
    // Python's str.strip also removes these; the browser's trim removes the BOM.
    expect(searchTerm('\u001c zebra \u0085')).toBe('zebra');
    expect(searchTerm('\ufeffzebra')).toBe('zebra');
    expect(searchTerm('   ')).toBe('');
    // Runs of spaces count as one, as in the stored titles.
    expect(searchTerm('número \t  6')).toBe('número 6');
    expect(searchTerm('a'.repeat(250))).toBe('a'.repeat(200));
    // Characters, not UTF-16 units: the server counts 200 emoji as 200.
    expect(searchTerm('\u{1F993}'.repeat(250))).toBe('\u{1F993}'.repeat(200));
    // Cut after a space: the space goes too.
    expect(searchTerm(`${'x'.repeat(199)} ${'y'.repeat(10)}`)).toBe('x'.repeat(199));
  });
});

describe('a search and the list (A12)', () => {
  it('follows the list: when a turn ends, the results are asked again', async () => {
    vi.useFakeTimers();
    try {
      serve(conversations(60, { 5: 'Pressupost zebra irrepetible' }));
      const convs = new Conversations();
      await convs.refresh();
      const search = convs.search();
      search.query = 'zebra';
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS);
      await vi.waitFor(() => expect(search.answered).toBe(true));
      expect(ids(search.items)).toEqual([5]);

      // A turn created a conversation whose title matches, then the app refreshes the list.
      server.add({ ...conversation(61, 'Zebra: una altra'), updated_at: at(1000) });
      await convs.refresh({ background: true });
      await vi.waitFor(() => expect(ids(search.items)).toEqual([61, 5]));
      expect(server.lists.slice(-2)).toContainEqual({ limit: 50, before: null, q: 'zebra', background: true });

      // Once its box is gone it follows nothing.
      search.dispose();
      const sent = server.lists.length;
      await convs.refresh();
      expect(server.lists).toHaveLength(sent + 1);
    } finally {
      vi.useRealTimers();
    }
  });
});
