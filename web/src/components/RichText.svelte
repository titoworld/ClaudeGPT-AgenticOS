<script lang="ts" module>
  export type RichPart = { kind: 'text' | 'bold' | 'code'; text: string };

  /** `**bold**` and `` `code` `` parts of a text; everything else is plain text. */
  export function richParts(text: string): RichPart[] {
    const parts: RichPart[] = [];
    let last = 0;
    for (const match of text.matchAll(/\*\*([^*]+)\*\*|`([^`]+)`/g)) {
      if (match.index > last) parts.push({ kind: 'text', text: text.slice(last, match.index) });
      parts.push(match[1] !== undefined ? { kind: 'bold', text: match[1] } : { kind: 'code', text: match[2]! });
      last = match.index + match[0].length;
    }
    if (last < text.length) parts.push({ kind: 'text', text: text.slice(last) });
    return parts;
  }
</script>

<script lang="ts">
  // A text of the catalogs (lib/i18n) with **bold** and `code` parts, as elements: a
  // translation moves its markup with its words, and no HTML is ever parsed.
  interface Props {
    text: string;
  }

  let { text }: Props = $props();
  const parts = $derived(richParts(text));
</script>

{#each parts as part, i (i)}{#if part.kind === 'bold'}<b>{part.text}</b>{:else if part.kind === 'code'}<code>{part.text}</code>{:else}{part.text}{/if}{/each}
