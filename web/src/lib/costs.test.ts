import { describe, expect, it } from 'vitest';
import {
  approxEur,
  budgetLevel,
  inferCostBasis,
  formatMoney,
  fxText,
  monthName,
  processedTokens,
  savingsEur,
  spendLine,
  streamCost,
  tokenBreakdown,
  turnCost,
  turnCostTitle,
  type TurnCost,
} from './costs';
import type { Agent, AgentSpend, FxRate, Message, PdfReading, TurnMode } from './protocol';
import { sequence, usage } from './test-fixtures';
import {
  applyTurnEvent,
  createLiveTurn,
  turnsFromMessages,
  type CostBasis,
  type StreamView,
  type TurnView,
} from './turns.svelte';

// Intl uses a no-break space before "€" and "%" in Catalan.
const plain = (s: string | null | undefined) => s?.replace(/ | /g, ' ');

const fx: FxRate = { eur_per_usd: 0.86, as_of: '2026-09-25', source: 'ecb' };

function stream(partial: Partial<StreamView>): StreamView {
  return {
    id: 's',
    agent: 'claude',
    kind: 'answer',
    round: 0,
    model: 'm',
    text: '',
    critique: '',
    status: 'done',
    error: null,
    messageId: 1,
    usage: null,
    latencyMs: null,
    ttftMs: null,
    agreement: null,
    unchanged: false,
    cached: false,
    costBasis: null,
    truncated: false,
    finishReason: null,
    unchangedNote: null,
    degraded: false,
    pdfReading: [],
    refineRole: null,
    refine: null,
    ...partial,
  };
}

const priced = (cost: number | null) => ({ ...usage(100, 50), cost_usd: cost });

describe('euro formatting', () => {
  it('shows per-answer costs as "≈ x €" with adaptive precision', () => {
    expect(plain(approxEur(0.0143, 0.86))).toBe('≈ 0,0123 €');
    expect(plain(approxEur(1, 0.86))).toBe('≈ 0,86 €');
    expect(plain(approxEur(20, 0.86))).toBe('≈ 17,20 €');
    expect(approxEur(null, 0.86)).toBeNull();
    expect(approxEur(undefined, 0.86)).toBeNull();
  });

  it('formats totals and limits compactly', () => {
    expect(plain(formatMoney(50))).toBe('50 €');
    expect(plain(formatMoney(12.3))).toBe('12,30 €');
    expect(plain(formatMoney(1234.5))).toBe('1.234,50 €');
    expect(plain(formatMoney(0))).toBe('0 €');
    expect(plain(formatMoney(0.0456))).toBe('0,0456 €');
  });

  it('describes the exchange rate', () => {
    expect(plain(fxText(fx))).toBe('1 $ = 0,86 € · BCE, 25 de set.');
    expect(plain(fxText({ eur_per_usd: 0.912345, as_of: null, source: 'manual' }))).toBe('1 $ = 0,9123 € · manual');
  });

  it('names the month in Catalan', () => {
    expect(monthName('2026-09')).toBe('setembre del 2026');
    expect(monthName('bad')).toBe('bad');
  });
});

describe('answer cost', () => {
  it('uses the cost basis for the tooltip', () => {
    const api = streamCost(stream({ usage: priced(0.0143), costBasis: 'api' }), 0.86);
    expect(plain(api?.text)).toBe('≈ 0,0123 €');
    expect(api?.title).toMatch(/^Cost real de l'API \(0,0143 \$\)$/);
    const eq = streamCost(stream({ usage: priced(0.0143), costBasis: 'equivalent' }), 0.86);
    expect(eq?.title).toMatch(/^Valor equivalent a preus d'API — inclòs a la subscripció/);
    const unknown = streamCost(stream({ usage: priced(0.5) }), 0.86);
    expect(unknown?.title).toMatch(/^Cost estimat a preus d'API/);
  });

  it('infers the basis of live answers like the server does', () => {
    expect(inferCostBasis('api', priced(null))).toBe('api');
    expect(inferCostBasis('cli', priced(null))).toBe('equivalent');
    expect(inferCostBasis('fake', priced(0.01))).toBe('equivalent');
    expect(inferCostBasis('fake', priced(null))).toBeNull();
    expect(inferCostBasis(undefined, priced(0.01))).toBeNull();
  });

  it('is hidden when unknown or for a free cached answer', () => {
    expect(streamCost(stream({ usage: priced(null) }), 0.86)).toBeNull();
    expect(streamCost(stream({ usage: null }), 0.86)).toBeNull();
    expect(streamCost(stream({ usage: priced(0), cached: true }), 0.86)).toBeNull();
    expect(streamCost(stream({ usage: priced(0) }), 0.86)).not.toBeNull();
  });
});

describe('turn cost and savings', () => {
  function turnWith(streams: StreamView[], totalUsd?: number | null): TurnView {
    const t = createLiveTurn({ requestId: 'r', question: 'q', mode: 'duel' });
    t.streams = streams;
    if (totalUsd !== undefined) t.usage = priced(totalUsd);
    return t;
  }

  it('splits real API cost from value included in subscriptions', () => {
    const t = turnWith([
      stream({ id: 'a', usage: priced(0.01), costBasis: 'api' }),
      stream({ id: 'b', agent: 'chatgpt', usage: priced(0.03), costBasis: 'equivalent' }),
      stream({ id: 'c', usage: priced(null) }),
    ]);
    const cost = turnCost(t);
    expect(cost.totalUsd).toBeCloseTo(0.04);
    expect(cost.apiUsd).toBeCloseTo(0.01);
    expect(cost.equivalentUsd).toBeCloseTo(0.03);
    expect(plain(turnCostTitle(cost, 1))).toBe(
      "Cost del torn a preus d'API · cost real d'API: 0,01 € · valor inclòs a la subscripció: 0,03 €",
    );
  });

  it('prefers the server total (it includes internal calls)', () => {
    const t = turnWith([stream({ usage: priced(0.01), costBasis: 'api' })], 0.05);
    expect(turnCost(t).totalUsd).toBe(0.05);
    expect(turnCost(turnWith([stream({ usage: priced(null) })])).totalUsd).toBeNull();
  });

  it('keeps every part of the split when it knows the whole total', () => {
    const cost = turnCost(turnWith([stream({ usage: priced(0.01), costBasis: 'api' })]));
    expect(cost).toEqual({ totalUsd: 0.01, apiUsd: 0.01, equivalentUsd: 0, otherUsd: 0 });
  });

  it('converts the value of the saved tokens', () => {
    const base = { cache: 0, compaction: 0, early_stop: 0, unchanged: 10, total: 10 };
    expect(savingsEur({ ...base, cost_usd: 0.1 }, 0.86)).toBeCloseTo(0.086);
    expect(savingsEur({ ...base, cost_usd: null }, 0.86)).toBeNull();
    expect(savingsEur({ ...base, cost_usd: 0 }, 0.86)).toBeNull();
    expect(savingsEur(null, 0.86)).toBeNull();
  });
});

// The turn total also has calls no answer shows: a history summary, failed calls and the
// attempts other models declined before a fallback (A8). The split says what they are when
// the turn tells it, and never passes them off as one kind of cost when it does not.
describe('turn cost split: the calls no answer shows (A8)', () => {
  function turnOf(mode: TurnMode, streams: StreamView[], totalUsd: number, compacted = false): TurnView {
    const t = createLiveTurn({ requestId: 'r', question: 'q', mode });
    t.streams = streams;
    t.usage = priced(totalUsd);
    t.compacted = compacted;
    return t;
  }
  const answer = (agent: Agent, usd: number, costBasis: CostBasis | null): StreamView =>
    stream({ id: `${agent}-${usd}`, agent, usage: priced(usd), costBasis });
  const failed = (agent: Agent, usd: number): StreamView =>
    stream({ id: `${agent}-failed`, agent, status: 'failed', messageId: null, usage: priced(usd) });
  /** The parts always add up to the total. */
  const parts = (c: TurnCost) => c.apiUsd + c.equivalentUsd + c.otherUsd;

  it("a solo turn's other calls are its agent's: the declined attempt is real API spend", () => {
    // The audit's Fable 5.1 → Opus 4.8 turn: the answer's 0.1305 $ and the 0.240125 $ declined.
    const cost = turnCost(turnOf('solo', [answer('claude', 0.1305, 'api')], 0.370625));
    expect(cost.totalUsd).toBe(0.370625);
    expect(cost.apiUsd).toBeCloseTo(0.370625, 12);
    expect([cost.equivalentUsd, cost.otherUsd]).toEqual([0, 0]);
    expect(plain(turnCostTitle(cost, 1))).toBe("Cost del torn a preus d'API · cost real d'API: 0,371 €");
  });

  it('a duel or a debate whose answers are all one kind of cost has calls of that kind only', () => {
    // Both agents by subscription: the summary is subscription value too, whoever wrote it.
    const compacted = turnCost(
      turnOf('duel', [answer('claude', 0.003063, 'equivalent'), answer('chatgpt', 0.003198, 'equivalent')], 0.009375, true),
    );
    expect(compacted.equivalentUsd).toBeCloseTo(0.009375, 12);
    expect([compacted.apiUsd, compacted.otherUsd]).toEqual([0, 0]);
    // Both by API: a failed call, which says no kind, was real API spend.
    const debate = turnCost(
      turnOf('debate', [answer('claude', 0.01, 'api'), answer('chatgpt', 0.02, 'api'), failed('claude', 0.005)], 0.035),
    );
    expect(debate.apiUsd).toBeCloseTo(0.035, 12);
    expect([debate.equivalentUsd, debate.otherUsd]).toEqual([0, 0]);
  });

  it("does not guess the kind of calls that may be either agent's", () => {
    // One agent by API and the other by subscription.
    const mixed = turnCost(turnOf('duel', [answer('claude', 0.01, 'api'), answer('chatgpt', 0.03, 'equivalent')], 0.06));
    expect([mixed.apiUsd, mixed.equivalentUsd]).toEqual([0.01, 0.03]);
    expect(mixed.otherUsd).toBeCloseTo(0.02, 12);
    expect(plain(turnCostTitle(mixed, 1))).toBe(
      "Cost del torn a preus d'API · cost real d'API: 0,01 € · valor inclòs a la subscripció: 0,03 € · altres crides: 0,02 €",
    );
    // A solo turn that compacted: Claude writes the summary first, even for ChatGPT's turn.
    const solo = turnCost(turnOf('solo', [answer('chatgpt', 0.003, 'equivalent')], 0.005, true));
    expect(solo.equivalentUsd).toBe(0.003);
    expect(solo.otherUsd).toBeCloseTo(0.002, 12);
    // An agent with no answer: its call's kind is unknown (the audit's late refusal, A9).
    const late = turnCost(turnOf('duel', [answer('chatgpt', 0.003116, 'equivalent'), failed('claude', 0.0262)], 0.029316));
    expect(late.equivalentUsd).toBe(0.003116);
    expect(late.otherUsd).toBeCloseTo(0.0262, 12);
    for (const c of [mixed, solo, late]) expect(parts(c)).toBeCloseTo(c.totalUsd!, 12);
  });

  it('takes rounding for what it is, not for a call', () => {
    const cost = turnCost(turnOf('duel', [answer('claude', 0.1, 'api'), answer('chatgpt', 0.2, 'equivalent')], 0.1 + 0.2));
    expect(cost.otherUsd).toBe(0);
    expect(turnCostTitle(cost, 1)).not.toContain('altres crides');
  });

  it('shows no split when it knows none of it', () => {
    const cost = turnCost(turnOf('solo', [failed('claude', 0.0262)], 0.0262));
    expect(cost).toMatchObject({ apiUsd: 0, equivalentUsd: 0 });
    expect(cost.otherUsd).toBeCloseTo(0.0262, 12);
    expect(turnCostTitle(cost, 1)).toBe("Cost del torn a preus d'API");
  });
});

// Claude checks the text of the PDFs that ChatGPT with the subscription reads (P7b): its calls
// are in the turn's total and on no answer, in a solo turn for ChatGPT too. What both views know
// of them is that ChatGPT read a PDF through the check (`pdf_reading`), never their kind of cost:
// the rest of such a turn is not passed off as ChatGPT's, live or after a reload (P5).
describe("turn cost split: Claude's check of the PDFs ChatGPT reads (P7b)", () => {
  const billed = (input: number, output: number, usd: number) => ({ ...usage(input, output), cost_usd: usd });
  const CHECK = billed(40_000, 800, 0.05); // Claude by API: real money
  const ANSWER = billed(1200, 300, 0.01); // ChatGPT with the subscription: a value
  const TOTAL = billed(41_200, 1100, 0.06);
  const NO_SAVINGS = { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null };
  const READING: PdfReading = {
    attachment_id: 7,
    name: 'informe.pdf',
    checked: true,
    claude_pages: [2, 5],
    hidden_pages: [],
    unchecked_pages: [],
    reason: null,
  };

  /**
   * A solo turn for ChatGPT whose PDF Claude checked, live. The app sets each basis: the
   * answer's from its stream.completed, the check's from Claude's mode.
   */
  function live(claude: CostBasis): TurnView {
    const turn = createLiveTurn({ requestId: 'r', question: 'Què diu?', mode: 'solo', target: 'chatgpt', conversationId: 3 });
    const check = { type: 'pdf.check', attachment_id: 7, name: 'informe.pdf', reused: false, reason: null } as const;
    for (const ev of sequence('r', [
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'solo', new_conversation: false },
      { ...check, state: 'checking', claude_pages: [], hidden_pages: [], unchecked_pages: [], usage: null },
      { ...check, state: 'checked', claude_pages: [2, 5], hidden_pages: [], unchecked_pages: [], usage: CHECK },
      { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
      {
        type: 'stream.completed', stream_id: 'g', message_id: 14, usage: ANSWER, latency_ms: 900, ttft_ms: 100,
        agreement: null, unchanged: false, cost_basis: 'equivalent', pdf_reading: [READING],
      },
      {
        type: 'turn.completed', conversation_id: 3, turn_id: 12, final_message_ids: [14], usage: TOTAL,
        savings: NO_SAVINGS, consensus: null, cached: false,
      },
    ])) {
      applyTurnEvent(turn, ev);
    }
    turn.streams[0]!.costBasis = 'equivalent';
    turn.pdfChecks[0]!.costBasis = claude;
    return turn;
  }

  /** The same turn after a reload: the check is only in the total, the answer says how ChatGPT read the PDF. */
  function reloaded(): TurnView {
    const at = '2026-09-29T10:00:00Z';
    const messages: Message[] = [
      {
        id: 11, turn_id: 12, kind: 'question', content: 'Què diu?', agent: null, round: 0, final: true, created_at: at,
        meta: {
          mode: 'solo',
          target: 'chatgpt',
          outcome: {
            status: 'completed', failures: [], usage: TOTAL, savings: NO_SAVINGS, consensus: null,
            final_message_ids: [14], cached: false,
          },
        },
      },
      {
        id: 14, turn_id: 12, kind: 'answer', content: 'Diu…', agent: 'chatgpt', round: 0, final: true, created_at: at,
        meta: { model: 'gpt-5', usage: ANSWER, cost_basis: 'equivalent', pdf_reading: [READING] },
      },
    ];
    return turnsFromMessages(messages, 3)[0]!;
  }

  it("never passes Claude's check off as ChatGPT's subscription value: it is among the other calls", () => {
    const cost = turnCost(live('api'));
    expect(cost).toMatchObject({ totalUsd: 0.06, apiUsd: 0, equivalentUsd: 0.01 });
    expect(cost.otherUsd).toBeCloseTo(0.05, 12);
    expect(plain(turnCostTitle(cost, 1))).toBe(
      "Cost del torn a preus d'API · valor inclòs a la subscripció: 0,01 € · altres crides: 0,05 €",
    );
  });

  it('splits it the same way live and after a reload, whatever mode Claude has', () => {
    const shown = turnCost(reloaded());
    expect(shown.otherUsd).toBeCloseTo(0.05, 12);
    // Nothing stored says what kind of cost the check was: the live view does not use it either.
    expect(turnCost(live('api'))).toEqual(shown);
    expect(turnCost(live('equivalent'))).toEqual(shown);
  });

  it("keeps a solo turn's rest as its agent's when ChatGPT read no PDF through a check", () => {
    // A declined attempt before a fallback: ChatGPT's own call, so subscription value.
    const turn = createLiveTurn({ requestId: 'r', question: 'q', mode: 'solo', target: 'chatgpt' });
    turn.streams = [stream({ agent: 'chatgpt', usage: priced(0.01), costBasis: 'equivalent' })];
    turn.usage = priced(0.03);
    expect(turnCost(turn)).toEqual({ totalUsd: 0.03, apiUsd: 0, equivalentUsd: 0.03, otherUsd: 0 });
  });
});

describe('processed tokens (A7, ADR 0008)', () => {
  const cached = {
    input_tokens: 3,
    output_tokens: 100,
    cache_read_tokens: 10_000,
    cache_write_tokens: 20_000,
    reasoning_tokens: 50,
    cost_usd: null,
  };

  it('counts input, cache reads, cache writes and output; the reasoning is part of the output', () => {
    expect(processedTokens(cached)).toBe(30_103);
    expect(processedTokens(usage(100, 50, 25))).toBe(175);
  });

  it('counts missing, negative and non-numeric fields as 0', () => {
    expect(processedTokens(null)).toBe(0);
    expect(processedTokens(undefined)).toBe(0);
    expect(processedTokens({ input_tokens: 5 })).toBe(5);
    expect(
      processedTokens({ input_tokens: -5, output_tokens: Number.NaN, cache_read_tokens: 'x' as never, cache_write_tokens: 7 }),
    ).toBe(7);
  });

  it('describes each kind of token, in Catalan', () => {
    expect(plain(tokenBreakdown(cached))).toBe(
      "3 d'entrada · 10.000 llegits de la memòria cau · 20.000 escrits a la memòria cau · 100 de sortida (50 de raonament)",
    );
    expect(tokenBreakdown(usage(1200, 80))).toBe("1.200 d'entrada · 80 de sortida");
  });
});

describe('budget bar', () => {
  it('warns from 80 % and is critical from 100 %', () => {
    expect(budgetLevel(null)).toBe('ok');
    expect(budgetLevel(0)).toBe('ok');
    expect(budgetLevel(0.799)).toBe('ok');
    expect(budgetLevel(0.8)).toBe('warn');
    expect(budgetLevel(0.99)).toBe('warn');
    expect(budgetLevel(1)).toBe('bad');
    expect(budgetLevel(1.7)).toBe('bad');
    expect(budgetLevel(Number.NaN)).toBe('ok');
  });
});

describe('spendLine (sidebar month line)', () => {
  const spend = (partial: Partial<AgentSpend>): AgentSpend => ({
    api_usd: 0,
    equivalent_usd: 0,
    unpriced_calls: 0,
    budget_eur: null,
    budget_used: null,
    plan_eur: null,
    plan_value: null,
    ...partial,
  });
  const one: FxRate = { eur_per_usd: 1, as_of: null, source: 'manual' };

  it('API mode with a budget: amount, share and level', () => {
    const line = spendLine('api', spend({ api_usd: 12.3, budget_eur: 50, budget_used: 0.246 }), one, '2026-09');
    expect(line?.label).toBe('Gastat');
    expect(plain(`${line?.amount} (${line?.percent})`)).toBe('12,30 € de 50 € (25 %)');
    expect(plain(line?.title)).toBe("Setembre del 2026: despesa d'API de 12,30 € sobre un pressupost de 50 € (25 %).");
    expect(line?.ratio).toBe(0.246);
    expect(line?.level).toBe('ok');
    expect(line?.kind).toBe('budget');

    const warn = spendLine('api', spend({ api_usd: 41, budget_eur: 50, budget_used: 0.82 }), one, '2026-09');
    expect(warn?.level).toBe('warn');
    const over = spendLine('api', spend({ api_usd: 55, budget_eur: 50, budget_used: 1.1 }), one, '2026-09');
    expect(over?.level).toBe('bad');
    expect(plain(`${over?.amount} (${over?.percent})`)).toBe('55 € de 50 € (110 %)');
  });

  it('computes the share when the server does not send it', () => {
    const line = spendLine('api', spend({ api_usd: 40, budget_eur: 50 }), one, '2026-09');
    expect(line?.ratio).toBeCloseTo(0.8);
    expect(line?.level).toBe('warn');
  });

  it('API mode without a budget shows the plain amount', () => {
    const line = spendLine('api', spend({ api_usd: 2, unpriced_calls: 3 }), { ...one, eur_per_usd: 0.86 }, '2026-09');
    expect(plain(line?.amount)).toBe('1,72 €');
    expect(line?.ratio).toBeNull();
    expect(line?.percent).toBeNull();
    expect(line?.title).toContain('sense pressupost');
    expect(line?.title).toContain('3 crides amb models sense preu conegut no hi compten.');
  });

  it('subscription mode compares the value obtained with the plan price', () => {
    const line = spendLine('cli', spend({ equivalent_usd: 34.2, plan_eur: 100, plan_value: 0.342 }), one, '2026-09');
    expect(line?.label).toBe('Valor aprofitat');
    expect(plain(`${line?.label} ${line?.amount} (${line?.percent})`)).toBe('Valor aprofitat 34,20 € · pla 100 € (34 %)');
    expect(line?.kind).toBe('plan');
    // Getting more than the plan is good news, never a warning.
    const lots = spendLine('cli', spend({ equivalent_usd: 250, plan_eur: 100, plan_value: 2.5 }), one, '2026-09');
    expect(lots?.level).toBe('ok');

    const noPlan = spendLine('cli', spend({ equivalent_usd: 34.2 }), one, '2026-09');
    expect(plain(noPlan?.amount)).toBe('34,20 €');
    expect(noPlan?.ratio).toBeNull();
  });

  it('shows nothing for the demo provider', () => {
    expect(spendLine('fake', spend({}), one, '2026-09')).toBeNull();
  });
});
