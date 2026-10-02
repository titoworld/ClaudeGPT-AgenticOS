<script lang="ts">
  // Claude's check of each PDF of the question for ChatGPT with the subscription, live
  // (docs/adr/0009-adjunts.md): ChatGPT cannot open a PDF, so it reads the text the server
  // extracted, and Claude checks that text against the document while it answers. Each
  // PDF says that it is being checked, then what ChatGPT reads through Claude and what the
  // check cost (tokens and euros, like the rest of the turn's usage), why nobody could
  // check it, or that the turn ended before the check did. A polite live region announces
  // each change: the turn keeps it in the page from the start, even with no check in it
  // (then out of the layout), since a region that comes with its content is not announced
  // and a PDF's only event may be its end (a check an earlier turn stored).
  import { approxEur, costTitle, processedTokens, tokenBreakdown } from '../lib/costs';
  import { formatInt } from '../lib/format';
  import { afterColon, checkDetails } from '../lib/pdf-pages';
  import type { PdfCheckView } from '../lib/turns.svelte';
  import Icon from './Icon.svelte';
  import PlainText from './PlainText.svelte';

  interface Props {
    /** In the order of the attachments (`pdfChecks`). */
    checks: PdfCheckView[];
    /** Euros per dollar, for the cost of each check. */
    eurPerUsd: number;
  }

  let { checks, eurPerUsd }: Props = $props();

  /** What a check's calls billed in this turn, as the turn shows usage; null when nothing. */
  function billed(check: PdfCheckView): { tokens: string; tokensTitle: string; cost: string | null; costTitle: string } | null {
    const usage = check.usage;
    if (!usage || check.reused) return null;
    const tokens = processedTokens(usage);
    const usd = usage.cost_usd;
    const cost = usd != null && usd > 0 ? approxEur(usd, eurPerUsd) : null;
    if (!tokens && !cost) return null;
    return {
      tokens: tokens ? `${formatInt(tokens)} tokens` : '',
      tokensTitle: tokenBreakdown(usage),
      cost,
      costTitle: cost ? costTitle(check.costBasis, usd!) : '',
    };
  }
</script>

<div class="pdf-live" class:sr-only={!checks.length} aria-live="polite">
  {#if checks.length}
    <ul class="pdf-checks" aria-label="Contrast dels PDF per a ChatGPT">
      {#each checks as check (check.attachmentId)}
        {@const details = checkDetails(check)}
        {@const reused = check.reused && check.state === 'checked'}
        {@const bill = billed(check)}
        <li class="pdf-check {check.state}">
          <span class="mark" aria-hidden="true">
            {#if check.state === 'checking'}
              <span class="spinner"></span>
            {:else if check.state === 'checked'}
              <Icon name="check" size={14} />
            {:else if check.state === 'interrupted'}
              <Icon name="x" size={14} />
            {:else}
              <Icon name="alert" size={14} />
            {/if}
          </span>
          <div class="body">
            <p class="head">
              {#if check.state === 'checking'}
                Claude contrasta «<PlainText text={check.name} />» per a ChatGPT…
              {:else if check.state === 'checked'}
                ChatGPT llegeix «<PlainText text={check.name} />» contrastat per Claude
              {:else if check.state === 'interrupted'}
                El torn s'ha aturat abans que Claude acabés de contrastar «<PlainText text={check.name} />».
              {:else}
                ChatGPT llegeix el text de «<PlainText text={check.name} />» sense contrastar{check.reason
                  ? `: ${afterColon(check.reason)}`
                  : '.'}
              {/if}
            </p>
            {#if details.length || reused || bill}
              <ul class="facts">
                {#each details as detail (detail)}
                  <li class="detail">{detail}</li>
                {/each}
                {#if reused}
                  <li class="detail">ja contrastat abans</li>
                {:else if bill}
                  {#if bill.tokens}
                    <li class="detail tokens" title={bill.tokensTitle}>
                      {bill.tokens} <span class="sr-only">({bill.tokensTitle})</span>
                    </li>
                  {/if}
                  {#if bill.cost}
                    <li class="detail cost" class:equivalent={check.costBasis === 'equivalent'} title={bill.costTitle}>
                      {bill.cost} <span class="sr-only">({bill.costTitle})</span>
                    </li>
                  {/if}
                {/if}
              </ul>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .pdf-checks {
    display: grid;
    gap: 0.45rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .pdf-check {
    display: flex;
    align-items: flex-start;
    gap: 0.55rem;
    padding: 0.55rem 0.8rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    border-left: 3px solid var(--claude);
    background: rgb(15 17 27 / 0.8);
    font-size: var(--text-sm);
    color: var(--text-secondary);
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  .pdf-check.unchecked {
    border-left-color: var(--warning);
  }

  /* Stopped with the turn: neither a result nor a warning about what ChatGPT read. */
  .pdf-check.interrupted {
    border-left-color: var(--border-strong);
  }

  .mark {
    display: grid;
    place-items: center;
    flex: none;
    width: 1rem;
    height: 1.3rem;
    color: var(--claude-glow);
  }

  .unchecked .mark {
    color: var(--warning);
  }

  .interrupted .mark {
    color: var(--text-muted);
  }

  .spinner {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.18);
    border-top-color: var(--claude);
    animation: spin 0.8s linear infinite;
  }

  .body {
    display: grid;
    gap: 0.15rem;
    min-width: 0;
  }

  .head {
    color: var(--text-primary);
    overflow-wrap: anywhere;
  }

  .unchecked .head {
    color: #ffd99a;
  }

  .interrupted .head {
    color: var(--text-secondary);
  }

  .facts {
    display: flex;
    flex-wrap: wrap;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .detail {
    overflow-wrap: anywhere;
  }

  /* A separator for the eyes only: each fact is an item of the list for screen readers. */
  .detail + .detail::before {
    content: '·';
    content: '·' / '';
    margin: 0 0.45rem;
  }

  .tokens,
  .cost {
    cursor: help;
  }

  /* Included in the subscription: a value, not money spent (as on each answer). */
  .cost.equivalent {
    text-decoration: underline dotted rgb(255 255 255 / 0.3);
    text-underline-offset: 3px;
  }

  /* No spin: the words say it is under way. */
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation: none;
    }
  }
</style>
