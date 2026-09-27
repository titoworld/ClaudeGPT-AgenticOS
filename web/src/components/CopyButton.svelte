<script lang="ts">
  import { copyText } from '../lib/clipboard';
  import Icon from './Icon.svelte';

  interface Props {
    text: string;
    label?: string;
  }

  let { text, label = 'Copia la resposta' }: Props = $props();
  let state: 'idle' | 'ok' | 'error' = $state('idle');
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function copy(): Promise<void> {
    const ok = await copyText(text);
    state = ok ? 'ok' : 'error';
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
