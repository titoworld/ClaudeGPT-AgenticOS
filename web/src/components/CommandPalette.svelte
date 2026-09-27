<script lang="ts">
  import { tick } from 'svelte';
  import { app } from '../lib/app.svelte';
  import { lightDismiss, syncDialog } from '../lib/dialog';
  import type { TurnMode } from '../lib/protocol';
  import { router } from '../lib/router.svelte';
  import { fuzzyFilter, MODE_LABEL } from '../lib/text';
  import Icon, { type IconName } from './Icon.svelte';

  interface Command {
    id: string;
    label: string;
    group: 'Accions' | 'Converses';
    icon: IconName;
    keywords?: string;
    hint?: string;
    run: () => void;
  }

  const uid = $props.id();
  let dialog: HTMLDialogElement | undefined = $state();
  let input: HTMLInputElement | undefined = $state();
  let query = $state('');
  let activeIndex = $state(0);

  function setMode(mode: TurnMode): void {
    app.composer.mode = mode;
    if (router.route.name !== 'chat') router.go({ name: 'chat', id: app.convs.currentId });
    app.focusComposer();
  }

  const actions: Command[] = [
    { id: 'new', label: 'Nova conversa', group: 'Accions', icon: 'plus', run: () => app.newConversation() },
    ...(['solo', 'duel', 'debate'] as const).map(
      (mode): Command => ({
        id: `mode-${mode}`,
        label: `Canvia al mode ${MODE_LABEL[mode]}`,
        group: 'Accions',
        icon: `mode-${mode}`,
        keywords: 'mode canviar',
        run: () => setMode(mode),
      }),
    ),
    {
      id: 'dashboard',
      label: 'Obre el tauler',
      group: 'Accions',
      icon: 'dashboard',
      keywords: 'estadistiques consum tokens estalvi',
      run: () => router.go({ name: 'dashboard' }),
    },
    {
      id: 'settings',
      label: 'Configuració',
      group: 'Accions',
      icon: 'settings',
      keywords: 'preferencies opcions efectes',
      run: () => (app.settingsOpen = true),
    },
    { id: 'logout', label: 'Tanca la sessió', group: 'Accions', icon: 'logout', keywords: 'sortir', run: () => void app.logout() },
  ];

  const results: Command[] = $derived.by(() => {
    const cmds = fuzzyFilter(actions, query, (c) => `${c.label} ${c.keywords ?? ''}`);
    const convs = fuzzyFilter(app.convs.list, query, (c) => c.title)
      .slice(0, query ? 12 : 6)
      .map(
        (c): Command => ({
          id: `conv-${c.id}`,
          label: c.title || 'Sense títol',
          group: 'Converses',
          icon: c.last_mode ? `mode-${c.last_mode}` : 'mode-solo',
          run: () => app.openConversation(c.id),
        }),
      );
    return [...cmds, ...convs];
  });

  $effect(() => syncDialog(dialog, app.paletteOpen));

  $effect(() => {
    if (!app.paletteOpen) return;
    query = '';
    activeIndex = 0;
    void tick().then(() => input?.focus());
  });

  $effect(() => {
    void query;
    activeIndex = 0;
  });

  function run(cmd: Command | undefined): void {
    if (!cmd) return;
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
  aria-label="Paleta d'ordres"
  onclose={() => (app.paletteOpen = false)}
  {@attach lightDismiss}>
  <div class="panel glass">
    <div class="search">
      <Icon name="search" size={18} />
      <input
        bind:this={input}
        bind:value={query}
        onkeydown={onKeydown}
        type="text"
        role="combobox"
        aria-expanded="true"
        aria-controls="{uid}-list"
        aria-activedescendant={results.length ? `${uid}-opt-${activeIndex}` : undefined}
        aria-autocomplete="list"
        placeholder="Escriu una ordre o cerca una conversa…"
        autocomplete="off"
        spellcheck="false" />
      <kbd>Esc</kbd>
    </div>

    <ul id="{uid}-list" role="listbox" aria-label="Resultats">
      {#each results as cmd, i (cmd.id)}
        {#if i === 0 || results[i - 1]?.group !== cmd.group}
          <li class="group" role="presentation">{cmd.group}</li>
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
      {:else}
        <li class="none" role="presentation">Cap resultat per a «{query}».</li>
      {/each}
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
