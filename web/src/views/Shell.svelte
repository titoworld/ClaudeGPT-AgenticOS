<script lang="ts">
  import { tick, untrack } from 'svelte';
  import AttachmentViewer from '../components/AttachmentViewer.svelte';
  import CommandPalette from '../components/CommandPalette.svelte';
  import SettingsDrawer from '../components/SettingsDrawer.svelte';
  import Sidebar from '../components/Sidebar.svelte';
  import TopBar from '../components/TopBar.svelte';
  import { app } from '../lib/app.svelte';
  import { i18n } from '../lib/i18n/index.svelte';
  import { prefs } from '../lib/prefs.svelte';
  import { router } from '../lib/router.svelte';
  import Chat from './Chat.svelte';

  // The dashboard (and its charts) is a separate chunk, loaded on first visit.
  let dashboard: ReturnType<typeof loadDashboard> | null = null;
  function loadDashboard() {
    return import('./Dashboard.svelte');
  }
  function getDashboard() {
    dashboard ??= loadDashboard();
    return dashboard;
  }

  const t = $derived(i18n.m.app);
  const collapsed = $derived(!prefs.narrow && prefs.sidebarCollapsed);
  const drawerOpen = $derived(prefs.narrow && app.sidebarOpen);

  let side: HTMLDivElement | undefined = $state();
  let topBar: ReturnType<typeof TopBar> | undefined = $state();

  // On phones the drawer is modal (N21): while it is open the rest of the page is inert
  // (keyboard and screen readers stay in the menu), the focus goes into it, Escape
  // closes it, and closing it gives the focus back to the menu button.
  let wasOpen = false;
  /** `app.focusComposerTick` when the drawer opened: a request made meanwhile could not be met. */
  let composerTick = 0;
  $effect(() => {
    const open = drawerOpen;
    untrack(() => {
      if (open === wasOpen) return;
      wasOpen = open;
      if (open) {
        composerTick = app.focusComposerTick;
        void tick().then(() => side?.querySelector<HTMLElement>('button, [href], input')?.focus());
      } else if (prefs.narrow) {
        void drawerClosed(app.focusComposerTick !== composerTick);
      }
    });
  });

  async function drawerClosed(composerAsked: boolean): Promise<void> {
    // E.g. «New conversation» asked for the composer while the page behind was inert.
    if (composerAsked) app.focusComposer();
    await tick();
    const active = document.activeElement;
    // Something outside the drawer has the focus already (the composer): leave it there.
    if (active && active !== document.body && !side?.contains(active)) return;
    topBar?.focusMenu();
  }

  function onKeydown(e: KeyboardEvent): void {
    if (!drawerOpen || e.key !== 'Escape' || e.defaultPrevented) return;
    // A dialog over the drawer (the palette, a confirmation) closes on its own.
    if (e.target instanceof Element && e.target.closest('dialog')) return;
    e.preventDefault();
    app.sidebarOpen = false;
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="shell" class:collapsed class:narrow={prefs.narrow} class:drawer-open={drawerOpen}>
  <div
    bind:this={side}
    class="side"
    inert={collapsed || (prefs.narrow && !app.sidebarOpen)}
    role={drawerOpen ? 'dialog' : undefined}
    aria-modal={drawerOpen ? 'true' : undefined}
    aria-label={drawerOpen ? t.shell.menu : undefined}>
    <Sidebar />
  </div>
  {#if drawerOpen}
    <!-- Pointer only: Escape and the drawer's own button close it from the keyboard. -->
    <button type="button" class="backdrop" tabindex="-1" aria-label={t.closeMenu} onclick={() => (app.sidebarOpen = false)}></button>
  {/if}

  <main class="main" inert={drawerOpen}>
    <TopBar bind:this={topBar} />
    <div class="view">
      {#if router.route.name === 'dashboard'}
        {#await getDashboard()}
          <div class="pending" aria-live="polite"><span class="spinner" aria-hidden="true"></span>{t.shell.dashboardLoading}</div>
        {:then mod}
          {@const Dashboard = mod.default}
          <div class="dashboard-wrap"><Dashboard /></div>
        {:catch}
          <div class="pending" role="alert">
            {t.shell.dashboardFailed}
            <button
              type="button"
              class="btn"
              onclick={() => {
                dashboard = null;
                router.go({ name: 'chat', id: app.convs.currentId });
              }}>{t.shell.backToConversations}</button>
          </div>
        {/await}
      {:else}
        <Chat />
      {/if}
    </div>
  </main>
</div>

<CommandPalette />
<SettingsDrawer />
<AttachmentViewer />

<style>
  .shell {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: 17.5rem minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
    height: 100%;
    transition: grid-template-columns var(--dur-med) var(--ease-out);
  }

  .shell.collapsed {
    grid-template-columns: 0 minmax(0, 1fr);
  }

  .side {
    min-width: 0;
    height: 100%;
    overflow: hidden;
  }

  .collapsed .side {
    visibility: hidden;
  }

  .main {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1fr);
    min-width: 0;
    min-height: 0;
    height: 100%;
    overflow: hidden;
  }

  .view {
    min-height: 0;
    overflow: hidden;
  }

  .dashboard-wrap {
    height: 100%;
    overflow-y: auto;
  }

  .pending {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.7rem;
    height: 100%;
    color: var(--text-muted);
  }

  .spinner {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.15);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }

  /* Phones and small tablets: the sidebar becomes an off-canvas drawer. */
  .shell.narrow {
    grid-template-columns: minmax(0, 1fr);
  }

  .narrow .side {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 40;
    width: min(20rem, 86vw);
    transform: translateX(-102%);
    transition: transform var(--dur-med) var(--ease-out);
  }

  .narrow.drawer-open .side {
    transform: none;
  }

  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 30;
    border: none;
    background: rgb(4 5 9 / 0.55);
    -webkit-backdrop-filter: blur(3px);
    backdrop-filter: blur(3px);
    cursor: default;
  }
</style>
