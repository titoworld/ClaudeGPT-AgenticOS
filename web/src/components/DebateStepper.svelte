<script lang="ts">
  // Live progress of a Consell turn: Respostes -> Revisió 1..N -> Síntesi.
  import { isTerminal, type TurnView } from '../lib/turns.svelte';

  interface Props {
    turn: TurnView;
    plannedRounds: number;
  }

  let { turn, plannedRounds }: Props = $props();

  type StepState = 'done' | 'active' | 'pending' | 'skipped' | 'failed';
  interface Step {
    key: string;
    label: string;
    state: StepState;
    /** Progress inside the step (0..1) from finished streams. */
    progress: number;
  }

  const steps: Step[] = $derived.by(() => {
    const maxSeen = Math.max(0, ...turn.streams.filter((s) => s.kind === 'revision').map((s) => s.round));
    const rounds = Math.max(turn.options?.debate.rounds ?? plannedRounds, maxSeen);
    const hasSynthesis = turn.streams.some((s) => s.kind === 'synthesis');
    const terminal = isTerminal(turn.status);

    // Index of the step in progress: 0 answers, r revision r, rounds+1 synthesis.
    let current = 0;
    if (turn.phase === 'revision') current = turn.round;
    else if (turn.phase === 'synthesis') current = rounds + 1;

    const streamsOf = (i: number) =>
      turn.streams.filter((s) =>
        i === 0 ? s.kind === 'answer' : i === rounds + 1 ? s.kind === 'synthesis' : s.kind === 'revision' && s.round === i,
      );

    const list: Step[] = [];
    for (let i = 0; i <= rounds + 1; i++) {
      const label = i === 0 ? 'Respostes' : i === rounds + 1 ? 'Síntesi' : `Revisió ${i}`;
      const own = streamsOf(i);
      const expected = i === rounds + 1 ? 1 : 2;
      const finished = own.filter((s) => s.status !== 'streaming').length;
      let state: StepState;
      if (turn.status === 'done') {
        state = own.length || (i === rounds + 1 && hasSynthesis) ? 'done' : 'skipped';
      } else if (terminal) {
        state = i < current ? (own.length ? 'done' : 'skipped') : i === current ? 'failed' : 'pending';
      } else if (i < current) {
        state = own.length ? 'done' : 'skipped';
      } else if (i === current) {
        state = 'active';
      } else {
        state = 'pending';
      }
      list.push({ key: `s${i}`, label, state, progress: Math.min(1, finished / expected) });
    }
    return list;
  });

  const activeIndex = $derived(steps.findIndex((s) => s.state === 'active'));
  const fill = $derived.by(() => {
    if (steps.length < 2) return 1;
    const lastReached = steps.reduce((acc, s, i) => (s.state === 'done' || s.state === 'active' ? i : acc), 0);
    const partial = activeIndex >= 0 ? (steps[activeIndex]?.progress ?? 0) * 0.9 : 0;
    return Math.min(1, (lastReached + (activeIndex >= 0 ? partial : 0)) / (steps.length - 1));
  });

  const STATE_LABEL: Record<StepState, string> = {
    done: 'fet',
    active: 'en curs',
    pending: 'pendent',
    skipped: 'omès per consens',
    failed: 'aturat',
  };
</script>

<div class="stepper" class:running={!isTerminal(turn.status)}>
  <div class="rail" aria-hidden="true"><span class="rail-fill" style:width="{fill * 100}%"></span></div>
  <ol aria-label="Progrés del consell">
    {#each steps as step (step.key)}
      <li class={step.state} aria-current={step.state === 'active' ? 'step' : undefined}>
        <span class="node" aria-hidden="true"></span>
        <span class="label">{step.label}</span>
        <span class="sr-only">({STATE_LABEL[step.state]})</span>
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
