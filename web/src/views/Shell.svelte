<script lang="ts">
  import CommandPalette from '../components/CommandPalette.svelte';
  import SettingsDrawer from '../components/SettingsDrawer.svelte';
  import Sidebar from '../components/Sidebar.svelte';
  import TopBar from '../components/TopBar.svelte';
  import { app } from '../lib/app.svelte';
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

  const collapsed = $derived(!prefs.narrow && prefs.sidebarCollapsed);
  const drawerOpen = $derived(prefs.narrow && app.sidebarOpen);
</script>

<div class="shell" class:collapsed class:narrow={prefs.narrow} class:drawer-open={drawerOpen}>
  <div class="side" inert={collapsed || (prefs.narrow && !app.sidebarOpen)}>
    <Sidebar />
  </div>
  {#if drawerOpen}
    <button type="button" class="backdrop" aria-label="Tanca el menú" onclick={() => (app.sidebarOpen = false)}></button>
  {/if}

  <main class="main">
    <TopBar />
    <div class="view">
      {#if router.route.name === 'dashboard'}
        {#await getDashboard()}
          <div class="pending" aria-live="polite"><span class="spinner" aria-hidden="true"></span>Carregant el tauler…</div>
        {:then mod}
          {@const Dashboard = mod.default}
          <div class="dashboard-wrap"><Dashboard /></div>
        {:catch}
          <div class="pending" role="alert">
            No s'ha pogut carregar el tauler.
            <button
              type="button"
              class="btn"
              onclick={() => {
                dashboard = null;
                router.go({ name: 'chat', id: app.convs.currentId });
              }}>Torna a les converses</button>
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
