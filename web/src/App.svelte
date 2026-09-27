<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import Icon from './components/Icon.svelte';
  import SceneBackdrop from './components/SceneBackdrop.svelte';
  import Toasts from './components/Toasts.svelte';
  import { app } from './lib/app.svelte';
  import { prefs } from './lib/prefs.svelte';
  import { router } from './lib/router.svelte';
  import Login from './views/Login.svelte';

  // The logged-in shell (markdown, conversation UI) is its own chunk. Start
  // fetching it right away, in parallel with the auth check, so the login
  // screen paints without it and the app does not wait for it afterwards.
  const shell = import('./views/Shell.svelte');

  onMount(() => {
    void app.init();
  });

  // Route changes (links, back button, deep links) drive the open conversation.
  // Only the route and the auth phase trigger this; whatever syncRoute reads
  // internally must not (it would re-run on unrelated state changes).
  $effect(() => {
    const route = router.route;
    const ready = app.auth === 'ready';
    untrack(() => {
      if (ready) void app.syncRoute(route);
    });
  });

  // Effects level for CSS (lighter glass when effects are low/off).
  $effect(() => {
    document.documentElement.dataset.effects = prefs.effects;
  });

  function onKeydown(e: KeyboardEvent): void {
    if (app.auth !== 'ready') return;
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      app.paletteOpen = !app.paletteOpen;
    }
  }
</script>

<svelte:window onkeydown={onKeydown} />

<SceneBackdrop />

{#if app.auth === 'checking'}
  <div class="splash" aria-live="polite">
    <span class="spinner" aria-hidden="true"></span>
    <span class="sr-only">Carregant…</span>
  </div>
{:else if app.auth === 'ready'}
  {#await shell}
    <div class="splash"><span class="spinner" aria-hidden="true"></span></div>
  {:then mod}
    {@const Shell = mod.default}
    <Shell />
  {:catch}
    <div class="splash" role="alert">
      <p>No s'ha pogut carregar l'aplicació. <button type="button" class="btn" onclick={() => location.reload()}>Torna a carregar</button></p>
    </div>
  {/await}
{:else}
  <Login />
{/if}

{#if app.fatal}
  <div class="fatal" role="alertdialog" aria-modal="true" aria-labelledby="fatal-title" aria-describedby="fatal-text">
    <div class="fatal-card glass">
      <h2 id="fatal-title"><Icon name="alert" size={20} />Connexió rebutjada</h2>
      <p id="fatal-text">{app.fatal}</p>
      <p class="hint">Revisa <code>AOS_ALLOWED_ORIGINS</code> al servidor i torna a carregar la pàgina.</p>
      <button type="button" class="btn primary" onclick={() => location.reload()}>Torna a carregar</button>
    </div>
  </div>
{/if}

<Toasts />

<style>
  .splash {
    position: relative;
    z-index: 1;
    display: grid;
    place-items: center;
    height: 100%;
  }

  .spinner {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 3px solid rgb(255 255 255 / 0.12);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }

  .fatal {
    position: fixed;
    inset: 0;
    z-index: 200;
    display: grid;
    place-items: center;
    padding: 1rem;
    background: rgb(4 5 9 / 0.7);
  }

  .fatal-card {
    display: grid;
    gap: 0.8rem;
    max-width: 30rem;
    padding: 1.5rem;
    border-radius: var(--radius-lg);
    border-color: rgb(255 93 108 / 0.45);
  }

  .fatal-card h2 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: var(--text-lg);
    color: #ffc9ce;
  }

  .fatal-card p {
    color: var(--text-secondary);
  }

  code {
    font-family: var(--font-mono);
    font-size: 0.85em;
  }
</style>
