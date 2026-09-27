<script lang="ts">
  import { copyVisible } from '../lib/clipboard';
  import { toasts } from '../lib/toasts.svelte';
  import Icon from './Icon.svelte';

  interface Props {
    text: string;
    label?: string;
  }

  let { text, label = 'Copia la resposta' }: Props = $props();
  let state: 'idle' | 'ok' | 'error' = $state('idle');
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function copy(): Promise<void> {
    // Hidden characters (Trojan Source) are copied as the visible marks the answer shows.
    const { ok, revealed } = await copyVisible(text);
    state = ok ? 'ok' : 'error';
    if (ok && revealed) {
      toasts.push(
        revealed === 1
          ? "La resposta tenia 1 caràcter invisible: s'ha copiat com a ⟨U+…⟩."
          : `La resposta tenia ${revealed} caràcters invisibles: s'han copiat com a ⟨U+…⟩.`,
      );
    }
    clearTimeout(timer);
    timer = setTimeout(() => (state = 'idle'), 1600);
  }

  $effect(() => () => clearTimeout(timer));
</script>

<button
  type="button"
  class="icon-btn small copy"
  class:ok={state === 'ok'}
  onclick={copy}
  aria-label={state === 'ok' ? 'Copiat' : label}
  title={state === 'ok' ? 'Copiat' : state === 'error' ? "No s'ha pogut copiar" : label}>
  <Icon name={state === 'ok' ? 'check' : 'copy'} size={15} />
</button>

<style>
  .ok {
    color: var(--good);
  }
</style>
