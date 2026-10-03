<script lang="ts">
  // How ChatGPT read the question's PDFs when it cannot open them (the subscription): a
  // badge on each of its messages, from `meta.pdf_reading` (live, from its
  // stream.completed), so that an agreement with Claude on the pages ChatGPT read through
  // Claude is not taken for two independent readings (docs/adr/0009-attachments.md). Its
  // tooltip lists, per PDF, the pages Claude read, the hidden ones and the unchecked ones;
  // a tap shows it too (phones have no hover), and screen readers get it as the badge's
  // description.
  import { readingGroups } from '../lib/pdf-pages';
  import type { PdfReading } from '../lib/protocol';
  import Icon from './Icon.svelte';
  import PlainText from './PlainText.svelte';

  interface Props {
    readings: PdfReading[];
  }

  let { readings }: Props = $props();

  const groups = $derived(readingGroups(readings));
  const uid = $props.id();
  /** The badge a tap opened: its PDFs were checked (true) or not (false); null: none. */
  let open: boolean | null = $state(null);
</script>

<span class="pdf-reading">
  {#each groups as group (group.checked)}
    {@const id = `${uid}-${group.checked ? 'checked' : 'unchecked'}`}
    <span class="wrap" class:open={open === group.checked}>
      <button
        type="button"
        class="chip {group.checked ? 'claude' : 'warn'}"
        aria-describedby={id}
        aria-expanded={open === group.checked}
        onclick={() => (open = open === group.checked ? null : group.checked)}
        onblur={() => (open = null)}>
        <Icon name={group.checked ? 'file-text' : 'alert'} size={12} />{group.label}
      </button>
      <span class="tip" role="tooltip" {id}>
        <!-- The spaces between the parts keep them apart as text (the description screen
             readers get); the grid ignores them. -->
        <strong>{group.lead}</strong>
        {#each group.pdfs as pdf (pdf.attachmentId)}
          {' '}<span class="pdf">
            <span class="name"><PlainText text={pdf.name} /></span>
            {#each pdf.rows as row (row.label)}
              {' '}<span class="row">{row.label} <b>{row.pages}</b></span>
            {/each}
            {#if pdf.note}{' '}<small>{pdf.note}</small>{/if}
          </span>
        {/each}
      </span>
    </span>
  {/each}
</span>

<style>
  .pdf-reading {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }

  .wrap {
    position: relative;
    display: inline-flex;
  }

  button.chip {
    cursor: help;
    font: inherit;
    font-size: var(--text-xs);
    font-weight: 650;
  }

  /* Claude read (part of) it for ChatGPT. */
  .chip.claude {
    border-color: rgb(221 107 59 / 0.45);
    background: var(--claude-soft);
    color: #ffc2a6;
  }

  .tip {
    position: absolute;
    bottom: calc(100% + 8px);
    left: 0;
    z-index: 20;
    display: grid;
    gap: 0.45rem;
    width: max-content;
    max-width: min(20rem, calc(100vw - 2.5rem));
    padding: 0.75rem 0.85rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-strong);
    background: rgb(19 22 35 / 0.97);
    box-shadow: var(--shadow);
    font-size: var(--text-xs);
    font-weight: 400;
    color: var(--text-secondary);
    white-space: normal;
    text-align: left;
    opacity: 0;
    visibility: hidden;
    transform: translateY(4px);
    transition:
      opacity var(--dur-fast) var(--ease-out),
      transform var(--dur-fast) var(--ease-out),
      visibility var(--dur-fast);
    pointer-events: none;
  }

  .wrap:hover .tip,
  .wrap:focus-within .tip,
  .open .tip {
    opacity: 1;
    visibility: visible;
    transform: none;
  }

  strong {
    color: var(--text-primary);
    font-weight: 650;
  }

  .pdf {
    display: grid;
    gap: 0.2rem;
    padding-top: 0.4rem;
    border-top: 1px solid var(--border);
  }

  .name {
    color: var(--text-primary);
    font-weight: 600;
    overflow-wrap: anywhere;
  }

  .row {
    display: flex;
    gap: 0.75rem;
  }

  .row b {
    margin-left: auto;
    color: var(--text-primary);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  small {
    color: var(--text-muted);
    font-size: inherit;
  }
</style>
