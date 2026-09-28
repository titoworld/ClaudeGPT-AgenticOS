<script lang="ts">
  import { copyText } from '../lib/clipboard';
  import { toasts } from '../lib/toasts.svelte';
  import Icon from './Icon.svelte';

  interface Props {
    text: string;
    label?: string;
    /**
     * What goes on the clipboard, plus an optional toast. Model text passes
     * `answerForClipboard` (lib/hidden-chars.ts), so hidden characters (Trojan
     * Source) are copied as the marks the answer shows. Without it `text` is
     * copied as is (the login screen's fixed command), which keeps the
     * hidden-character helpers out of the entry chunk.
     */
    prepare?: (text: string) => { text: string; notice: string | null };
  }

  let { text, label = 'Copia la resposta', prepare }: Props = $props();
  let state: 'idle' | 'ok' | 'error' = $state('idle');
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function copy(): Promise<void> {
    const { text: copied, notice } = prepare ? prepare(text) : { text, notice: null };
    const ok = await copyText(copied);
    state = ok ? 'ok' : 'error';
    if (ok && notice) toasts.push(notice);
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
