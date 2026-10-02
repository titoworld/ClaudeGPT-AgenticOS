<script lang="ts">
  // The interface's language (lib/i18n): each language named in itself, never translated,
  // so anyone finds theirs. The choice stays in this browser.
  import { app } from '../lib/app.svelte';
  import { asLocale, i18n, LOCALE_NAMES, LOCALES } from '../lib/i18n/index.svelte';
  import Icon from './Icon.svelte';

  interface Props {
    /** `compact`: the icon and the select alone (the login screen); else a full-width field. */
    compact?: boolean;
  }

  let { compact = false }: Props = $props();
  const uid = $props.id();

  function choose(e: Event & { currentTarget: HTMLSelectElement }): void {
    const locale = asLocale(e.currentTarget.value);
    if (locale) app.setLocale(locale);
  }
</script>

<div class="language" class:compact>
  <label for="{uid}-lang">
    <Icon name="globe" size={compact ? 15 : 16} />
    <span class:sr-only={compact}>{i18n.m.common.language}</span>
  </label>
  <select id="{uid}-lang" class="input" value={i18n.locale} onchange={choose}>
    {#each LOCALES as locale (locale)}
      <option value={locale} lang={locale}>{LOCALE_NAMES[locale]}</option>
    {/each}
  </select>
</div>

<style>
  .language {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }

  label {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    color: var(--text-secondary);
    font-size: var(--text-sm);
    font-weight: 600;
  }

  select {
    flex: 1;
    min-width: 0;
  }

  .compact {
    gap: 0.35rem;
  }

  .compact select {
    flex: none;
    width: auto;
    padding-block: 0.3rem;
    font-size: var(--text-sm);
  }
</style>
