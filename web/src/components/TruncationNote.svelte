<script lang="ts">
  // Says that a reply was cut off before the end, and why. By default it speaks of
  // the answer above it: a usable partial answer, never a complete one.
  import { truncationReason } from '../lib/text';
  import Icon from './Icon.svelte';

  interface Props {
    /** `finish_reason` of the stored message (null live: the event does not carry it). */
    reason: string | null;
    /** What is incomplete, before the reason. */
    lead?: string;
    /** A sentence after the reason (what is shown instead). */
    detail?: string | null;
    /** Smaller, for the columns of a revision round. */
    compact?: boolean;
  }

  let { reason, lead = 'Resposta incompleta', detail = null, compact = false }: Props = $props();
</script>

<p class="truncation-note" class:compact role="note">
  <Icon name="alert" size={compact ? 14 : 16} />
  <span><strong>{lead}:</strong> {truncationReason(reason)}.{#if detail}{' '}{detail}{/if}</span>
</p>

<style>
  .truncation-note {
    display: flex;
    gap: 0.5rem;
    align-items: flex-start;
    margin-top: 0.5rem;
    padding: 0.6rem 0.75rem;
    border-radius: var(--radius-sm);
    border: 1px solid color-mix(in srgb, var(--warning) 35%, transparent);
    background: color-mix(in srgb, var(--warning) 8%, transparent);
    color: #ffd99a;
    font-size: var(--text-sm);
    overflow-wrap: anywhere;
  }

  .truncation-note :global(.icon) {
    flex: none;
    margin-top: 0.1rem;
  }

  .compact {
    margin-top: 0;
    padding: 0.45rem 0.6rem;
    font-size: var(--text-xs);
  }
</style>
