<script lang="ts">
  // Abstract agent glyphs (not vendor logos): a radiant burst for Claude and a
  // hexagonal knot for ChatGPT. Always shown next to the agent name.
  import type { Agent } from '../lib/protocol';

  interface Props {
    agent: Agent;
    size?: number;
  }

  let { agent, size = 18 }: Props = $props();
</script>

<svg
  class="agent-icon {agent}"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  aria-hidden="true"
  focusable="false">
  {#if agent === 'claude'}
    <path d="M12 3v5.2M12 15.8V21M3 12h5.2M15.8 12H21M5.6 5.6l3.7 3.7M14.7 14.7l3.7 3.7M18.4 5.6l-3.7 3.7M9.3 14.7l-3.7 3.7" />
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
  {:else}
    <path d="M12 3.2 19.6 7.6v8.8L12 20.8 4.4 16.4V7.6L12 3.2Z" stroke-linejoin="round" />
    <path d="M12 8.2 15.3 10.1v3.8L12 15.8l-3.3-1.9v-3.8L12 8.2Z" stroke-linejoin="round" />
  {/if}
</svg>

<style>
  .agent-icon {
    flex: none;
    display: block;
  }

  .claude {
    color: var(--claude-glow);
  }

  .chatgpt {
    color: var(--chatgpt-glow);
  }
</style>
