// Steps of a Consell turn for the stepper: Respostes -> Revisió 1..N -> Síntesi.
// The same function serves live turns and stored ones, so a reloaded debate
// reads like it did live.

import { isTerminal, type TurnView } from './turns.svelte';

export type StepState = 'done' | 'active' | 'pending' | 'skipped' | 'failed';

export interface DebateStep {
  key: string;
  label: string;
  state: StepState;
  /** State for screen readers ("fet", "omès per consens"...). */
  stateLabel: string;
  /** Progress inside the step (0..1) from finished streams (the synthesis: its last attempt done). */
  progress: number;
}

const STATE_LABEL: Record<StepState, string> = {
  done: 'fet',
  active: 'en curs',
  pending: 'pendent',
  skipped: 'omès',
  failed: 'aturat',
};

/** Steps of a debate; `plannedRounds` applies when the turn does not carry its options. */
export function debateSteps(turn: TurnView, plannedRounds: number): DebateStep[] {
  const maxSeen = Math.max(0, ...turn.streams.filter((s) => s.kind === 'revision').map((s) => s.round));
  const rounds = Math.max(turn.options?.debate.rounds ?? plannedRounds, maxSeen);
  const terminal = isTerminal(turn.status);
  // Rounds left out are "skipped by consensus" only when the debate reached it.
  const skippedLabel = turn.consensus?.reached ? 'omès per consens' : STATE_LABEL.skipped;

  // Index of the step in progress: 0 answers, r revision r, rounds+1 synthesis.
  let current = 0;
  if (turn.phase === 'revision') current = turn.round;
  else if (turn.phase === 'synthesis') current = rounds + 1;

  const streamsOf = (i: number) =>
    turn.streams.filter((s) =>
      i === 0 ? s.kind === 'answer' : i === rounds + 1 ? s.kind === 'synthesis' : s.kind === 'revision' && s.round === i,
    );

  const list: DebateStep[] = [];
  for (let i = 0; i <= rounds + 1; i++) {
    const synthesis = i === rounds + 1;
    const label = i === 0 ? 'Respostes' : synthesis ? 'Síntesi' : `Revisió ${i}`;
    const own = streamsOf(i);
    const expected = synthesis ? 1 : 2;
    // The synthesis follows its last attempt: each attempt has its own stream, and
    // one that failed is followed by another (the other agent, or a kept answer).
    const finished = synthesis
      ? Number(own.at(-1)?.status === 'done')
      : own.filter((s) => s.status !== 'streaming').length;
    let state: StepState;
    if (turn.status === 'done') {
      state = own.length ? 'done' : 'skipped';
    } else if (terminal) {
      state = i < current ? (own.length ? 'done' : 'skipped') : i === current ? 'failed' : 'pending';
    } else if (i < current) {
      state = own.length ? 'done' : 'skipped';
    } else if (i === current) {
      state = 'active';
    } else {
      state = 'pending';
    }
    list.push({
      key: `s${i}`,
      label,
      state,
      stateLabel: state === 'skipped' ? skippedLabel : STATE_LABEL[state],
      progress: Math.min(1, finished / expected),
    });
  }
  return list;
}
