import { describe, expect, it } from 'vitest';
import {
  applyTurnEvent,
  createLiveTurn,
  keptAnswers,
  latestAgreement,
  mergeTurns,
  pdfChecks,
  revisionRounds,
  shownSynthesis,
  streamsByAgent,
  synthesisAttempts,
  synthesisNote,
  turnUsage,
  turnsFromMessages,
  INCOMPLETE_KIND,
  type StreamView,
  type TurnView,
} from './turns.svelte';
import { turnCost } from './costs';
import type { Attachment, PdfReading } from './protocol';
import {
  cancelledDebateEvents,
  cancelledDebateMessages,
  compactedDuelEvents,
  compactedDuelMessages,
  debateEvents,
  debateMessages,
  degradedSynthesisEvents,
  fallbackSynthesisEvents,
  keptRevisionEvents,
  keptRevisionMessages,
  message,
  oneSurvivorDebateEvents,
  priced,
  QUICK_DEBATE,
  quickDebateMessages,
  retriedDuelEvents,
  retriedDuelMessages,
  sequence,
  usage,
} from './test-fixtures';
import type { Message, MessageMeta, Savings, TurnEvent } from './protocol';

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
    applyTurnEvent(turn, { type: 'turn.failed', request_id: 'req-1', seq: 7, error: { kind: 'x', message: 'Error' }, usage: usage(0, 0) });
    expect(turn.status).toBe('failed');
    expect(turn.error?.message).toBe('Error');
    expect(turn.streams.every((s) => s.status === 'interrupted')).toBe(true);
  });

  it('handles cancellation', () => {
    const turn = live();
    applyAll(turn, debateEvents().slice(0, 4));
    applyTurnEvent(turn, { type: 'turn.cancelled', request_id: 'req-1', seq: 5, usage: usage(0, 0) });
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
    // Both agents answered by subscription, so the summary, whichever wrote it, was
    // subscription value too: the split covers the whole total (A8).
    expect(turnCost(stored!)).toMatchObject({ apiUsd: 0, otherUsd: 0 });
    expect(turnCost(stored!).equivalentUsd).toBeCloseTo(0.003063 + 0.003198 + 0.003114, 12);
  });

  it('knows the turn compacted the history, live and after a reload', () => {
    expect(liveTurn().compacted).toBe(true);
    expect(turnsFromMessages(compactedDuelMessages(), 3)[0]!.compacted).toBe(true);
    const plain = live();
    applyAll(plain, debateEvents());
    expect(plain.compacted).toBe(false);
    expect(turnsFromMessages(debateMessages())[0]!.compacted).toBe(false);
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

describe('turnsFromMessages: billed calls that stored no message (K16)', () => {
  const liveTurn = () => {
    const t = createLiveTurn({ requestId: 'req-r', question: 'I ara?', mode: 'duel', conversationId: 4 });
    applyAll(t, retriedDuelEvents());
    return t;
  };

  it('adds the unstored usage of the last final message, as the live total does', () => {
    const live = liveTurn();
    const [stored] = turnsFromMessages(retriedDuelMessages(), 4);
    const a = turnUsage(live)!;
    const b = turnUsage(stored!)!;
    expect([b.input_tokens, b.output_tokens]).toEqual([a.input_tokens, a.output_tokens]);
    expect([b.input_tokens, b.output_tokens]).toEqual([1692, 256]);
    expect(b.cost_usd).toBeCloseTo(a.cost_usd!, 12);
    expect(turnCost(stored!).totalUsd).toBeCloseTo(turnCost(live).totalUsd!, 12);
  });

  it('counts the running total once, whatever the order of the final messages', () => {
    const msgs = retriedDuelMessages();
    const [t] = turnsFromMessages([msgs[0]!, msgs[2]!, msgs[1]!]);
    expect(turnUsage(t!)).toMatchObject({ input_tokens: 1692, output_tokens: 256 });
    // An earlier final message with a smaller running total, a non-final one with any.
    const extra = message({
      id: 33, turn_id: 30, kind: 'revision', agent: 'claude', round: 1,
      meta: { usage: usage(1, 1), unstored_usage: priced(9999, 9999, 1) },
    });
    const [u] = turnsFromMessages([...msgs, extra]);
    expect(turnUsage(u!)).toMatchObject({ input_tokens: 1693, output_tokens: 257 });
  });

  it('adds it on top of the compaction summary', () => {
    const summary = priced(768, 54, 0.003114);
    const msgs = retriedDuelMessages().map((m) =>
      m.kind === 'question' ? { ...m, meta: { ...m.meta, compaction_usage: summary } } : m,
    );
    const [t] = turnsFromMessages(msgs);
    const total = turnUsage(t!)!;
    expect([total.input_tokens, total.output_tokens]).toEqual([1692 + 768, 256 + 54]);
    expect(total.cost_usd).toBeCloseTo(0.008681 + 0.003114, 12);
  });

  it('is left out of cached replays, which carry none', () => {
    const msgs = retriedDuelMessages().map((m) => {
      const meta = { ...m.meta };
      delete meta.unstored_usage;
      return { ...m, meta };
    });
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

// ------------------------------------------------ synthesis attempts (A1)

/** A debate without revision rounds (synthesizer: Claude), as the live events build it. */
function liveDebate(events: TurnEvent[]): TurnView {
  const t = createLiveTurn({ requestId: events[0]!.request_id, question: 'Pregunta?', mode: 'debate', options: QUICK_DEBATE, conversationId: 8 });
  applyAll(t, events);
  return t;
}

describe('shownSynthesis: the synthesis a debate shows (A1)', () => {
  const upTo = (events: TurnEvent[], match: (e: TurnEvent) => boolean): TurnEvent[] => events.slice(0, events.findIndex(match));
  const view = (s: StreamView | null) => (s ? { id: s.id, agent: s.agent, status: s.status, messageId: s.messageId, text: s.text } : null);
  const same = (s: StreamView | null) => (s ? [s.agent, s.kind, s.round, s.status, s.messageId, s.text] : null);

  it("fallback: the other agent's synthesis (the final message), not the failed first attempt", () => {
    const t = liveDebate(fallbackSynthesisEvents());
    expect(synthesisAttempts(t).map((s) => `${s.id}:${s.agent}:${s.status}`)).toEqual(['s1:claude:failed', 's2:chatgpt:done']);
    expect(t.finalMessageIds).toEqual([63]);
    expect(view(shownSynthesis(t))).toEqual({ id: 's2', agent: 'chatgpt', status: 'done', messageId: 63, text: 'Síntesi de ChatGPT' });
  });

  it('degraded: the answer stored as final after both attempts failed', () => {
    const t = liveDebate(degradedSynthesisEvents());
    expect(synthesisAttempts(t)).toHaveLength(3);
    expect(view(shownSynthesis(t))).toEqual({ id: 's3', agent: 'claude', status: 'done', messageId: 63, text: 'Resposta de Claude' });
  });

  it('while the fallback attempt runs, the attempt in progress', () => {
    const events = fallbackSynthesisEvents();
    const streaming = liveDebate(upTo(events, (e) => e.type === 'stream.completed' && e.stream_id === 's2'));
    expect(view(shownSynthesis(streaming))).toEqual({ id: 's2', agent: 'chatgpt', status: 'streaming', messageId: null, text: 'Síntesi de ChatGPT' });
    // Done but before turn.completed (no final ids yet): the latest finished attempt.
    const done = liveDebate(upTo(events, (e) => e.type === 'turn.completed'));
    expect(done.finalMessageIds).toEqual([]);
    expect(shownSynthesis(done)?.id).toBe('s2');
    // Right after the first attempt failed, it is the only one there is.
    const failed = liveDebate(upTo(events, (e) => e.type === 'stream.started' && e.stream_id === 's2'));
    expect(shownSynthesis(failed)?.id).toBe('s1');
  });

  it('prefers the final message over any later attempt', () => {
    const t = liveDebate(fallbackSynthesisEvents());
    t.streams.push({ ...t.streams.at(-1)!, id: 'late', messageId: 99, text: 'Una altra' });
    expect(shownSynthesis(t)?.id).toBe('s2');
  });

  it('a debate without synthesis shows none; a normal one shows its only attempt', () => {
    expect(shownSynthesis(liveDebate(upTo(fallbackSynthesisEvents(), (e) => e.type === 'stream.started' && e.stream_id === 's1')))).toBeNull();
    const t = live();
    applyAll(t, debateEvents());
    expect(view(shownSynthesis(t))).toMatchObject({ agent: 'claude', status: 'done', messageId: 47, text: 'Síntesi final' });
  });

  it('shows the same synthesis live and after a reload', () => {
    const cases: [TurnEvent[], Message[]][] = [
      [fallbackSynthesisEvents(), quickDebateMessages({ agent: 'chatgpt', content: 'Síntesi de ChatGPT' })],
      [degradedSynthesisEvents(), quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true })],
      [oneSurvivorDebateEvents(), quickDebateMessages({ agent: 'chatgpt', content: 'Resposta de ChatGPT', degraded: true }, ['chatgpt'])],
    ];
    for (const [events, messages] of cases) {
      const l = liveDebate(events);
      const [stored] = turnsFromMessages(messages, 8);
      expect(same(shownSynthesis(stored!))).toEqual(same(shownSynthesis(l)));
      expect(synthesisNote(stored!)).toBe(synthesisNote(l));
      expect(stored!.status).toBe(l.status);
    }
  });
});

describe("synthesisNote: when the synthesis is not the chosen synthesizer's (A1)", () => {

  it('says who made it after the synthesizer failed', () => {
    const events = fallbackSynthesisEvents();
    expect(synthesisNote(liveDebate(events))).toBe("Claude no ha pogut fer la síntesi; l'ha feta ChatGPT.");
    const streaming = events.slice(0, events.findIndex((e) => e.type === 'stream.completed' && e.stream_id === 's2'));
    expect(synthesisNote(liveDebate(streaming))).toBe('Claude no ha pogut fer la síntesi; ara la fa ChatGPT.');
    const [stored] = turnsFromMessages(quickDebateMessages({ agent: 'chatgpt', content: 'Síntesi de ChatGPT' }));
    expect(synthesisNote(stored!)).toBe("Claude no ha pogut fer la síntesi; l'ha feta ChatGPT.");
  });

  it('says that nobody could synthesize when an answer was kept instead', () => {
    expect(synthesisNote(liveDebate(degradedSynthesisEvents()))).toBe(
      "No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.",
    );
    // An agent failed its first answer: the partner's answer is final, no synthesis is attempted.
    expect(synthesisNote(liveDebate(oneSurvivorDebateEvents()))).toBe(
      "No s'ha pogut fer la síntesi: es mostra l'última resposta de ChatGPT.",
    );
    const [stored] = turnsFromMessages(quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true }));
    expect(stored!.streams.at(-1)?.degraded).toBe(true);
    expect(synthesisNote(stored!)).toBe("No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.");
  });

  it('says nothing when the chosen synthesizer made it', () => {
    const t = live();
    applyAll(t, debateEvents());
    expect(synthesisNote(t)).toBeNull();
    expect(synthesisNote(turnsFromMessages(debateMessages())[0]!)).toBeNull();
    const [byChatgpt] = turnsFromMessages(
      quickDebateMessages({ agent: 'chatgpt', content: 'Síntesi' }).map((m) =>
        m.kind === 'question' ? { ...m, meta: { ...m.meta, options: { ...QUICK_DEBATE, debate: { ...QUICK_DEBATE.debate, synthesizer: 'chatgpt' as const } } } } : m,
      ),
    );
    expect(synthesisNote(byChatgpt!)).toBeNull();
  });
});

// ------------------------------------------- truncated answers (N4, N14, A10)

describe('truncated answers', () => {
  const soloEvents = (completed: Record<string, unknown>) =>
    sequence('t', [
      { type: 'turn.started', conversation_id: 1, turn_id: 70, mode: 'solo', new_conversation: false },
      { type: 'stream.started', stream_id: 'x', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
      { type: 'stream.delta', stream_id: 'x', section: 'text', text: 'Resposta a mitja' },
      {
        type: 'stream.completed', stream_id: 'x', message_id: 71, usage: usage(10, 8000), latency_ms: 9, ttft_ms: 1,
        agreement: null, unchanged: false, ...completed,
      },
    ]);

  it('flags the stream the live event marks as truncated', () => {
    const t = createLiveTurn({ requestId: 't', question: 'q', mode: 'solo', target: 'claude' });
    applyAll(t, soloEvents({ truncated: true }));
    expect(t.streams[0]).toMatchObject({ status: 'done', truncated: true, finishReason: null, text: 'Resposta a mitja' });

    const complete = createLiveTurn({ requestId: 't', question: 'q', mode: 'solo', target: 'claude' });
    applyAll(complete, soloEvents({})); // omitted when false
    expect(complete.streams[0]?.truncated).toBe(false);
  });

  it('takes the reason from the live event, as a reload takes it from the meta', () => {
    const t = createLiveTurn({ requestId: 't', question: 'q', mode: 'solo', target: 'claude' });
    applyAll(t, soloEvents({ truncated: true, finish_reason: ' max_tokens ' }));
    expect(t.streams[0]).toMatchObject({ truncated: true, finishReason: 'max_tokens' });

    const odd = createLiveTurn({ requestId: 't', question: 'q', mode: 'solo', target: 'claude' });
    applyAll(odd, soloEvents({ truncated: true, finish_reason: 7 }));
    expect(odd.streams[0]?.finishReason).toBeNull();
  });

  it('keeps the note of an unchanged revision from the live event', () => {
    const revision = (fields: Record<string, unknown>) =>
      sequence('u', [
        { type: 'turn.started', conversation_id: 1, turn_id: 80, mode: 'debate', new_conversation: false },
        { type: 'stream.started', stream_id: 'r', agent: 'chatgpt', kind: 'revision', round: 1, model: 'm' },
        {
          type: 'stream.completed', stream_id: 'r', message_id: 81, usage: usage(5, 5), latency_ms: 1, ttft_ms: 1,
          agreement: 90, unchanged: false, ...fields,
        },
      ]);
    const kept = createLiveTurn({ requestId: 'u', question: 'q', mode: 'debate' });
    applyAll(kept, revision({ unchanged: true, unchanged_note: ' ja ho cobria ' }));
    expect(kept.streams[0]).toMatchObject({ unchanged: true, unchangedNote: 'ja ho cobria' });

    const rewritten = createLiveTurn({ requestId: 'u', question: 'q', mode: 'debate' });
    applyAll(rewritten, revision({ unchanged: false, unchanged_note: 'nota' }));
    expect(rewritten.streams[0]?.unchangedNote).toBeNull();
  });

  it('reads the flag and the reason from the stored meta after a reload', () => {
    const [t] = turnsFromMessages([
      message({ id: 70, turn_id: 70, kind: 'question', content: 'q', final: true, meta: { mode: 'solo', target: 'claude' } }),
      message({
        id: 71, turn_id: 70, kind: 'answer', agent: 'claude', content: 'Resposta a mitja', final: true,
        meta: { model: 'm', usage: usage(10, 8000), truncated: true, finish_reason: 'max_tokens' },
      }),
    ]);
    expect(t!.streams[0]).toMatchObject({ truncated: true, finishReason: 'max_tokens' });
  });

  it('takes only a real true as truncated', () => {
    const malformed = { truncated: 'yes', finish_reason: 7 } as unknown as MessageMeta;
    const [t] = turnsFromMessages([
      message({ id: 1, turn_id: 1, kind: 'answer', agent: 'claude', content: 'a', final: true, meta: malformed }),
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'chatgpt', content: 'b', final: true, meta: {} }),
    ]);
    expect(t!.streams.map((s) => [s.truncated, s.finishReason])).toEqual([
      [false, null],
      [false, null],
    ]);
  });
});

describe('unchanged revisions with a note (N2)', () => {
  it('keeps the note of an unchanged revision from the stored meta', () => {
    const msgs = debateMessages().map((m) =>
      m.id === 44 ? { ...m, meta: { ...m.meta, unchanged_note: '  my previous answer already covers this ' } } : m,
    );
    const [t] = turnsFromMessages(msgs);
    const r1 = streamsByAgent(t!, 'revision', 1);
    expect(r1.chatgpt).toMatchObject({ unchanged: true, unchangedNote: 'my previous answer already covers this' });
    expect(r1.claude?.unchangedNote).toBeNull();
  });

  it('ignores a note on a revision that changed, and empty notes', () => {
    const msgs = debateMessages().map((m) =>
      m.id === 43 ? { ...m, meta: { ...m.meta, unchanged_note: 'nota' } } : m.id === 45 ? { ...m, meta: { ...m.meta, unchanged_note: '  ' } } : m,
    );
    const [t] = turnsFromMessages(msgs);
    expect(streamsByAgent(t!, 'revision', 1).claude?.unchangedNote).toBeNull();
    expect(streamsByAgent(t!, 'revision', 2).claude?.unchangedNote).toBeNull();
  });
});

// -------------------------- revisions that keep the previous answer (review)

describe('keptAnswers: revisions that keep the previous answer', () => {
  /** The map as "agent round" of each revision -> "agent round" of the stream that wrote its answer. */
  function describeKept(turn: TurnView): Record<string, string | null> {
    const name = (s: StreamView) => `${s.agent} ${s.round}`;
    const byId = new Map(turn.streams.map((s) => [s.id, s]));
    return Object.fromEntries([...keptAnswers(turn)].map(([id, from]) => [name(byId.get(id)!), from && name(from)]));
  }

  const liveTurn = (events: TurnEvent[]): TurnView => {
    const t = live(events[0]!.request_id);
    applyAll(t, events);
    return t;
  };

  it('maps each unchanged revision to the stream that wrote the answer it keeps', () => {
    const [stored] = turnsFromMessages(debateMessages());
    // Claude revised in round 1 and kept that revision in round 2; ChatGPT kept its first answer twice.
    const expected = { 'chatgpt 1': 'chatgpt 0', 'claude 2': 'claude 1', 'chatgpt 2': 'chatgpt 0' };
    expect(describeKept(stored!)).toEqual(expected);
    expect(describeKept(liveTurn(debateEvents()))).toEqual(expected);
  });

  it('includes a revision cut off before its answer, which stores the previous answer again', () => {
    const [stored] = turnsFromMessages(keptRevisionMessages());
    const expected = { 'claude 1': 'claude 0', 'chatgpt 1': 'chatgpt 0' };
    expect(describeKept(stored!)).toEqual(expected);
    expect(streamsByAgent(stored!, 'revision', 1).claude).toMatchObject({ unchanged: false, truncated: true });
    // Live, the engine streams the kept answer again (without the first answer's final line break).
    expect(describeKept(liveTurn(keptRevisionEvents()))).toEqual(expected);
  });

  it('leaves out revisions that wrote a new answer, even one that was cut off', () => {
    const msgs = keptRevisionMessages().map((m) => (m.id === 83 ? { ...m, content: 'Hola món, amb més con' } : m));
    const [t] = turnsFromMessages(msgs);
    expect(describeKept(t!)).toEqual({ 'chatgpt 1': 'chatgpt 0' });
  });

  it('skips a failed revision: the next one keeps the answer from before it', () => {
    const events = sequence('kf', [
      { type: 'turn.started', conversation_id: 1, turn_id: 1, mode: 'debate', new_conversation: false },
      { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
      { type: 'stream.delta', stream_id: 'c0', section: 'text', text: 'Primera' },
      { type: 'stream.completed', stream_id: 'c0', message_id: 2, usage: usage(1, 1), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
      { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'm' },
      { type: 'stream.delta', stream_id: 'c1', section: 'answer', text: 'Primera' },
      { type: 'stream.failed', stream_id: 'c1', error: { kind: 'timeout', message: 'Temps esgotat' } },
      { type: 'stream.started', stream_id: 'c2', agent: 'claude', kind: 'revision', round: 2, model: 'm' },
      { type: 'stream.delta', stream_id: 'c2', section: 'answer', text: 'Primera' },
      { type: 'stream.completed', stream_id: 'c2', message_id: 3, usage: usage(1, 1), latency_ms: 1, ttft_ms: 1, agreement: 60, unchanged: false },
    ]);
    expect(describeKept(liveTurn(events))).toEqual({ 'claude 2': 'claude 0' });
  });

  it('waits for a revision to finish: a new answer may begin with the previous one', () => {
    const events = sequence('kw', [
      { type: 'turn.started', conversation_id: 1, turn_id: 1, mode: 'debate', new_conversation: false },
      { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
      { type: 'stream.delta', stream_id: 'c0', section: 'text', text: 'Primera' },
      { type: 'stream.completed', stream_id: 'c0', message_id: 2, usage: usage(1, 1), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
      { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'm' },
      { type: 'stream.delta', stream_id: 'c1', section: 'answer', text: 'Primera\n\n' },
      { type: 'stream.delta', stream_id: 'c1', section: 'answer', text: 'I una millora.' },
      { type: 'stream.completed', stream_id: 'c1', message_id: 3, usage: usage(1, 1), latency_ms: 1, ttft_ms: 1, agreement: 60, unchanged: false },
    ]);
    // While it streams, its text is the previous answer for a moment.
    expect(describeKept(liveTurn(events.slice(0, 6)))).toEqual({});
    expect(describeKept(liveTurn(events))).toEqual({});
  });

  it('is empty for turns without revisions', () => {
    expect(keptAnswers(liveTurn(fallbackSynthesisEvents())).size).toBe(0);
    expect(keptAnswers(turnsFromMessages(compactedDuelMessages())[0]!).size).toBe(0);
  });
});

describe('the attachments of a question (docs/PROTOCOL.md "Attachments")', () => {
  const attachment = (id: number, partial: Partial<Attachment> = {}): Attachment => ({
    id,
    name: `fitxer-${id}.png`,
    kind: 'image',
    mime: 'image/png',
    size: 1000 * id,
    pages: null,
    width: 640,
    height: 480,
    sha256: String(id).repeat(64).slice(0, 64),
    created_at: '2026-09-28T10:00:00Z',
    has_thumbnail: true,
    text_available: false,
    estimated_tokens: 414,
    pdf_notes: null,
    ...partial,
  });

  it('a live turn keeps the ones it was sent with', () => {
    const turn = createLiveTurn({ requestId: 'r1', question: 'Què és?', mode: 'solo', attachments: [attachment(2), attachment(1)] });
    expect(turn.attachments.map((a) => a.id)).toEqual([2, 1]);
    expect(createLiveTurn({ requestId: 'r2', question: 'Hola', mode: 'solo' }).attachments).toEqual([]);
  });

  it('a stored turn reads them from its question, in order', () => {
    const pdf = attachment(7, { name: 'informe.pdf', kind: 'pdf', mime: 'application/pdf', pages: 12, width: null, height: null, text_available: true, estimated_tokens: 43_200 });
    const [turn] = turnsFromMessages([
      message({ id: 1, turn_id: 1, kind: 'question', content: 'Resumeix-ho', final: true, meta: { mode: 'solo', target: 'claude', attachments: [pdf, attachment(3)] } }),
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'claude', content: 'Resum', final: true, meta: {} }),
    ]);
    expect(turn!.attachments).toEqual([pdf, attachment(3)]);
  });

  it('skips entries it cannot use, and a question without any has none', () => {
    const [withBad, without] = turnsFromMessages([
      message({ id: 1, turn_id: 1, kind: 'question', content: 'A', final: true, meta: { mode: 'solo', attachments: [attachment(4), { id: 'x' }, null, { ...attachment(5), kind: 'video' }, { ...attachment(6), id: 0 }] as unknown as Attachment[] } }),
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'claude', content: 'B', final: true }),
      message({ id: 3, turn_id: 2, kind: 'question', content: 'C', final: true, meta: { mode: 'solo', attachments: 'no' as unknown as Attachment[] } }),
      message({ id: 4, turn_id: 2, kind: 'answer', agent: 'claude', content: 'D', final: true }),
    ]);
    expect(withBad!.attachments.map((a) => a.id)).toEqual([4]);
    expect(without!.attachments).toEqual([]);
  });

  it("a stored PDF keeps the warnings of its pages; one stored before the analysis has none", () => {
    const pdf = (id: number, pdf_notes: unknown) =>
      ({ ...attachment(id), name: `doc-${id}.pdf`, kind: 'pdf', mime: 'application/pdf', pages: 9, pdf_notes }) as Attachment;
    const { pdf_notes: _dropped, ...before } = pdf(4, null); // stored before P7b: no key at all
    const [turn] = turnsFromMessages([
      message({
        id: 1, turn_id: 1, kind: 'question', content: 'Llegeix-los', final: true,
        meta: {
          mode: 'solo',
          attachments: [
            pdf(1, { no_text: [2, 5], garbled: [3], hidden: [7] }),
            pdf(2, null),
            before as Attachment,
            pdf(3, { no_text: [1, 'x', 0, 2.5, 4], garbled: 'no', hidden: null }),
            { ...attachment(5), pdf_notes: { no_text: [1], garbled: [], hidden: [] } },
          ],
        },
      }),
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'claude', content: 'Fet', final: true }),
    ]);
    expect(turn!.attachments.map((a) => a.pdf_notes)).toEqual([
      { no_text: [2, 5], garbled: [3], hidden: [7] },
      null,
      null,
      { no_text: [1, 4], garbled: [], hidden: [] },
      null, // an image never has any
    ]);
  });
});

// ------------------------------------------------ Claude's check of the PDFs for ChatGPT (P7b)

const CHECK_USAGE = { ...priced(9000, 700, 0.0123), cache_read_tokens: 2000 };

/** A duel whose question has two PDFs: Claude answers while it checks them for ChatGPT. */
function checkedDuelEvents(requestId = 'rc'): TurnEvent[] {
  return sequence(requestId, [
    { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'claude-opus' },
    {
      type: 'pdf.check', attachment_id: 7, name: 'informe.pdf', state: 'checking',
      claude_pages: [], hidden_pages: [], unchecked_pages: [], reused: false, usage: null, reason: null,
    },
    // The second one was checked in an earlier turn: no call, no «checking».
    {
      type: 'pdf.check', attachment_id: 8, name: 'annex.pdf', state: 'checked',
      claude_pages: [], hidden_pages: [], unchecked_pages: [], reused: true, usage: null, reason: null,
    },
    {
      type: 'pdf.check', attachment_id: 7, name: 'informe.pdf', state: 'checked',
      claude_pages: [2, 5], hidden_pages: [5], unchecked_pages: [], reused: false, usage: CHECK_USAGE, reason: null,
    },
    { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
  ]);
}

const pdfAttachment = (id: number, name: string): Attachment => ({
  id,
  name,
  kind: 'pdf',
  mime: 'application/pdf',
  size: 120_000,
  pages: 12,
  width: null,
  height: null,
  sha256: String(id).repeat(64).slice(0, 64),
  created_at: '2026-09-29T10:00:00Z',
  has_thumbnail: false,
  text_available: true,
  estimated_tokens: 43_200,
  pdf_notes: { no_text: [], garbled: [], hidden: [] },
});

describe("Claude's check of the PDFs for ChatGPT (pdf.check)", () => {
  it('follows each PDF from checking to checked, with what its calls cost', () => {
    const turn = live('rc');
    const events = checkedDuelEvents();
    applyAll(turn, events.slice(0, 4));
    expect(turn.pdfChecks).toEqual([
      {
        attachmentId: 7, name: 'informe.pdf', state: 'checking', claudePages: [], hiddenPages: [], uncheckedPages: [],
        reused: false, usage: null, reason: null, costBasis: null,
      },
    ]);
    applyAll(turn, events.slice(4));
    expect(turn.pdfChecks).toEqual([
      {
        attachmentId: 7, name: 'informe.pdf', state: 'checked', claudePages: [2, 5], hiddenPages: [5], uncheckedPages: [],
        reused: false, usage: CHECK_USAGE, reason: null, costBasis: null,
      },
      {
        attachmentId: 8, name: 'annex.pdf', state: 'checked', claudePages: [], hiddenPages: [], uncheckedPages: [],
        reused: true, usage: null, reason: null, costBasis: null,
      },
    ]);
  });

  it('a PDF nobody could check says why', () => {
    const turn = live('ru');
    applyAll(turn, sequence('ru', [
      { type: 'turn.started', conversation_id: 3, turn_id: 13, mode: 'solo', new_conversation: false },
      {
        type: 'pdf.check', attachment_id: 7, name: 'informe.pdf', state: 'unchecked', claude_pages: [], hidden_pages: [],
        unchecked_pages: [1, 2, 3], reused: false, usage: null, reason: 'Claude no està disponible per contrastar-lo.',
      },
    ]));
    expect(turn.pdfChecks).toMatchObject([
      { attachmentId: 7, state: 'unchecked', uncheckedPages: [1, 2, 3], reason: 'Claude no està disponible per contrastar-lo.' },
    ]);
  });

  it('a PDF that no call checks gets only its end, and that end is enough', () => {
    // No «checking» first: the server could not analyse its pages, or the turn's time ran out
    // while the PDF still waited for a check slot (protocol.ts, the `pdf.check` event).
    const turn = live('rl');
    const unchecked = { type: 'pdf.check' as const, state: 'unchecked' as const, claude_pages: [], hidden_pages: [], reused: false };
    applyAll(turn, sequence('rl', [
      { type: 'turn.started', conversation_id: 3, turn_id: 13, mode: 'duel', new_conversation: false },
      {
        ...unchecked, attachment_id: 7, name: 'informe.pdf', unchecked_pages: [1, 2, 3], usage: priced(0, 0, 0),
        reason: "El servidor no n'ha pogut analitzar les pàgines.",
      },
      {
        ...unchecked, attachment_id: 8, name: 'annex.pdf', unchecked_pages: [1, 2], usage: priced(0, 0, 0),
        reason: 'La comprovació de Claude ha trigat massa.',
      },
    ]));
    expect(turn.pdfChecks).toMatchObject([
      { attachmentId: 7, state: 'unchecked', uncheckedPages: [1, 2, 3], reason: "El servidor no n'ha pogut analitzar les pàgines." },
      { attachmentId: 8, state: 'unchecked', uncheckedPages: [1, 2], reason: 'La comprovació de Claude ha trigat massa.' },
    ]);
  });

  // The server sends no end for a check that a cancelled or failed turn stopped (its task stops
  // with the turn): the turn's end settles it, so it never looks as if Claude still worked on it.
  for (const end of [
    { type: 'turn.cancelled', usage: CHECK_USAGE },
    { type: 'turn.failed', error: { kind: 'internal', message: 'Error intern.' }, usage: CHECK_USAGE },
  ] as const) {
    it(`a check still running when the turn ends (${end.type}) stopped with it`, () => {
      const turn = live('rs');
      const events = checkedDuelEvents('rs');
      // Claude was checking «informe.pdf», and «annex.pdf» had been checked before.
      applyAll(turn, [...events.slice(0, 5), { ...end, request_id: 'rs', seq: 6 }]);
      expect(turn.pdfChecks.map((c) => [c.name, c.state])).toEqual([
        ['informe.pdf', 'interrupted'],
        ['annex.pdf', 'checked'],
      ]);
      // Nothing it billed is known by itself: the turn's total has it.
      expect(turn.pdfChecks[0]).toMatchObject({ usage: null, claudePages: [], reason: null });
    });
  }

  it('a turn that completes with a check still running settles it too', () => {
    const turn = live('rs');
    const savings = { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null };
    applyAll(turn, [
      ...checkedDuelEvents('rs').slice(0, 4),
      {
        type: 'turn.completed', request_id: 'rs', seq: 5, conversation_id: 3, turn_id: 12, final_message_ids: [],
        usage: CHECK_USAGE, savings, consensus: null, cached: false,
      },
    ]);
    expect(turn.pdfChecks.map((c) => c.state)).toEqual(['interrupted']);
  });

  it('shows them in the order of the attachments, whatever order they ended in', () => {
    const turn = createLiveTurn({
      requestId: 'rc', question: 'Compara-ho', mode: 'duel', conversationId: 3,
      attachments: [pdfAttachment(8, 'annex.pdf'), pdfAttachment(7, 'informe.pdf')],
    });
    applyAll(turn, checkedDuelEvents());
    expect(pdfChecks(turn).map((c) => c.name)).toEqual(['annex.pdf', 'informe.pdf']);
    // A replayed turn whose attachments are not known yet: the order they came in.
    const replayed = live('rc');
    applyAll(replayed, checkedDuelEvents());
    expect(pdfChecks(replayed).map((c) => c.name)).toEqual(['informe.pdf', 'annex.pdf']);
  });

  it('is part of the replay: duplicates are ignored, a replay from the start rebuilds it', () => {
    const turn = live('rc');
    const events = checkedDuelEvents();
    applyAll(turn, events);
    expect(applyAll(turn, events)).toBe(0);
    const fresh = live('rc');
    applyAll(fresh, events);
    expect(fresh.pdfChecks).toEqual(turn.pdfChecks);
    expect(turn.pdfChecks).toHaveLength(2);
  });

  it('a stored turn has none: its ChatGPT messages say how it read the PDFs', () => {
    const [stored] = turnsFromMessages(debateMessages(), 7);
    expect(stored!.pdfChecks).toEqual([]);
  });
});

describe('how ChatGPT read the PDFs when it cannot open them (meta.pdf_reading)', () => {
  const READING: PdfReading = {
    attachment_id: 7,
    name: 'informe.pdf',
    checked: true,
    claude_pages: [2, 5],
    hidden_pages: [5],
    unchecked_pages: [],
    reason: null,
  };
  const UNCHECKED: PdfReading = {
    attachment_id: 8,
    name: 'annex.pdf',
    checked: false,
    claude_pages: [],
    hidden_pages: [],
    unchecked_pages: [1, 2],
    reason: 'La comprovació de Claude ha trigat massa.',
  };

  const completed = (reading?: PdfReading[]): TurnEvent[] =>
    sequence('rr', [
      { type: 'turn.started', conversation_id: 3, turn_id: 14, mode: 'duel', new_conversation: false },
      { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'claude-opus' },
      { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
      { type: 'stream.completed', stream_id: 'c', message_id: 15, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
      {
        type: 'stream.completed', stream_id: 'g', message_id: 16, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, agreement: null,
        unchanged: false, ...(reading ? { pdf_reading: reading } : {}),
      },
    ]);

  it('comes with stream.completed of each ChatGPT message, live', () => {
    const turn = live('rr');
    applyAll(turn, completed([READING, UNCHECKED]));
    const { claude, chatgpt } = streamsByAgent(turn, 'answer');
    expect(chatgpt!.pdfReading).toEqual([READING, UNCHECKED]);
    expect(claude!.pdfReading).toEqual([]);
    const plain = live('rr');
    applyAll(plain, completed());
    expect(streamsByAgent(plain, 'answer').chatgpt!.pdfReading).toEqual([]);
  });

  it('is read from the stored meta after a reload, the same as live', () => {
    const turn = live('rr');
    applyAll(turn, completed([READING, UNCHECKED]));
    const [stored] = turnsFromMessages([
      message({ id: 14, turn_id: 14, kind: 'question', content: 'Pregunta?', final: true, meta: { mode: 'duel' } }),
      message({ id: 15, turn_id: 14, kind: 'answer', agent: 'claude', content: 'A', final: true, meta: { usage: usage(10, 5) } }),
      message({ id: 16, turn_id: 14, kind: 'answer', agent: 'chatgpt', content: 'B', final: true, meta: { usage: usage(10, 5), pdf_reading: [READING, UNCHECKED] } }),
    ]);
    const reloaded = streamsByAgent(stored!, 'answer');
    expect(reloaded.chatgpt!.pdfReading).toEqual(streamsByAgent(turn, 'answer').chatgpt!.pdfReading);
    expect(reloaded.claude!.pdfReading).toEqual([]);
  });

  it('skips entries it cannot use, and pages that are not page numbers', () => {
    const [stored] = turnsFromMessages([
      message({ id: 20, turn_id: 20, kind: 'question', content: 'Q', final: true, meta: { mode: 'solo', target: 'chatgpt' } }),
      message({
        id: 21, turn_id: 20, kind: 'answer', agent: 'chatgpt', content: 'A', final: true,
        meta: {
          pdf_reading: [
            { ...READING, claude_pages: [2, '3', 0, 5.5, 5], reason: 7 },
            { ...READING, attachment_id: 'x' },
            { ...READING, name: null },
            { ...READING, checked: 'yes' },
            null,
            { attachment_id: 9, name: 'nou.pdf', checked: false },
          ] as unknown as PdfReading[],
        },
      }),
    ]);
    expect(stored!.streams[0]!.pdfReading).toEqual([
      { ...READING, claude_pages: [2, 5], reason: null },
      { attachment_id: 9, name: 'nou.pdf', checked: false, claude_pages: [], hidden_pages: [], unchecked_pages: [], reason: null },
    ]);
    const [none] = turnsFromMessages([
      message({ id: 30, turn_id: 30, kind: 'question', content: 'Q', final: true, meta: { mode: 'solo' } }),
      message({ id: 31, turn_id: 30, kind: 'answer', agent: 'chatgpt', content: 'A', final: true, meta: { pdf_reading: 'no' as unknown as PdfReading[] } }),
    ]);
    expect(none!.streams[0]!.pdfReading).toEqual([]);
  });
});
