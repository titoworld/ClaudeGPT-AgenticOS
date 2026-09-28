// How a turn ended (ADR 0007; audit A9, A14 and N10). Live, the terminal event says it;
// stored, the outcome the engine writes on the question does, so a reloaded turn shows
// what the live one did: its status, its error, the calls that failed and its whole cost.
import { describe, expect, it } from 'vitest';
import { turnCost } from './costs';
import type { Message, MessageMeta, TurnEvent, TurnMode } from './protocol';
import {
  CANCELLED_TOTAL,
  cancelledDebateEvents,
  cancelledDebateMessages,
  cancelledDuelEvents,
  cancelledDuelMessages,
  degradedSynthesisEvents,
  failedRevisionEvents,
  failedRevisionMessages,
  failedSoloEvents,
  failedSoloMessages,
  fallbackSynthesisEvents,
  lateFailureDuelEvents,
  lateFailureDuelMessages,
  message,
  oneSurvivorDebateEvents,
  outcome,
  priced,
  QUICK_DEBATE,
  quickDebateMessages,
  quickDebateOutcome,
  REFUSED,
  sequence,
  usage,
  withOutcome,
  withoutOutcome,
  type LateFailure,
} from './test-fixtures';
import {
  applyTurnEvent,
  createLiveTurn,
  INCOMPLETE_KIND,
  revisionRounds,
  shownSynthesis,
  streamsByAgent,
  synthesisAttempts,
  synthesisNote,
  turnsFromMessages,
  turnUsage,
  type StreamView,
  type TurnView,
} from './turns.svelte';

function live(events: TurnEvent[], mode: TurnMode): TurnView {
  const turn = createLiveTurn({
    requestId: events[0]!.request_id,
    question: 'Pregunta?',
    mode,
    options: mode === 'debate' ? QUICK_DEBATE : null,
    conversationId: 1,
  });
  for (const ev of events) applyTurnEvent(turn, ev);
  return turn;
}

function reloaded(messages: Message[]): TurnView {
  const turns = turnsFromMessages(messages, 1);
  expect(turns).toHaveLength(1);
  return turns[0]!;
}

/** What a view tells about each call: agent, kind, round, status and error. */
const calls = (streams: StreamView[]) => streams.map((s) => [s.agent, s.kind, s.round, s.status, s.error?.message ?? null]);
/** The same, in a fixed order (live, calls start in parallel; stored, failures come last). */
const sortedCalls = (streams: StreamView[]) => calls(streams).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

const LATE: LateFailure[] = ['refusal', 'empty'];
const LATE_TOTAL_USD: Record<LateFailure, number> = { refusal: 0.029316, empty: 0.029116 };

describe('live: what failed calls and turns cost (N10)', () => {
  it('a failed stream keeps what the failed call was billed', () => {
    const events = lateFailureDuelEvents();
    const turn = live(events.slice(0, -1), 'duel');
    const claude = streamsByAgent(turn, 'answer', 0).claude!;
    expect(claude.status).toBe('failed');
    expect(claude.usage).toEqual({ ...usage(5000, 300, 1000), cost_usd: 0.0262 });
    // A failed call billed nothing when the event has no usage.
    const timeout = live(failedRevisionEvents(), 'debate');
    expect(streamsByAgent(timeout, 'revision', 1).chatgpt).toMatchObject({ status: 'failed', usage: null });
  });

  it('a failed turn takes its total from turn.failed', () => {
    const turn = live(failedSoloEvents(), 'solo');
    expect(turn.status).toBe('failed');
    expect(turnUsage(turn)).toEqual(REFUSED);
    expect(turnCost(turn).totalUsd).toBeCloseTo(0.0262, 12);
  });

  it('a cancelled turn takes its total from turn.cancelled (the compaction summary included)', () => {
    const turn = live(cancelledDuelEvents(), 'duel');
    expect(turn.status).toBe('cancelled');
    expect(turnUsage(turn)).toEqual(CANCELLED_TOTAL);
    expect(turnCost(turn).totalUsd).toBeCloseTo(0.003114 + 0.00318, 12);
  });

  it('without a total from the server, a failed turn adds up what its calls were billed', () => {
    // A terminal event without it, as servers sent before it existed.
    const events = failedSoloEvents().map((e) => (e.type === 'turn.failed' ? ({ ...e, usage: undefined } as unknown as TurnEvent) : e));
    const turn = live(events, 'solo');
    expect(turnUsage(turn)).toEqual(REFUSED);
  });
});

describe('turnsFromMessages: a duel with a late billed failure (A9)', () => {
  for (const variant of LATE) {
    it(`costs the same after a reload as live (${variant})`, () => {
      const l = live(lateFailureDuelEvents(variant), 'duel');
      const s = reloaded(lateFailureDuelMessages(variant));
      expect(turnUsage(s)).toEqual(turnUsage(l));
      expect(turnCost(s).totalUsd).toBeCloseTo(LATE_TOTAL_USD[variant], 12);
      expect(turnCost(l).totalUsd).toBeCloseTo(LATE_TOTAL_USD[variant], 12);
      expect(s.status).toBe('done');
      expect(s.error).toBeNull();
    });
  }

  it('shows the agent that failed with its reason, as live (A14)', () => {
    const l = live(lateFailureDuelEvents(), 'duel');
    const s = reloaded(lateFailureDuelMessages());
    expect(streamsByAgent(s, 'answer', 0).claude).toMatchObject({
      status: 'failed',
      error: { kind: 'invalid', message: 'Claude ha declinat.' },
      text: '',
      messageId: null,
      usage: null,
    });
    expect(sortedCalls(s.streams)).toEqual(sortedCalls(l.streams));
    expect(s.finalMessageIds).toEqual([2]);
  });

  it('takes the total from the outcome, never adding the compaction or unstored usage again', () => {
    const summary = priced(768, 54, 0.003114);
    const total = priced(9000, 900, 0.05);
    const messages = lateFailureDuelMessages().map((m) => {
      if (m.kind === 'question') {
        const meta: MessageMeta = { ...m.meta, compaction_usage: summary, outcome: outcome({ status: 'completed', usage: total, final_message_ids: [2] }) };
        return { ...m, meta };
      }
      return { ...m, meta: { ...m.meta, unstored_usage: priced(5000, 300, 0.0262) } };
    });
    const s = reloaded(messages);
    expect(turnUsage(s)).toEqual(total);
    expect(turnCost(s).totalUsd).toBe(0.05);
  });
});

describe('turnsFromMessages: turns that did not complete (A14, N10)', () => {
  it('a cancelled duel reloads as cancelled, with what it cost', () => {
    const l = live(cancelledDuelEvents(), 'duel');
    const s = reloaded(cancelledDuelMessages());
    expect(s.status).toBe('cancelled');
    expect(l.status).toBe('cancelled');
    expect(s.error).toBeNull();
    expect(turnUsage(s)).toEqual(turnUsage(l));
    expect(turnUsage(s)).toEqual(CANCELLED_TOTAL);
  });

  it('a failed solo reloads as failed, with its error, the call that failed and its cost', () => {
    const l = live(failedSoloEvents(), 'solo');
    const s = reloaded(failedSoloMessages());
    expect(s.status).toBe('failed');
    expect(s.error).toEqual({ kind: 'invalid', message: 'Claude no ha pogut respondre.' });
    expect(s.error).toEqual(l.error);
    expect(calls(s.streams)).toEqual(calls(l.streams));
    expect(calls(s.streams)).toEqual([['claude', 'answer', 0, 'failed', 'Claude ha declinat.']]);
    expect(turnUsage(s)).toEqual(REFUSED);
    expect(turnCost(s).totalUsd).toBeCloseTo(turnCost(l).totalUsd!, 12);
    expect([s.phase, s.round]).toEqual([l.phase, l.round]);
  });

  it('a cancelled debate keeps the state it had live: no consensus, stopped at its round', () => {
    const events = cancelledDebateEvents();
    const l = createLiveTurn({ requestId: 'req-x', question: 'Debat llarg', mode: 'debate', conversationId: 5 });
    for (const ev of events) applyTurnEvent(l, ev);
    const total = usage(60, 20);
    const s = reloaded(withOutcome(cancelledDebateMessages(), outcome({ status: 'cancelled', usage: total })));
    expect(s.status).toBe('cancelled');
    expect(s.consensus).toBeNull();
    expect(l.consensus).toBeNull();
    expect([s.phase, s.round]).toEqual([l.phase, l.round]);
    expect(turnUsage(s)).toEqual(total);
  });

  it('outcome null: the turn never ended, even with every answer stored', () => {
    for (const messages of [lateFailureDuelMessages(), cancelledDuelMessages(), failedRevisionMessages()]) {
      const s = reloaded(withOutcome(messages, null));
      expect(s.status).toBe('failed');
      expect(s.error).toEqual({ kind: INCOMPLETE_KIND, message: 'Aquest torn no es va completar.' });
    }
    // What it cost as far as the stored messages tell.
    const s = reloaded(withOutcome(cancelledDuelMessages(), null));
    expect(turnUsage(s)).toEqual(CANCELLED_TOTAL);
  });

  it('no outcome key: a turn stored before it existed follows the old rules', () => {
    const s = reloaded(withoutOutcome(lateFailureDuelMessages()));
    expect(s.status).toBe('done');
    expect(s.error).toBeNull();
    expect(turnCost(s).totalUsd).toBeCloseTo(0.003116, 12); // what the answers say (A9 stays for them)
    expect(streamsByAgent(s, 'answer', 0).claude).toBeUndefined();
    // A debate without a synthesis is incomplete, with the consensus of its last round.
    const d = reloaded(withoutOutcome(cancelledDebateMessages()));
    expect(d.status).toBe('failed');
    expect(d.error?.kind).toBe(INCOMPLETE_KIND);
    expect(d.consensus).toEqual({ reached: false, round: 1, scores: { claude: 60, chatgpt: 60 } });
  });

  it('an outcome it cannot read counts as absent', () => {
    for (const bad of ['completed', 7, [], { status: 'maybe', usage: usage(1, 1) }, { usage: usage(1, 1) }]) {
      const messages = lateFailureDuelMessages().map((m) =>
        m.kind === 'question' ? { ...m, meta: { ...m.meta, outcome: bad } as MessageMeta } : m,
      );
      const s = reloaded(messages);
      expect(s.status).toBe('done');
      expect(turnCost(s).totalUsd).toBeCloseTo(0.003116, 12);
    }
  });

  it('reads what it can of an outcome and ignores the rest', () => {
    const odd = {
      status: 'failed',
      error: { message: 'Ha fallat.' },
      failures: [
        { agent: 'claude', kind: 'invalid', message: 'Claude ha declinat.', round: 0 },
        { agent: 'gemini', kind: 'x', message: 'no', round: 0 },
        { agent: 'chatgpt', kind: 'timeout', message: 'Temps esgotat.', round: -1 },
        'garbage',
      ],
      usage: { input_tokens: 10, output_tokens: 'x' },
      final_message_ids: [2, 'x'],
    };
    const s = reloaded([
      message({ id: 1, turn_id: 1, kind: 'question', content: 'Pregunta?', final: true, meta: { mode: 'duel', outcome: odd } as unknown as MessageMeta }),
    ]);
    expect(s.status).toBe('failed');
    expect(s.error).toEqual({ kind: 'unavailable', message: 'Ha fallat.' });
    expect(calls(s.streams)).toEqual([['claude', 'answer', 0, 'failed', 'Claude ha declinat.']]);
    expect(turnUsage(s)).toMatchObject({ input_tokens: 10, output_tokens: 0, cost_usd: null });
    expect(s.finalMessageIds).toEqual([2]);
    expect(s.savings).toBeNull();
    expect(s.consensus).toBeNull();
    expect(s.cached).toBe(false);
  });
});

describe('turnsFromMessages: debates with calls that failed (A14)', () => {
  it('a failed revision shows in its round with its reason', () => {
    const l = createLiveTurn({ requestId: 'req-v', question: 'Pregunta?', mode: 'debate', conversationId: 6 });
    for (const ev of failedRevisionEvents()) applyTurnEvent(l, ev);
    const s = reloaded(failedRevisionMessages());
    const round = (t: TurnView) => sortedCalls(revisionRounds(t)[0]!.streams);
    expect(round(s)).toEqual(round(l));
    expect(round(s)).toContainEqual(['chatgpt', 'revision', 1, 'failed', 'Temps esgotat.']);
    expect(s.consensus).toEqual({ reached: false, round: 1, scores: { claude: 70 } });
    expect(shownSynthesis(s)?.messageId).toBe(94);
    expect([s.phase, s.round]).toEqual([l.phase, l.round]);
  });

  it("an agent's failed first answer, and the partner's answer kept as final", () => {
    const l = live(oneSurvivorDebateEvents(), 'debate');
    const failures = [{ agent: 'claude' as const, kind: 'timeout', message: 'Temps esgotat.', round: 0 }];
    const s = reloaded(
      withOutcome(quickDebateMessages({ agent: 'chatgpt', content: 'Resposta de ChatGPT', degraded: true }, ['chatgpt']), quickDebateOutcome(failures)),
    );
    expect(streamsByAgent(s, 'answer', 0).claude).toMatchObject({ status: 'failed', error: { kind: 'timeout', message: 'Temps esgotat.' } });
    expect(sortedCalls(streamsAnswers(s))).toEqual(sortedCalls(streamsAnswers(l)));
    expect(synthesisNote(s)).toBe(synthesisNote(l));
  });

  it('synthesis attempts that failed come before the synthesis that counts, as live', () => {
    const fallback = [{ agent: 'claude' as const, kind: 'unavailable', message: 'Connexió tallada.', round: 0 }];
    const degraded = [
      { agent: 'claude' as const, kind: 'unavailable', message: 'Error de Claude.', round: 0 },
      { agent: 'chatgpt' as const, kind: 'unavailable', message: 'Error de ChatGPT.', round: 0 },
    ];
    const cases: [TurnEvent[], Message[]][] = [
      [fallbackSynthesisEvents(), withOutcome(quickDebateMessages({ agent: 'chatgpt', content: 'Síntesi de ChatGPT' }), quickDebateOutcome(fallback))],
      [
        degradedSynthesisEvents(),
        withOutcome(quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true }), quickDebateOutcome(degraded)),
      ],
    ];
    for (const [events, messages] of cases) {
      const l = live(events, 'debate');
      const s = reloaded(messages);
      expect(calls(synthesisAttempts(s))).toEqual(calls(synthesisAttempts(l)));
      expect(shownSynthesis(s)?.messageId).toBe(63);
      expect(synthesisNote(s)).toBe(synthesisNote(l));
      expect(sortedCalls(streamsAnswers(s))).toEqual(sortedCalls(streamsAnswers(l)));
      expect(s.status).toBe('done');
    }
  });

  it('takes the final messages, savings, consensus and cache flag from the outcome', () => {
    const savings = { cache: 900, compaction: 0, early_stop: 0, unchanged: 0, total: 900, cost_usd: 0.01 };
    const s = reloaded(
      withOutcome(
        quickDebateMessages({ agent: 'claude', content: 'Síntesi' }),
        outcome({
          status: 'completed',
          usage: usage(0, 0),
          savings,
          consensus: { reached: true, round: 0, scores: { claude: 90, chatgpt: 95 } },
          final_message_ids: [63],
          cached: true,
        }),
      ),
    );
    expect(s.savings).toEqual(savings);
    expect(s.consensus).toEqual({ reached: true, round: 0, scores: { claude: 90, chatgpt: 95 } });
    expect(s.finalMessageIds).toEqual([63]);
    expect(s.cached).toBe(true);
  });
});

describe('failures in solo and duel turns', () => {
  it('both agents failing a duel fail the turn, each card with its reason', () => {
    const failures = [
      { agent: 'chatgpt' as const, kind: 'timeout', message: 'Temps esgotat.', round: 0 },
      { agent: 'claude' as const, kind: 'invalid', message: 'Claude ha declinat.', round: 0 },
    ];
    const error = { kind: 'unavailable', message: 'Cap dels dos agents ha pogut respondre.' };
    const events = sequence('req-b', [
      { type: 'turn.started', conversation_id: 1, turn_id: 1, mode: 'duel', new_conversation: false },
      { type: 'phase', phase: 'answer', round: 0 },
      { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
      { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'm' },
      { type: 'stream.failed', stream_id: 'g', error: { kind: 'timeout', message: 'Temps esgotat.' } },
      { type: 'stream.failed', stream_id: 'c', error: { kind: 'invalid', message: 'Claude ha declinat.' }, usage: REFUSED },
      { type: 'turn.failed', error, usage: REFUSED },
    ]);
    const l = live(events, 'duel');
    const s = reloaded([
      message({
        id: 1, turn_id: 1, kind: 'question', content: 'Pregunta?', final: true,
        meta: { mode: 'duel', outcome: outcome({ status: 'failed', error, failures, usage: REFUSED }) },
      }),
    ]);
    expect(s.status).toBe('failed');
    expect(s.error).toEqual(error);
    expect(sortedCalls(streamsAnswers(s))).toEqual(sortedCalls(streamsAnswers(l)));
    expect(turnUsage(s)).toEqual(turnUsage(l));
  });
});

function streamsAnswers(turn: TurnView): StreamView[] {
  return turn.streams.filter((s) => s.kind === 'answer');
}
