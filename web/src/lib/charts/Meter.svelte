<script lang="ts">
  /**
   * Usage meter: the fill carries severity (accent -> warning -> critical) and
   * the track is a faint step of the same hue, so state reads across the bar.
   */
  import { i18n } from '../i18n/index.svelte';
  import type { LimitTone } from './usage';

  interface Props {
    /** 0..100 */
    value: number;
    /** Severity for limits; 'neutral' for plain shares. */
    tone: LimitTone | 'neutral';
    label: string;
    /** Spoken value; defaults to 'N% used'. */
    valueText?: string;
  }

  let { value, tone, label, valueText }: Props = $props();
  const pct = $derived(Math.max(0, Math.min(100, value)));
</script>

<div
  class="meter {tone}"
  role="meter"
  aria-label={label}
  aria-valuemin={0}
  aria-valuemax={100}
  aria-valuenow={Math.round(pct)}
  aria-valuetext={valueText ?? i18n.m.dashboard.chart.used(Math.round(pct))}
>
  <span class="fill" style:width="{pct}%"></span>
</div>

<style>
  .meter {
    --tone: var(--accent);
    position: relative;
    height: 8px;
    overflow: hidden;
    border-radius: 999px;
    background: color-mix(in srgb, var(--tone) 18%, transparent);
  }

  .meter.warning {
    --tone: var(--warning);
  }

  .meter.critical {
    --tone: var(--critical);
  }

  .meter.unknown {
    --tone: var(--text-muted);
  }

  .meter.neutral {
    --tone: var(--text-secondary);
  }

  .fill {
    display: block;
    height: 100%;
    min-width: 0;
    border-radius: 999px;
    background: var(--tone);
    transition: width var(--dur-med) var(--ease-out);
  }
</style>
