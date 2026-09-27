<script lang="ts">
  import { app } from '../lib/app.svelte';
  import { prefs } from '../lib/prefs.svelte';
  import { router } from '../lib/router.svelte';
  import { fuzzyFilter, groupConversations } from '../lib/text';
  import { isTerminal } from '../lib/turns.svelte';
  import BrandMark from './BrandMark.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import ConversationItem from './ConversationItem.svelte';
  import Icon from './Icon.svelte';
  import ProviderBadge from './ProviderBadge.svelte';

  const convs = app.convs;
  let query = $state('');
  let pendingDelete: { id: number; title: string } | null = $state(null);

  const filtered = $derived(fuzzyFilter(convs.list, query, (c) => c.title));
  const groups = $derived(groupConversations(filtered));
  const runningIds = $derived(
    new Set(
      Object.values(app.turns.turns)
        .filter((t) => !isTerminal(t.status) && t.conversationId != null)
        .map((t) => t.conversationId),
    ),
  );
  const isDashboard = $derived(router.route.name === 'dashboard');

  function close(): void {
    if (prefs.narrow) app.sidebarOpen = false;
    else prefs.setSidebarCollapsed(true);
  }
</script>

<aside class="sidebar glass" class:open={app.sidebarOpen} aria-label="Converses i navegació">
  <div class="brand">
    <BrandMark size={26} />
    <span class="brand-name">ClaudeGPT <b>OS</b></span>
    <button type="button" class="icon-btn" onclick={close} aria-label={prefs.narrow ? 'Tanca el menú' : 'Amaga la barra lateral'}>
      <Icon name={prefs.narrow ? 'x' : 'sidebar'} />
    </button>
  </div>

  <button type="button" class="btn primary new" onclick={() => app.newConversation()}>
    <Icon name="plus" size={16} />Nova conversa
  </button>

  <div class="search">
    <Icon name="search" size={15} />
    <input
      type="search"
      class="search-input"
      placeholder="Cerca converses"
      aria-label="Cerca converses"
      bind:value={query} />
  </div>

  <nav class="list" aria-label="Converses">
    {#each groups as group (group.label)}
      <section>
        <h2>{group.label}</h2>
        <ul>
          {#each group.items as conv (conv.id)}
            <ConversationItem
              {conv}
              active={!isDashboard && conv.id === convs.currentId}
              running={runningIds.has(conv.id)}
              onRename={(title) => app.renameConversation(conv.id, title)}
              onDelete={() => (pendingDelete = { id: conv.id, title: conv.title })} />
          {/each}
        </ul>
      </section>
    {:else}
      <p class="none">
        {#if convs.listError}
          {convs.listError}
        {:else if query}
          Cap conversa coincideix amb «{query}».
        {:else if convs.listLoading}
          Carregant…
        {:else}
          Encara no hi ha converses.
        {/if}
      </p>
    {/each}
    {#if convs.hasMore && !query}
      <button type="button" class="btn ghost more" onclick={() => void convs.loadMore()} disabled={convs.listLoading}>
        Mostra'n més
      </button>
    {/if}
  </nav>

  {#if app.providers.length}
    <section class="providers" aria-label="Proveïdors">
      {#each app.providers as provider (provider.agent)}
        <ProviderBadge {provider} spend={app.spend} />
      {/each}
    </section>
  {/if}

  <nav class="links" aria-label="Aplicació">
    <a href="#/tauler" class:current={isDashboard} aria-current={isDashboard ? 'page' : undefined}>
      <Icon name="dashboard" size={16} />Tauler
    </a>
    <button type="button" onclick={() => (app.settingsOpen = true)}>
      <Icon name="settings" size={16} />Configuració
    </button>
    <button type="button" onclick={() => void app.logout()}>
      <Icon name="logout" size={16} />Tancar sessió
    </button>
  </nav>
</aside>

<ConfirmDialog
  open={pendingDelete !== null}
  title="Eliminar la conversa?"
  message={pendingDelete
    ? `S'eliminarà «${pendingDelete.title}» amb tots els seus missatges. No es pot desfer.`
    : ''}
  onConfirm={() => {
    if (pendingDelete) void app.deleteConversation(pendingDelete.id);
  }}
  onClose={() => (pendingDelete = null)} />

<style>
  .sidebar {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    height: 100%;
    min-height: 0;
    padding: 0.9rem 0.7rem 0.7rem;
    border-width: 0 1px 0 0;
    border-radius: 0;
    box-shadow: none;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0 0.2rem 0 0.35rem;
  }

  .brand-name {
    flex: 1;
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.01em;
  }

  .brand-name b {
    background: linear-gradient(90deg, var(--claude-glow), var(--chatgpt-glow));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }

  .new {
    justify-content: flex-start;
    min-height: 2.4rem;
  }

  .search {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0 0.6rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    background: rgb(7 8 13 / 0.45);
    color: var(--text-muted);
  }

  .search:focus-within {
    border-color: var(--accent);
  }

  .search-input {
    flex: 1;
    min-width: 0;
    height: 2.1rem;
    border: none;
    outline: none;
    background: none;
    font-size: var(--text-sm);
  }

  .search-input::placeholder {
    color: var(--text-muted);
  }

  .list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    margin: 0 -0.3rem;
    padding: 0 0.3rem;
  }

  section + section {
    margin-top: 0.8rem;
  }

  h2 {
    margin: 0 0 0.25rem;
    padding: 0 0.55rem;
    font-size: 0.68rem;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  ul {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 1px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .none {
    padding: 1rem 0.55rem;
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .more {
    width: 100%;
    margin-top: 0.5rem;
  }

  .providers {
    display: grid;
    gap: 0.45rem;
  }

  .links {
    display: grid;
    gap: 1px;
    padding-top: 0.5rem;
    border-top: 1px solid var(--border);
  }

  .links a,
  .links button {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    width: 100%;
    padding: 0.45rem 0.55rem;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--text-secondary);
    font-size: var(--text-sm);
    text-align: left;
    text-decoration: none;
  }

  .links a:hover,
  .links button:hover,
  .links a.current {
    background: rgb(255 255 255 / 0.06);
    color: var(--text-primary);
  }
</style>
