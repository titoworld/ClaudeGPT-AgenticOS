<script lang="ts">
  // Two interlocking orbits (one per agent) around a shared core.
  interface Props {
    size?: number;
  }

  let { size = 28 }: Props = $props();
  const uid = $props.id();
</script>

<svg class="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="{uid}-g" x1="0" y1="0" x2="1" y2="1">
      <stop class="s1" offset="0" />
      <stop class="s2" offset="0.5" />
      <stop class="s3" offset="1" />
    </linearGradient>
  </defs>
  <ellipse class="orbit claude" cx="16" cy="16" rx="13" ry="6.2" transform="rotate(35 16 16)" />
  <ellipse class="orbit chatgpt" cx="16" cy="16" rx="13" ry="6.2" transform="rotate(-35 16 16)" />
  <circle cx="16" cy="16" r="4.2" fill="url(#{uid}-g)" />
</svg>

<style>
  .brand-mark {
    flex: none;
    filter: drop-shadow(0 0 6px rgb(179 90 214 / 0.55));
  }

  .orbit {
    fill: none;
    stroke-width: 2;
  }

  .claude {
    stroke: var(--claude);
  }

  .chatgpt {
    stroke: var(--chatgpt);
  }

  .s1 {
    stop-color: var(--claude-glow);
  }

  .s2 {
    stop-color: #c9a2ff;
  }

  .s3 {
    stop-color: var(--chatgpt-glow);
  }
</style>
