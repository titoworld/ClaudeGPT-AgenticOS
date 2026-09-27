<script lang="ts">
  import type { ConversationSummary } from '../lib/protocol';
  import { routeHash } from '../lib/router.svelte';
  import { MODE_LABEL } from '../lib/text';
  import Icon from './Icon.svelte';

  interface Props {
    conv: ConversationSummary;
    active: boolean;
    running: boolean;
    onRename: (title: string) => Promise<boolean>;
    onDelete: () => void;
  }

  let { conv, active, running, onRename, onDelete }: Props = $props();

  let editing = $state(false);
  let draft = $state('');
  let saving = false;

  function startEdit(): void {
    draft = conv.title;
    editing = true;
  }

  async function save(): Promise<void> {
    if (saving) return;
    const title = draft.trim();
    if (!title || title === conv.title) {
      editing = false;
      return;
    }
    saving = true;
    const ok = await onRename(title);
    saving = false;
    if (ok) editing = false;
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.key === 'Enter') {
      e.preventDefault();
      void save();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      editing = false;
    }
  }

  const focusSelect = (el: HTMLInputElement) => {
    el.focus();
    el.select();
  };
</script>

<li class="item" class:active>
  {#if editing}
    <input
      class="rename"
      bind:value={draft}
      onkeydown={onKeydown}
      onblur={() => void save()}
      maxlength="200"
      aria-label="Nou nom de la conversa"
      {@attach focusSelect} />
  {:else}
    <a href={routeHash({ name: 'chat', id: conv.id })} aria-current={active ? 'page' : undefined} ondblclick={startEdit}>
      <span class="mode" title={conv.last_mode ? MODE_LABEL[conv.last_mode] : ''}>
        <Icon name={conv.last_mode ? `mode-${conv.last_mode}` : 'mode-solo'} size={15} />
      </span>
      <span class="title">{conv.title || 'Sense títol'}</span>
      {#if running}<span class="running" title="Torn en curs"><span class="sr-only">Torn en curs</span></span>{/if}
    </a>
    <div class="actions">
      <button type="button" class="icon-btn small" onclick={startEdit} aria-label="Canvia el nom de «{conv.title}»">
        <Icon name="edit" size={14} />
      </button>
      <button type="button" class="icon-btn small danger" onclick={onDelete} aria-label="Elimina «{conv.title}»">
        <Icon name="trash" size={14} />
      </button>
    </div>
  {/if}
</li>

<style>
  .item {
    position: relative;
    display: flex;
    min-width: 0;
    align-items: center;
    border-radius: var(--radius-sm);
    transition: background var(--dur-fast) var(--ease-out);
  }

  .item:hover,
  .item:focus-within {
    background: rgb(255 255 255 / 0.05);
  }

  .item.active {
    background: rgb(139 156 255 / 0.14);
    box-shadow: inset 2px 0 0 var(--accent);
  }

  a {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    flex: 1;
    min-width: 0;
    padding: 0.45rem 0.55rem;
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    text-decoration: none;
    font-size: var(--text-sm);
  }

  .active a,
  a:hover {
    color: var(--text-primary);
  }

  .mode {
    color: var(--text-muted);
  }

  .active .mode {
    color: var(--accent);
  }

  .title {
    flex: 1;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .running {
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent);
    animation: pulse-dot 1.2s ease-in-out infinite;
  }

  .actions {
    display: none;
    gap: 0.1rem;
    padding-right: 0.25rem;
  }

  .item:hover .actions,
  .item:focus-within .actions {
    display: flex;
  }

  .danger:hover {
    color: #ff9aa4;
  }

  .rename {
    width: 100%;
    margin: 0.15rem;
    padding: 0.35rem 0.5rem;
    border-radius: 6px;
    border: 1px solid var(--accent);
    background: rgb(7 8 13 / 0.7);
    font-size: var(--text-sm);
    outline: none;
  }

  @media (hover: none) {
    .actions {
      display: flex;
    }
  }
</style>
