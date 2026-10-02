<script lang="ts">
  // Euro amount typed as text: accepts the Catalan decimal comma ("50,5"), and
  // text that is not an amount binds NaN, which validation reports, instead of the
  // null a number input gives (that would clear the saved value). See parseAmount.
  import type { HTMLInputAttributes } from 'svelte/elements';
  import { amountText, parseAmount } from '../lib/settings';

  interface Props extends Omit<HTMLInputAttributes, 'type' | 'value' | 'inputmode' | 'oninput'> {
    value: number | null;
  }

  let { value = $bindable(), ...rest }: Props = $props();
  let typed = $state('');
  // What the owner typed while it still means `value`, else `value` itself (form reset).
  const text = $derived(amountText(typed, value));

  function oninput(e: Event & { currentTarget: HTMLInputElement }): void {
    typed = e.currentTarget.value;
    value = parseAmount(typed);
  }
</script>

<input {...rest} type="text" inputmode="decimal" autocomplete="off" spellcheck="false" value={text} {oninput} />
