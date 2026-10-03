<script lang="ts">
  // Live progress of a Council turn: Answers -> Review 1..N -> Synthesis.
  import { debateSteps } from '../lib/debate-steps';
  import { i18n } from '../lib/i18n/index.svelte';
  import { isTerminal, type TurnView } from '../lib/turns.svelte';

  interface Props {
    turn: TurnView;
    plannedRounds: number;
  }

  let { turn, plannedRounds }: Props = $props();

  const steps = $derived(debateSteps(turn, plannedRounds));

  const activeIndex = $derived(steps.findIndex((s) => s.state === 'active'));
  const fill = $derived.by(() => {
    if (steps.length < 2) return 1;
    const lastReached = steps.reduce((acc, s, i) => (s.state === 'done' || s.state === 'active' ? i : acc), 0);
    const partial = activeIndex >= 0 ? (steps[activeIndex]?.progress ?? 0) * 0.9 : 0;
    return Math.min(1, (lastReached + (activeIndex >= 0 ? partial : 0)) / (steps.length - 1));
  });
</script>

<div class="stepper" class:running={!isTerminal(turn.status)}>
  <div class="rail" aria-hidden="true"><span class="rail-fill" style:width="{fill * 100}%"></span></div>
  <ol aria-label={i18n.m.turn.steps.label}>
    {#each steps as step (step.key)}
      <li class={step.state} aria-current={step.state === 'active' ? 'step' : undefined}>
        <span class="node" aria-hidden="true"></span>
        <span class="label">{step.label}</span>
        <span class="sr-only">({step.stateLabel})</span>
      </li>
    {/each}
  </ol>
</div>

<style>
  .stepper {
    position: relative;
    padding: 0.75rem 1rem 0.6rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: rgb(15 17 27 / 0.72);
    -webkit-backdrop-filter: blur(10px);
    backdrop-filter: blur(10px);
  }

  ol {
    position: relative;
    display: flex;
    justify-content: space-between;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .rail {
    position: absolute;
    top: calc(0.75rem + 7px);
    left: calc(1rem + 7px);
    right: calc(1rem + 7px);
    height: 2px;
    border-radius: 2px;
    background: rgb(255 255 255 / 0.1);
    overflow: hidden;
  }

  .rail-fill {
    display: block;
    height: 100%;
    background: linear-gradient(90deg, var(--claude), #b35ad6, var(--chatgpt));
    transition: width 600ms var(--ease-out);
  }

  .running .rail-fill {
    background-size: 200% 100%;
    background-image: linear-gradient(90deg, var(--claude), #b35ad6, var(--chatgpt), #b35ad6, var(--claude));
    animation: shimmer 2.4s linear infinite;
  }

  li {
    position: relative;
    display: grid;
    justify-items: center;
    gap: 0.35rem;
    min-width: 0;
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  li:first-child {
    justify-items: start;
  }

  li:last-child {
    justify-items: end;
  }

  .node {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.18);
    background: var(--surface-2);
    transition:
      background var(--dur-med) var(--ease-out),
      border-color var(--dur-med) var(--ease-out);
  }

  .done .node {
    border-color: transparent;
    background: linear-gradient(135deg, var(--claude), var(--chatgpt));
  }

  .done {
    color: var(--text-secondary);
  }

  .active {
    color: var(--text-primary);
    font-weight: 600;
  }

  .active .node {
    border-color: var(--accent);
    background: var(--surface-3);
    box-shadow: 0 0 0 4px rgb(139 156 255 / 0.18);
    animation: node-pulse 1.4s ease-in-out infinite;
  }

  .skipped .label {
    text-decoration: line-through;
    text-decoration-color: rgb(255 255 255 / 0.3);
  }

  .skipped .node {
    border-style: dashed;
  }

  .failed .node {
    border-color: var(--critical);
  }

  .failed {
    color: #ffb3ba;
  }

  .label {
    white-space: nowrap;
  }

  @keyframes node-pulse {
    50% {
      box-shadow: 0 0 0 7px rgb(139 156 255 / 0.05);
    }
  }

  @media (max-width: 560px) {
    .label {
      font-size: 0.68rem;
    }
  }
</style>
