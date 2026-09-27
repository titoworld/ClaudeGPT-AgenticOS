import { describe, expect, it } from 'vitest';
import { debateSteps } from './debate-steps';
import { cancelledDebateEvents, cancelledDebateMessages, debateEvents, debateMessages } from './test-fixtures';
import { applyTurnEvent, createLiveTurn, turnsFromMessages, type TurnView } from './turns.svelte';

const states = (turn: TurnView, plannedRounds = 2) =>
  debateSteps(turn, plannedRounds).map((s) => `${s.label}: ${s.stateLabel}`);

describe('debateSteps', () => {
  it('a reloaded debate that was stopped reads like it did live (F4)', () => {
    const live = createLiveTurn({
      requestId: 'req-x',
      question: 'Debat llarg',
      mode: 'debate',
      conversationId: 5,
      options: { debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: false },
    });
    for (const ev of cancelledDebateEvents()) applyTurnEvent(live, ev);
    const [stored] = turnsFromMessages(cancelledDebateMessages(), 5);

    const expected = ['Respostes: fet', 'Revisió 1: aturat', 'Revisió 2: pendent', 'Revisió 3: pendent', 'Síntesi: pendent'];
    expect(states(live)).toEqual(expected);
    expect(states(stored!)).toEqual(expected);
  });

  it('labels rounds left out "omès per consens" only when the debate reached consensus', () => {
    // Consensus in round 1 of 3: rounds 2 and 3 were skipped by consensus.
    const live = createLiveTurn({
      requestId: 'req-1',
      question: 'Pregunta?',
      mode: 'debate',
      options: { debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: true },
    });
    for (const ev of debateEvents()) applyTurnEvent(live, ev);
    expect(states(live)).toEqual([
      'Respostes: fet',
      'Revisió 1: fet',
      'Revisió 2: fet',
      'Revisió 3: omès per consens',
      'Síntesi: fet',
    ]);

    // A finished debate without consensus (e.g. a degraded synthesis after a failure).
    const [stored] = turnsFromMessages(debateMessages());
    stored!.options!.debate.rounds = 3;
    stored!.consensus = { reached: false, round: 2, scores: { claude: 60, chatgpt: 50 } };
    expect(states(stored!).at(3)).toBe('Revisió 3: omès');
  });

  it('shows a running debate in progress', () => {
    const live = createLiveTurn({ requestId: 'req-x', question: 'q', mode: 'debate' });
    for (const ev of cancelledDebateEvents().slice(0, 9)) applyTurnEvent(live, ev);
    expect(states(live)).toEqual(['Respostes: fet', 'Revisió 1: en curs', 'Revisió 2: pendent', 'Síntesi: pendent']);
  });
});
