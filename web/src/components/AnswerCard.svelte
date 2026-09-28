<script lang="ts">
  import type { Snippet } from 'svelte';
  import { answerForClipboard } from '../lib/hidden-chars';
  import type { Agent } from '../lib/protocol';
  import type { StreamView } from '../lib/turns.svelte';
  import AgentLabel from './AgentLabel.svelte';
  import AnswerMeta from './AnswerMeta.svelte';
  import CopyButton from './CopyButton.svelte';
  import Icon from './Icon.svelte';
  import Markdown from './Markdown.svelte';
  import StreamStatus from './StreamStatus.svelte';

  interface Props {
    agent: Agent;
    /** null while the model has not started yet. */
    stream: StreamView | null;
    /** Whether the turn is still running (a null stream means "waiting"). */
    active: boolean;
    variant?: 'answer' | 'synthesis';
    title?: string;
    badge?: Snippet;
  }

  let { agent, stream, active, variant = 'answer', title, badge }: Props = $props();

  const status = $derived(stream ? stream.status : active ? 'waiting' : null);
  const streaming = $derived(stream?.status === 'streaming');
</script>

<article class="card {agent} {variant}" class:live={streaming || (!stream && active)} aria-busy={streaming}>
  <header>
    <div class="who">
      {#if variant === 'synthesis'}
        <span class="synth-title"><Icon name="sparkles" size={16} />{title ?? 'Síntesi'}</span>
        <span class="by">per <AgentLabel {agent} size={15} /></span>
      {:else}
        <AgentLabel {agent} />
        {#if title}<span class="subtitle">{title}</span>{/if}
      {/if}
    </div>
    <div class="side">
      {#if badge}{@render badge()}{/if}
      {#if status}<StreamStatus {status} />{/if}
      {#if stream?.status === 'done' && stream.text}
        <CopyButton text={stream.text} prepare={answerForClipboard} />
      {/if}
    </div>
  </header>

  <div class="body">
    {#if stream?.text}
      <Markdown text={stream.text} {streaming} />
    {:else if status === 'waiting' || streaming}
      <div class="skeleton" aria-hidden="true"><i></i><i></i><i></i></div>
    {:else if !stream}
      <p class="empty">No ha respost.</p>
    {/if}

    {#if stream?.status === 'failed'}
      <div class="problem" role="alert">
        <Icon name="alert" size={16} />
        <span><strong>No ha pogut respondre.</strong> {stream.error?.message ?? ''}</span>
      </div>
    {:else if stream?.status === 'interrupted'}
      <p class="note">Resposta interrompuda.</p>
    {/if}
  </div>

  {#if stream?.status === 'done'}
    <AnswerMeta {stream} />
  {/if}
</article>

<style>
  .card {
    --accent-color: var(--claude);
    --caret: var(--claude-glow);
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.75rem;
    align-content: start;
    min-width: 0;
    padding: 1rem 1.1rem 0.9rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: rgb(15 17 27 / 0.8);
    box-shadow: 0 8px 30px -12px rgb(0 0 0 / 0.6);
    animation: rise-in var(--dur-med) var(--ease-out);
    transition:
      box-shadow var(--dur-med) var(--ease-out),
      border-color var(--dur-med) var(--ease-out);
  }

  .card::before {
    content: '';
    position: absolute;
    inset: 0 auto 0 0;
    width: 3px;
    border-radius: var(--radius-md) 0 0 var(--radius-md);
    background: var(--accent-color);
    opacity: 0.85;
  }

  .chatgpt {
    --accent-color: var(--chatgpt);
    --caret: var(--chatgpt-glow);
  }

  .live {
    border-color: color-mix(in srgb, var(--accent-color) 45%, transparent);
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--accent-color) 20%, transparent),
      0 10px 40px -12px color-mix(in srgb, var(--accent-color) 55%, transparent);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    min-height: 1.75rem;
  }

  .who {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.35rem 0.6rem;
    min-width: 0;
  }

  .subtitle {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .side {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .body {
    min-width: 0;
  }

  .skeleton {
    display: grid;
    gap: 0.55rem;
    padding: 0.2rem 0;
  }

  .skeleton i {
    height: 0.7rem;
    border-radius: 6px;
    background: linear-gradient(
      90deg,
      rgb(255 255 255 / 0.04) 20%,
      color-mix(in srgb, var(--accent-color) 22%, transparent) 50%,
      rgb(255 255 255 / 0.04) 80%
    );
    background-size: 200% 100%;
    animation: shimmer 1.6s linear infinite;
  }

  .skeleton i:nth-child(2) {
    width: 88%;
  }

  .skeleton i:nth-child(3) {
    width: 62%;
  }

  .empty,
  .note {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .note {
    margin-top: 0.5rem;
  }

  .problem {
    display: flex;
    gap: 0.5rem;
    align-items: flex-start;
    margin-top: 0.5rem;
    padding: 0.6rem 0.75rem;
    border-radius: var(--radius-sm);
    border: 1px solid rgb(255 93 108 / 0.35);
    background: rgb(255 93 108 / 0.08);
    color: #ffc9ce;
    font-size: var(--text-sm);
  }

  /* Synthesis: prominent card with a border mixing both agent colors. */
  .synthesis {
    --accent-color: var(--accent);
    --caret: #c9b8ff;
    padding: 1.2rem 1.3rem 1rem;
    border: 1px solid transparent;
    background:
      linear-gradient(rgb(16 18 30 / 0.92), rgb(16 18 30 / 0.92)) padding-box,
      conic-gradient(
          from var(--angle, 0deg),
          var(--claude),
          #b35ad6,
          var(--chatgpt),
          #3987e5,
          var(--claude)
        )
        border-box;
    box-shadow:
      0 0 40px -10px rgb(221 107 59 / 0.35),
      0 0 60px -12px rgb(26 163 131 / 0.35);
    animation:
      rise-in var(--dur-med) var(--ease-out),
      border-spin 8s linear infinite;
  }

  .synthesis::before {
    display: none;
  }

  .synthesis.live {
    animation:
      rise-in var(--dur-med) var(--ease-out),
      border-spin 2.5s linear infinite;
  }

  .synth-title {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-weight: 700;
    font-size: var(--text-lg);
    background: linear-gradient(90deg, #ffb38f, #d6a5ff, #7ff0d0);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }

  .synth-title :global(.icon) {
    color: #d6a5ff;
  }

  .by {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  @property --angle {
    syntax: '<angle>';
    initial-value: 0deg;
    inherits: false;
  }

  @keyframes border-spin {
    to {
      --angle: 360deg;
    }
  }
</style>
