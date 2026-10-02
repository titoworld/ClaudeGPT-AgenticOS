<script lang="ts">
  // Model text shown as plain text, outside Markdown (the note of an unchanged
  // revision), with its hidden characters revealed as ⟨U+XXXX⟩ marks, as they are
  // in a rendered answer (lib/hidden-chars.ts). Built from text nodes: no HTML.
  import { revealHiddenParts } from '../lib/hidden-chars';

  interface Props {
    text: string;
  }

  let { text }: Props = $props();

  const parts = $derived(revealHiddenParts(text));
</script>

<span class="plain-text">{#each parts as part}{#if typeof part === 'string'}{part}{:else}<span class="invisible-char" class:invisible-collapsed={part.collapsed} title={part.title}>{part.text}</span>{/if}{/each}</span>
