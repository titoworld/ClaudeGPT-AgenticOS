import { describe, expect, it } from 'vitest';
import {
  applyTurnEvent,
  createLiveTurn,
  latestAgreement,
  mergeTurns,
  revisionRounds,
  streamsByAgent,
  turnUsage,
  turnsFromMessages,
  INCOMPLETE_KIND,
  type TurnView,
} from './turns.svelte';
import { turnCost } from './costs';
import {
  cancelledDebateEvents,
  cancelledDebateMessages,
  compactedDuelEvents,
  compactedDuelMessages,
  debateEvents,
  debateMessages,
  message,
  priced,
  sequence,
  usage,
} from './test-fixtures';
import type { Savings, TurnEvent } from './protocol';

const live = (requestId = 'req-1'): TurnView =>
  createLiveTurn({ requestId, question: 'Pregunta?', mode: 'debate', conversationId: null });

function applyAll(turn: TurnView, events: TurnEvent[]): number {
  return events.filter((e) => applyTurnEvent(turn, e)).length;
}

describe('applyTurnEvent: full debate', () => {
  it('builds rounds, sections, agreement and the final result', () => {
    const turn = live();
    const events = debateEvents();
    expect(applyAll(turn, events)).toBe(events.length);

    expect(turn.status).toBe('done');
    expect(turn.conversationId).toBe(7);
    expect(turn.turnId).toBe(40);
    expect(turn.phase).toBe('synthesis');
    expect(turn.lastSeq).toBe(events.length);

    const answers = streamsByAgent(turn, 'answer', 0);
    expect(answers.claude?.text).toBe('Hola món');
    expect(answers.chatgpt?.text).toBe('Bon dia');
    expect(answers.chatgpt?.usage?.cache_read_tokens).toBe(50);
    expect(answers.claude?.ttftMs).toBe(300);

    const rounds = revisionRounds(turn);
    expect(rounds.map((r) => r.round)).toEqual([1, 2]);
    const r1 = streamsByAgent(turn, 'revision', 1);
    expect(r1.claude?.critique).toBe('- Falta context');
    expect(r1.claude?.text).toBe('Hola món millorat');
    expect(r1.claude?.agreement).toBe(70);
    expect(r1.chatgpt?.unchanged).toBe(true);
    expect(latestAgreement(turn)).toBe(90);

    const synthesis = streamsByAgent(turn, 'synthesis').claude;
    expect(synthesis?.text).toBe('Síntesi final');
    expect(synthesis?.status).toBe('done');

    expect(turn.consensus).toEqual({ reached: true, round: 2, scores: { claude: 92, chatgpt: 88 } });
    expect(turn.savings?.total).toBe(3200);
    expect(turn.finalMessageIds).toEqual([47]);
    expect(turn.cached).toBe(false);
    expect(turnUsage(turn)?.input_tokens).toBe(1250);
  });

  it('tracks the running phase while streaming', () => {
    const turn = live();
    const events = debateEvents();
    applyAll(turn, events.slice(0, 13));
    expect(turn.status).toBe('running');
    expect(turn.phase).toBe('revision');
    expect(turn.round).toBe(1);
    expect(streamsByAgent(turn, 'revision', 1).claude?.status).toBe('streaming');
  });
});

describe('applyTurnEvent: idempotency', () => {
  it('ignores duplicated events', () => {
    const turn = live();
    const events = debateEvents();
    applyAll(turn, events.slice(0, 7));
    const again = applyAll(turn, events.slice(0, 7));
    expect(again).toBe(0);
    expect(streamsByAgent(turn, 'answer').claude?.text).toBe('Hola món');
    expect(turn.streams).toHaveLength(2);
  });

  it('ignores events older than the last applied seq (out of order)', () => {
    const turn = live();
    const events = debateEvents();
    applyAll(turn, events.slice(0, 5)); // up to "Hola "
    expect(applyTurnEvent(turn, events[6]!)).toBe(true); // seq 7 "món" arrives first
    expect(applyTurnEvent(turn, events[5]!)).toBe(false); // seq 6 is now stale
    expect(streamsByAgent(turn, 'answer').claude?.text).toBe('Hola món');
    expect(streamsByAgent(turn, 'answer').chatgpt?.text).toBe('');
  });

  it('replays after a reconnect without duplicating text', () => {
    const turn = live();
    const events = debateEvents();
    applyAll(turn, events.slice(0, 14));
    // turn.subscribe(after_seq = 12) raced with live delivery: the server resends 13.. again.
    applyAll(turn, events.slice(12));
    const r1 = streamsByAgent(turn, 'revision', 1).claude;
    expect(r1?.critique).toBe('- Falta context');
    expect(r1?.text).toBe('Hola món millorat');
    expect(turn.status).toBe('done');
  });

  it('rebuilds a whole turn from a replay starting at seq 1', () => {
    const turn = createLiveTurn({ requestId: 'req-1', question: '', mode: 'solo', conversationId: 7 });
    applyAll(turn, debateEvents());
    expect(turn.mode).toBe('debate');
    expect(turn.streams).toHaveLength(7);
  });

  it('ignores events of another request', () => {
    const turn = live('other');
    expect(applyAll(turn, debateEvents())).toBe(0);
    expect(turn.status).toBe('pending');
  });
});

describe('applyTurnEvent: failures', () => {
  it('marks a single stream as failed and lets the turn continue', () => {
    const turn = createLiveTurn({ requestId: 'r', question: 'q', mode: 'duel' });
    applyAll(
      turn,
      sequence('r', [
        { type: 'turn.started', conversation_id: 1, turn_id: 2, mode: 'duel', new_conversation: false },
        { type: 'stream.started', stream_id: 'a', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
        { type: 'stream.started', stream_id: 'b', agent: 'chatgpt', kind: 'answer', round: 0, model: 'n' },
        { type: 'stream.failed', stream_id: 'b', error: { kind: 'timeout', message: 'Temps esgotat' } },
        { type: 'stream.delta', stream_id: 'a', section: 'text', text: 'ok' },
        {
          type: 'stream.completed', stream_id: 'a', message_id: 3, usage: usage(1, 1), latency_ms: 10, ttft_ms: 5,
          agreement: null, unchanged: false,
        },
        {
          type: 'turn.completed', conversation_id: 1, turn_id: 2, final_message_ids: [3], usage: usage(1, 1),
          savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null }, consensus: null, cached: false,
        },
      ]),
    );
    const byAgent = streamsByAgent(turn, 'answer');
    expect(byAgent.chatgpt?.status).toBe('failed');
    expect(byAgent.chatgpt?.error?.message).toBe('Temps esgotat');
    expect(byAgent.claude?.status).toBe('done');
    expect(turn.status).toBe('done');
  });

  it('interrupts open streams when the turn fails', () => {
    const turn = live();
    const events = debateEvents().slice(0, 6);
    applyAll(turn, events);
    applyTurnEvent(turn, { type: 'turn.failed', request_id: 'req-1', seq: 7, error: { kind: 'x', message: 'Error' } });
    expect(turn.status).toBe('failed');
    expect(turn.error?.message).toBe('Error');
    expect(turn.streams.every((s) => s.status === 'interrupted')).toBe(true);
  });

  it('handles cancellation', () => {
    const turn = live();
    applyAll(turn, debateEvents().slice(0, 4));
    applyTurnEvent(turn, { type: 'turn.cancelled', request_id: 'req-1', seq: 5 });
    expect(turn.status).toBe('cancelled');
    expect(turn.streams.every((s) => s.status === 'interrupted')).toBe(true);
  });
});

describe('applyTurnEvent: cached turn', () => {
  it('flags every stream as coming from the cache', () => {
    const turn = createLiveTurn({ requestId: 'c', question: 'q', mode: 'solo', target: 'chatgpt' });
    applyAll(
      turn,
      sequence('c', [
        { type: 'turn.started', conversation_id: 1, turn_id: 9, mode: 'solo', new_conversation: false },
        { type: 'stream.started', stream_id: 'x', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt' },
        { type: 'stream.delta', stream_id: 'x', section: 'text', text: 'resposta' },
        {
          type: 'stream.completed', stream_id: 'x', message_id: 10, usage: usage(0, 0), latency_ms: 3, ttft_ms: null,
          agreement: null, unchanged: false,
        },
        {
          type: 'turn.completed', conversation_id: 1, turn_id: 9, final_message_ids: [10], usage: usage(0, 0),
          savings: { cache: 812, compaction: 0, early_stop: 0, unchanged: 0, total: 812, cost_usd: null }, consensus: null, cached: true,
        },
      ]),
    );
    expect(turn.cached).toBe(true);
    expect(turn.streams[0]?.cached).toBe(true);
    expect(turn.savings?.cache).toBe(812);
  });
});

describe('turnsFromMessages', () => {
  it('maps a stored debate to the same view as the live one', () => {
    const [stored] = turnsFromMessages(debateMessages(), 7);
    const fromEvents = live();
    applyAll(fromEvents, debateEvents());
    expect(stored).toBeDefined();
    const s = stored!;

    expect(s.mode).toBe('debate');
    expect(s.turnId).toBe(40);
    expect(s.conversationId).toBe(7);
    expect(s.status).toBe('done');
    expect(s.question).toBe('Pregunta?');
    expect(s.options?.debate.rounds).toBe(2);

    // Unchanged revisions may store the kept answer as content, so text is compared separately.
    const pick = (t: TurnView) =>
      t.streams.map((x) => [x.agent, x.kind, x.round, x.critique, x.agreement, x.unchanged, x.status, x.messageId]);
    expect(pick(s)).toEqual(pick(fromEvents));
    expect(streamsByAgent(s, 'synthesis').claude?.text).toBe(streamsByAgent(fromEvents, 'synthesis').claude?.text);
    expect(streamsByAgent(s, 'revision', 1).claude?.critique).toBe('- Falta context');
    expect(s.consensus).toEqual({ reached: true, round: 2, scores: { claude: 92, chatgpt: 88 } });
    expect(s.finalMessageIds).toEqual([47]);
    expect(turnUsage(s)?.input_tokens).toBe(1250);
  });

  it('derives "no consensus" when the last round stays below the threshold', () => {
    const msgs = debateMessages().map((m) =>
      m.id === 46 ? { ...m, meta: { ...m.meta, agreement: 60 } } : m,
    );
    const [t] = turnsFromMessages(msgs);
    expect(t?.consensus).toEqual({ reached: false, round: 2, scores: { claude: 92, chatgpt: 60 } });
  });

  it('reads turn-level savings and consensus from meta when stored', () => {
    const msgs = debateMessages().map((m) =>
      m.id === 47
        ? {
            ...m,
            meta: {
              ...m.meta,
              savings: { cache: 0, compaction: 10, early_stop: 20, unchanged: 30, total: 60, cost_usd: 0.0021 },
              consensus: { reached: false, round: 2, scores: { claude: 50, chatgpt: 60 } },
            },
          }
        : m,
    );
    const [t] = turnsFromMessages(msgs);
    expect(t?.savings?.total).toBe(60);
    expect(t?.savings?.cost_usd).toBe(0.0021);
    expect(t?.consensus?.reached).toBe(false);
  });

  it('reads the cost basis of each answer and tolerates savings without a value', () => {
    // Stored before savings carried a value.
    const legacySavings = { cache: 5, compaction: 0, early_stop: 0, unchanged: 0, total: 5 } as unknown as Savings;
    const msgs = [
      message({ id: 1, turn_id: 1, kind: 'question', content: 'a', meta: { mode: 'duel' } }),
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'claude', meta: { usage: usage(1, 1), cost_basis: 'api' } }),
      message({
        id: 3, turn_id: 1, kind: 'answer', agent: 'chatgpt', final: true,
        meta: { usage: usage(1, 1), cost_basis: 'equivalent', savings: legacySavings },
      }),
    ];
    const [t] = turnsFromMessages(msgs);
    expect(t?.streams.map((s) => s.costBasis)).toEqual(['api', 'equivalent']);
    expect(t?.savings).toMatchObject({ total: 5, cost_usd: null });
  });

  it('groups several turns, infers modes and flags cached answers', () => {
    const msgs = [
      message({ id: 1, turn_id: 1, kind: 'question', content: 'a', meta: { mode: 'solo', target: 'chatgpt' } }),
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'chatgpt', content: 'A', final: true, meta: { cached: true } }),
      message({ id: 3, turn_id: 3, kind: 'question', content: 'b' }),
      message({ id: 4, turn_id: 3, kind: 'answer', agent: 'claude', content: 'B1', final: true }),
      message({ id: 5, turn_id: 3, kind: 'answer', agent: 'chatgpt', content: 'B2', final: true }),
      message({ id: 6, turn_id: 6, kind: 'question', content: 'c', meta: { mode: 'duel' } }),
    ];
    const turns = turnsFromMessages(msgs, 2);
    expect(turns.map((t) => t.mode)).toEqual(['solo', 'duel', 'duel']);
    expect(turns[0]?.target).toBe('chatgpt');
    expect(turns[0]?.cached).toBe(true);
    expect(turns[0]?.streams[0]?.cached).toBe(true);
    expect(turns[1]?.cached).toBe(false);
    expect(turns[2]?.status).toBe('failed');
  });
});

describe('turnsFromMessages: turn totals after a reload (F1)', () => {
  const liveTurn = () => {
    const t = createLiveTurn({ requestId: 'req-c', question: 'I ara?', mode: 'duel', conversationId: 3 });
    applyAll(t, compactedDuelEvents());
    return t;
  };

  it('adds the compaction summary call, as the live total does', () => {
    const live = liveTurn();
    const [stored] = turnsFromMessages(compactedDuelMessages(), 3);
    const a = turnUsage(live)!;
    const b = turnUsage(stored!)!;
    expect([b.input_tokens, b.output_tokens]).toEqual([a.input_tokens, a.output_tokens]);
    expect(b.cost_usd).toBeCloseTo(a.cost_usd!, 12);
    expect(turnCost(stored!).totalUsd).toBeCloseTo(turnCost(live).totalUsd!, 12);
    // The split between API cost and subscription value still comes from the answers.
    expect(turnCost(stored!).equivalentUsd).toBeCloseTo(0.003063 + 0.003198, 12);
  });

  it('prefers a stored turn total and never adds the summary twice', () => {
    const total = priced(2000, 300, 0.01);
    const msgs = compactedDuelMessages().map((m) => (m.id === 11 ? { ...m, meta: { ...m.meta, turn_usage: total } } : m));
    const [t] = turnsFromMessages(msgs);
    expect(turnUsage(t!)).toEqual(total);
  });

  it('keeps summing the answers when there was no compaction', () => {
    const msgs = compactedDuelMessages().map((m) => (m.kind === 'question' ? { ...m, meta: { mode: 'duel' as const } } : m));
    const [t] = turnsFromMessages(msgs);
    expect(t!.usage).toBeNull();
    expect(turnUsage(t!)).toMatchObject({ input_tokens: 912, output_tokens: 235 });
  });
});

describe('turnsFromMessages: debates that did not finish (F4)', () => {
  it('a debate stored without a synthesis is incomplete, not done', () => {
    const [t] = turnsFromMessages(cancelledDebateMessages(), 5);
    expect(t!.status).toBe('failed');
    expect(t!.error).toEqual({ kind: INCOMPLETE_KIND, message: 'Aquest torn no es va completar.' });
    // Same phase and round the live turn had when it stopped.
    const live = createLiveTurn({ requestId: 'req-x', question: 'Debat llarg', mode: 'debate', conversationId: 5 });
    applyAll(live, cancelledDebateEvents());
    expect([t!.phase, t!.round]).toEqual([live.phase, live.round]);
  });

  it('a debate stopped while answering is incomplete too', () => {
    const msgs = cancelledDebateMessages().filter((m) => m.kind !== 'revision');
    expect(turnsFromMessages(msgs)[0]!.status).toBe('failed');
  });

  it('a debate with its synthesis, and solo and duel turns with answers, are done', () => {
    expect(turnsFromMessages(debateMessages())[0]!.status).toBe('done');
    expect(turnsFromMessages(compactedDuelMessages())[0]!.status).toBe('done');
  });
});

describe('mergeTurns', () => {
  it('replaces stored turns by their live version and keeps order', () => {
    const stored = turnsFromMessages([
      message({ id: 1, turn_id: 1, kind: 'question', content: 'a' }),
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'claude', content: 'A' }),
      message({ id: 3, turn_id: 3, kind: 'question', content: 'b' }),
    ]);
    const liveOld = createLiveTurn({ requestId: 'x', question: 'b', mode: 'solo', conversationId: 1 });
    liveOld.turnId = 3;
    const pending = createLiveTurn({ requestId: 'y', question: 'c', mode: 'solo', conversationId: 1 });
    const merged = mergeTurns(stored, [pending, liveOld]);
    expect(merged.map((t) => t.key)).toEqual(['t1', 'x', 'y']);
  });
});
