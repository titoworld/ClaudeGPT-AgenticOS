<script lang="ts">
  import { onDestroy, tick, untrack } from 'svelte';
  import { app } from '../lib/app.svelte';
  import { lightDismiss, syncDialog } from '../lib/dialog';
  import { i18n } from '../lib/i18n/index.svelte';
  import { CONVERSATION_QUERY_MAX_LENGTH, TURN_MODES, type ConversationSummary, type TurnMode } from '../lib/protocol';
  import { router } from '../lib/router.svelte';
  import { fuzzyFilter, MODE_LABEL } from '../lib/text';
  import Icon, { type IconName } from './Icon.svelte';

  interface Command {
    id: string;
    label: string;
    group: 'actions' | 'conversations';
    icon: IconName;
    keywords?: string;
    hint?: string;
    /** Runs without closing the palette (e.g. «Show more conversations»). */
    keepOpen?: boolean;
    run: () => void;
  }

  /** Conversations per page of a search. */
  const SEARCH_PAGE = 12;
  /** Most recent conversations listed when nothing is typed. */
  const RECENT = 6;

  const uid = $props.id();
  let dialog: HTMLDialogElement | undefined = $state();
  let input: HTMLInputElement | undefined = $state();
  // The server searches every conversation, not only the pages loaded (A12).
  const search = app.convs.search(SEARCH_PAGE);
  onDestroy(() => search.dispose());
  const query = $derived(search.query);
  let activeIndex = $state(0);
  const t = $derived(i18n.m.app);

  function setMode(mode: TurnMode): void {
    app.composer.mode = mode;
    if (router.route.name !== 'chat') router.go({ name: 'chat', id: app.convs.currentId });
    app.focusComposer();
  }

  // In the language in force: a change of language makes them again.
  const actions: Command[] = $derived([
    { id: 'new', label: t.newConversation, group: 'actions', icon: 'plus', run: () => app.newConversation() },
    // Refine too: picking it here is the owner choosing it (it is never a default).
    ...TURN_MODES.map(
      (mode): Command => ({
        id: `mode-${mode}`,
        label: t.palette.switchMode(MODE_LABEL[mode]),
        group: 'actions',
        icon: `mode-${mode}`,
        keywords: mode === 'refine' ? `${t.palette.keywords.mode} ${t.palette.keywords.refine}` : t.palette.keywords.mode,
        run: () => setMode(mode),
      }),
    ),
    {
      id: 'dashboard',
      label: t.palette.openDashboard,
      group: 'actions',
      icon: 'dashboard',
      keywords: t.palette.keywords.dashboard,
      run: () => router.go({ name: 'dashboard' }),
    },
    {
      id: 'settings',
      label: t.settings,
      group: 'actions',
      icon: 'settings',
      keywords: t.palette.keywords.settings,
      run: () => (app.settingsOpen = true),
    },
    {
      id: 'logout',
      label: t.palette.logOut,
      group: 'actions',
      icon: 'logout',
      keywords: t.palette.keywords.logOut,
      run: () => void app.logout(),
    },
  ]);

  const conversationCommand = (c: ConversationSummary): Command => ({
    id: `conv-${c.id}`,
    label: c.title || t.untitled,
    group: 'conversations',
    icon: c.last_mode ? `mode-${c.last_mode}` : 'mode-solo',
    run: () => app.openConversation(c.id),
  });

  const results: Command[] = $derived.by(() => {
    const cmds = fuzzyFilter(actions, query, (c) => `${c.label} ${c.keywords ?? ''}`);
    if (!search.term) return [...cmds, ...app.convs.list.slice(0, RECENT).map(conversationCommand)];
    // Until the server answers, the known conversations that contain the text.
    const found = search.answered ? search.items : search.shown.slice(0, SEARCH_PAGE);
    const convs = found.map(conversationCommand);
    if (search.answered && search.hasMore) {
      convs.push({
        id: 'more',
        label: search.loading ? t.palette.loadingMore : t.palette.showMore,
        group: 'conversations',
        icon: 'chevron-down',
        keepOpen: true,
        run: () => void search.loadMore(),
      });
    } else if (search.error && !search.answered) {
      convs.push({
        id: 'retry',
        label: t.palette.searchAgain,
        group: 'conversations',
        icon: 'refresh',
        keepOpen: true,
        run: () => search.retry(),
      });
    }
    return [...cmds, ...convs];
  });

  /** How the conversation search goes: nothing claims there is no match before the server has said so. */
  const note = $derived(search.pending ? t.palette.searching : search.error);
  const hasConversations = $derived(results.some((c) => c.group === 'conversations'));

  $effect(() => syncDialog(dialog, app.paletteOpen));

  // Every opening starts afresh, and a closed palette searches nothing.
  $effect(() => {
    const open = app.paletteOpen;
    untrack(() => {
      search.query = '';
      activeIndex = 0;
    });
    if (open) void tick().then(() => input?.focus());
  });

  $effect(() => {
    void query;
    activeIndex = 0;
  });

  // The results can shrink under the active option (an answer of the server arrives).
  $effect(() => {
    const n = results.length;
    if (untrack(() => activeIndex) >= n) activeIndex = Math.max(0, n - 1);
  });

  function run(cmd: Command | undefined): void {
    if (!cmd) return;
    if (cmd.keepOpen) {
      cmd.run();
      input?.focus();
      return;
    }
    app.paletteOpen = false;
    cmd.run();
  }

  function scrollActiveIntoView(): void {
    void tick().then(() => document.getElementById(`${uid}-opt-${activeIndex}`)?.scrollIntoView({ block: 'nearest' }));
  }

  function onKeydown(e: KeyboardEvent): void {
    const n = results.length;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (n) activeIndex = (activeIndex + 1) % n;
      scrollActiveIntoView();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (n) activeIndex = (activeIndex - 1 + n) % n;
      scrollActiveIntoView();
    } else if (e.key === 'Home') {
      e.preventDefault();
      activeIndex = 0;
      scrollActiveIntoView();
    } else if (e.key === 'End') {
      e.preventDefault();
      activeIndex = Math.max(0, n - 1);
      scrollActiveIntoView();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      run(results[activeIndex]);
    }
  }
</script>

<dialog
  bind:this={dialog}
  class="palette"
  aria-label={t.palette.label}
  onclose={() => (app.paletteOpen = false)}
  {@attach lightDismiss}>
  <div class="panel glass">
    <div class="search">
      <Icon name="search" size={18} />
      <input
        bind:this={input}
        value={search.query}
        oninput={(e) => (search.query = e.currentTarget.value)}
        onkeydown={onKeydown}
        type="text"
        role="combobox"
        aria-expanded="true"
        aria-controls="{uid}-list"
        aria-activedescendant={results.length ? `${uid}-opt-${activeIndex}` : undefined}
        aria-autocomplete="list"
        maxlength={CONVERSATION_QUERY_MAX_LENGTH}
        placeholder={t.palette.placeholder}
        autocomplete="off"
        spellcheck="false" />
      <kbd>Esc</kbd>
    </div>

    <ul id="{uid}-list" role="listbox" aria-label={t.palette.results} aria-busy={search.pending || search.loading}>
      {#each results as cmd, i (cmd.id)}
        {#if i === 0 || results[i - 1]?.group !== cmd.group}
          <li class="group" role="presentation">{t.palette.groups[cmd.group]}</li>
          {#if cmd.group === 'conversations' && note}
            <li class="none" role="presentation">{note}</li>
          {/if}
        {/if}
        <li
          id="{uid}-opt-{i}"
          role="option"
          aria-selected={i === activeIndex}
          tabindex="-1"
          class:active={i === activeIndex}
          onmousemove={() => (activeIndex = i)}
          onclick={() => run(cmd)}
          onkeydown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              run(cmd);
            }
          }}>
          <Icon name={cmd.icon} size={16} />
          <span class="label">{cmd.label}</span>
          {#if i === activeIndex}<span class="enter" aria-hidden="true">↵</span>{/if}
        </li>
      {/each}
      {#if note && !hasConversations}
        <li class="group" role="presentation">{t.palette.groups.conversations}</li>
        <li class="none" role="presentation">{note}</li>
      {:else if !results.length}
        <li class="none" role="presentation">{t.palette.noResults(query)}</li>
      {/if}
    </ul>
  </div>
</dialog>

<style>
  .palette {
    margin: 12vh auto auto;
    width: min(38rem, calc(100vw - 1.5rem));
  }

  .panel {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    max-height: 70vh;
    border-radius: var(--radius-lg);
    overflow: hidden;
  }

  .palette[open] .panel {
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  .search {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    padding: 0.85rem 1rem;
    border-bottom: 1px solid var(--border);
    color: var(--text-muted);
  }

  input {
    flex: 1;
    min-width: 0;
    border: none;
    outline: none;
    background: none;
    font-size: 1rem;
    color: var(--text-primary);
  }

  input::placeholder {
    color: var(--text-muted);
  }

  ul {
    margin: 0;
    padding: 0.4rem;
    overflow-y: auto;
    list-style: none;
  }

  .group {
    padding: 0.55rem 0.65rem 0.25rem;
    font-size: 0.68rem;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  [role='option'] {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.55rem 0.65rem;
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    font-size: var(--text-sm);
    cursor: pointer;
  }

  [role='option'].active {
    background: rgb(139 156 255 / 0.16);
    color: var(--text-primary);
  }

  .label {
    flex: 1;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .enter {
    color: var(--text-muted);
    font-size: var(--text-xs);
  }

  .none {
    padding: 1.2rem 0.65rem;
    color: var(--text-muted);
    font-size: var(--text-sm);
  }
</style>
