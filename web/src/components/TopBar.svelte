<script lang="ts">
  import { app } from '../lib/app.svelte';
  import { i18n } from '../lib/i18n/index.svelte';
  import { prefs } from '../lib/prefs.svelte';
  import { router } from '../lib/router.svelte';
  import { MODE_LABEL } from '../lib/text';
  import ConnectionIndicator from './ConnectionIndicator.svelte';
  import Icon from './Icon.svelte';

  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent);
  const t = $derived(i18n.m.app);
  const title = $derived(router.route.name === 'dashboard' ? t.dashboard : app.convs.currentTitle);
  /** A new conversation, not yet sent: the page is just the app's name. */
  const fresh = $derived(router.route.name !== 'dashboard' && app.convs.currentId == null);
  const lastMode = $derived.by(() => {
    const id = app.convs.currentId;
    if (router.route.name !== 'chat' || id == null) return null;
    // Opened from a search, it may not be in the pages loaded: its own details say.
    const current = app.convs.list.find((c) => c.id === id) ?? (app.convs.detail?.id === id ? app.convs.detail : null);
    return current?.last_mode ?? null;
  });
  const showMenu = $derived(prefs.narrow || prefs.sidebarCollapsed);
  let menuButton: HTMLButtonElement | undefined = $state();

  function toggleSidebar(): void {
    if (prefs.narrow) app.sidebarOpen = true;
    else prefs.setSidebarCollapsed(false);
  }

  /** The drawer closed: the focus goes back to the button that opened it. */
  export function focusMenu(): void {
    menuButton?.focus();
  }
</script>

<svelte:head>
  <title>{fresh ? 'ClaudeGPT OS' : `${title} · ClaudeGPT OS`}</title>
</svelte:head>

<header class="topbar">
  {#if showMenu}
    <button
      bind:this={menuButton}
      type="button"
      class="icon-btn"
      onclick={toggleSidebar}
      aria-label={prefs.narrow ? t.topBar.openMenu : t.topBar.showSidebar}>
      <Icon name={prefs.narrow ? 'menu' : 'sidebar'} />
    </button>
  {/if}
  <h1 class="title">
    <span class="text">{title}</span>
    {#if lastMode}<span class="chip"><Icon name="mode-{lastMode}" size={12} />{MODE_LABEL[lastMode]}</span>{/if}
  </h1>
  <div class="right">
    <ConnectionIndicator />
    <button type="button" class="palette-btn" onclick={() => (app.paletteOpen = true)} aria-keyshortcuts={isMac ? 'Meta+K' : 'Control+K'}>
      <Icon name="search" size={15} />
      <span class="palette-text">{t.topBar.palette}</span>
      <kbd>{isMac ? '⌘' : 'Ctrl'} K</kbd>
    </button>
  </div>
</header>

<style>
  .topbar {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    min-height: 3.5rem;
    padding: 0.55rem clamp(0.6rem, 2vw, 1.2rem);
    border-bottom: 1px solid var(--border);
    background: linear-gradient(rgb(7 8 13 / 0.55), rgb(7 8 13 / 0.2));
    -webkit-backdrop-filter: blur(12px);
    backdrop-filter: blur(12px);
  }

  .title {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex: 1;
    min-width: 0;
    font-size: var(--text-md);
    font-weight: 650;
  }

  .text {
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .right {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .palette-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    min-height: 1.9rem;
    padding: 0.2rem 0.4rem 0.2rem 0.65rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: rgb(7 8 13 / 0.4);
    color: var(--text-muted);
    font-size: var(--text-xs);
    transition:
      border-color var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }

  .palette-btn:hover {
    border-color: var(--border-strong);
    color: var(--text-primary);
  }

  @media (max-width: 640px) {
    .palette-text,
    .palette-btn kbd {
      display: none;
    }

    .palette-btn {
      padding: 0.2rem 0.55rem;
    }

    .title .chip {
      display: none;
    }
  }
</style>
