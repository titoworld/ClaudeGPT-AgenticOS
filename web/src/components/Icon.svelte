<script lang="ts" module>
  // Static, trusted SVG fragments (24x24, stroke = currentColor).
  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    edit: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    dashboard: '<path d="M4 20V11M10 20V4M16 20v-6M22 20H2"/>',
    settings:
      '<circle cx="12" cy="12" r="7" stroke-width="3.2" stroke-dasharray="2.75 2.75"/><circle cx="12" cy="12" r="5.2"/><circle cx="12" cy="12" r="1.8"/>',
    logout: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 16l-4-4 4-4M6 12h10"/>',
    send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.2"/><path d="M15.5 8.5V6.2A1.7 1.7 0 0 0 13.8 4.5H6.2a1.7 1.7 0 0 0-1.7 1.7v7.6a1.7 1.7 0 0 0 1.7 1.7h2.3"/>',
    'chevron-down': '<path d="m6 9 6 6 6-6"/>',
    'chevron-right': '<path d="m9 6 6 6-6 6"/>',
    'arrow-down': '<path d="M12 5v14M6 13l6 6 6-6"/>',
    sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    cache: '<ellipse cx="12" cy="6" rx="7" ry="2.8"/><path d="M5 6v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-6"/>',
    bolt: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6L13 3Z"/>',
    command:
      '<path d="M9 6v12M15 6v12M6 9h12M6 15h12"/><path d="M9 6a3 3 0 1 0-3 3M15 6a3 3 0 1 1 3 3M9 18a3 3 0 1 1-3-3M15 18a3 3 0 1 0 3-3"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4.5M12 17.2v.1"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.1"/>',
    refresh: '<path d="M19.5 11A7.5 7.5 0 0 0 6 7.5L4.5 9M4.5 13A7.5 7.5 0 0 0 18 16.5l1.5-1.5"/><path d="M4.5 4.5V9H9M19.5 19.5V15H15"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    sparkles:
      '<path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18.5l-1.8-5.9L4.5 10.8 10.2 9 12 3.5Z"/><path d="M19 17v4M17 19h4"/>',
    terminal: '<rect x="3.5" y="5" width="17" height="14" rx="2.2"/><path d="m7.5 10 2.5 2-2.5 2M12.5 14.5h4"/>',
    'mode-solo': '<circle cx="12" cy="12" r="4.2"/><circle cx="12" cy="12" r="8.2" stroke-opacity=".35"/>',
    'mode-duel': '<circle cx="8.7" cy="12" r="5.2"/><circle cx="15.3" cy="12" r="5.2"/>',
    'mode-debate':
      '<circle cx="12" cy="5.8" r="2.6"/><circle cx="5.8" cy="17" r="2.6"/><circle cx="18.2" cy="17" r="2.6"/><path d="M10.7 8.1 7.1 14.7M13.3 8.1l3.6 6.6M8.4 17h7.2"/>',
  } as const;

  export type IconName = keyof typeof ICONS;
</script>

<script lang="ts">
  interface Props {
    name: IconName;
    size?: number;
    label?: string;
    class?: string;
  }

  let { name, size = 18, label, class: className = '' }: Props = $props();
</script>

<svg
  class="icon {className}"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  role={label ? 'img' : undefined}
  aria-label={label}
  aria-hidden={label ? undefined : 'true'}
  focusable="false">{@html ICONS[name]}</svg>

<style>
  .icon {
    flex: none;
    display: block;
  }
</style>
