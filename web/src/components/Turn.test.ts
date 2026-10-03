// Which synthesis a debate shows when the first attempt is not the result (audit A1), how
// a turn ended, live and after a reload (A9, A14, N10), and the tokens of its totals (A7).
import { flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { i18n } from '../lib/i18n/index.svelte';
import type { Attachment, Message, TurnEvent, TurnMode, TurnOptions, Usage } from '../lib/protocol';
import {
  cancelledDuelEvents,
  cancelledDuelMessages,
  debateEvents,
  degradedSynthesisEvents,
  failedRevisionEvents,
  failedRevisionMessages,
  failedSoloEvents,
  failedSoloMessages,
  fallbackSoloEvents,
  fallbackSoloMessages,
  fallbackSynthesisEvents,
  keptRevisionEvents,
  keptRevisionMessages,
  lateFailureDuelEvents,
  lateFailureDuelMessages,
  oneSurvivorDebateEvents,
  QUICK_DEBATE,
  quickDebateMessages,
  quickDebateOutcome,
  sequence,
  withOutcome,
} from '../lib/test-fixtures';
import { cleanup, render, textOf } from '../lib/test-render';
import { applyTurnEvent, createLiveTurn, turnsFromMessages, type TurnView } from '../lib/turns.svelte';

vi.mock('../lib/app.svelte', () => ({ app: { eurPerUsd: 0.86 } }));

import Turn from './Turn.svelte';

afterEach(cleanup);

// Intl uses a no-break space before "€" in Catalan.
const plain = (s: string) => s.replace(/\u00a0|\u202f/g, ' ');

function liveDebate(events: TurnEvent[]): TurnView {
  const turn = createLiveTurn({
    requestId: events[0]!.request_id,
    question: 'Pregunta?',
    mode: 'debate',
    options: QUICK_DEBATE,
    conversationId: 8,
  });
  for (const ev of events) applyTurnEvent(turn, ev);
  return turn;
}

/** Text of the synthesis card of a rendered turn. */
function synthesisCard(turn: TurnView): string {
  const card = render(Turn, { turn, plannedRounds: 0 }).querySelector('article.card.synthesis');
  expect(card).not.toBeNull();
  return textOf(card);
}

describe('Turn: the synthesis card (A1)', () => {
  it("shows the other agent's synthesis after the first attempt failed, as after a reload", () => {
    const live = synthesisCard(liveDebate(fallbackSynthesisEvents()));
    expect(live).toContain('Síntesi per ChatGPT');
    expect(live).toContain('Síntesi de ChatGPT');
    expect(live).toContain("Claude no ha pogut fer la síntesi; l'ha feta ChatGPT.");
    expect(live).not.toContain('Síntesi a mig');
    expect(live).not.toContain('No ha pogut respondre');

    const [stored] = turnsFromMessages(quickDebateMessages({ agent: 'chatgpt', content: 'Síntesi de ChatGPT' }), 8);
    expect(synthesisCard(stored!)).toBe(live);
  });

  it('shows the fallback attempt while it runs, not the failed one', () => {
    const events = fallbackSynthesisEvents();
    const cut = events.findIndex((e) => e.type === 'stream.completed' && e.stream_id === 's2');
    const card = synthesisCard(liveDebate(events.slice(0, cut)));
    expect(card).toContain('Síntesi per ChatGPT');
    expect(card).toContain('Escrivint…');
    expect(card).toContain('Claude no ha pogut fer la síntesi; ara la fa ChatGPT.');
    expect(card).not.toContain('Síntesi a mig');
    expect(card).not.toContain('No ha pogut respondre');
  });

  it('shows the answer kept as final when nobody could synthesize, as after a reload', () => {
    const live = synthesisCard(liveDebate(degradedSynthesisEvents()));
    expect(live).toContain('Síntesi per Claude');
    expect(live).toContain('Resposta de Claude');
    expect(live).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.");
    expect(live).not.toContain('Error de Claude.');
    expect(live).not.toContain('No ha pogut respondre');

    const stored = quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true });
    expect(synthesisCard(turnsFromMessages(stored, 8)[0]!)).toBe(live);
  });

  it('says both things when the answer kept as final was itself cut off', () => {
    const events = degradedSynthesisEvents().map((e) =>
      e.type === 'stream.completed' && e.stream_id === 's3' ? { ...e, truncated: true } : e,
    );
    const live = synthesisCard(liveDebate(events));
    expect(live).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.");
    expect(live).toContain('Incompleta');
    expect(live).toContain("Resposta incompleta: s'ha tallat abans d'acabar.");

    const stored = quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true }).map((m) =>
      m.id === 63 ? { ...m, meta: { ...m.meta, truncated: true as const, finish_reason: 'max_tokens' } } : m,
    );
    const reloaded = synthesisCard(turnsFromMessages(stored, 8)[0]!);
    expect(reloaded).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.");
    expect(reloaded).toContain("Resposta incompleta: s'ha arribat al límit de sortida.");
  });

  it("marks the partner's answer kept after an agent failed its first answer", () => {
    const live = synthesisCard(liveDebate(oneSurvivorDebateEvents()));
    expect(live).toContain('Síntesi per ChatGPT');
    expect(live).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de ChatGPT.");

    const stored = quickDebateMessages({ agent: 'chatgpt', content: 'Resposta de ChatGPT', degraded: true }, ['chatgpt']);
    expect(synthesisCard(turnsFromMessages(stored, 8)[0]!)).toBe(live);
  });
});

describe('Turn: revision rounds', () => {
  it('tells each round which revisions keep the previous answer, live and after a reload', () => {
    const turn = createLiveTurn({ requestId: 'req-k', question: 'Pregunta?', mode: 'debate', conversationId: 9 });
    for (const ev of keptRevisionEvents()) applyTurnEvent(turn, ev);
    const claudeRevision = (t: TurnView) =>
      textOf(render(Turn, { turn: t, plannedRounds: 1 }).querySelector('section[aria-label="Revisió de Claude"]'));

    const live = claudeRevision(turn);
    expect(live).toContain("Revisió incompleta: s'ha tallat abans d'acabar. Es manté la resposta anterior.");
    expect(live).not.toContain('Resposta revisada');

    const reloaded = claudeRevision(turnsFromMessages(keptRevisionMessages(), 9)[0]!);
    expect(reloaded).toContain("Revisió incompleta: s'ha arribat al límit de sortida. Es manté la resposta anterior.");
    expect(reloaded).not.toContain('Resposta revisada');
  });
});

// ------------------------------------------ how a turn ended (A9, A14, N10) and its totals (A7)

/** A live turn as the app builds it (the cost basis comes with stream.completed). */
function liveTurn(events: TurnEvent[], mode: TurnMode, options: TurnOptions | null = null): TurnView {
  const turn = createLiveTurn({
    requestId: events[0]!.request_id,
    question: 'Pregunta?',
    mode,
    target: mode === 'solo' ? 'claude' : null,
    options,
    conversationId: 1,
    createdAt: '2026-09-27T10:00:00Z',
  });
  for (const ev of events) {
    applyTurnEvent(turn, ev);
    if (ev.type !== 'stream.completed') continue;
    const stream = turn.streams.find((s) => s.id === ev.stream_id);
    if (stream) stream.costBasis = ev.cost_basis ?? null;
  }
  return turn;
}

/** What a rendered turn says: its agent cards, its banner and its totals. */
function parts(turn: TurnView) {
  const el = render(Turn, { turn, plannedRounds: 1 });
  return {
    claude: textOf(el.querySelector('article.card.claude')),
    chatgpt: textOf(el.querySelector('article.card.chatgpt')),
    banner: textOf(el.querySelector('.banner')),
    totals: textOf(el.querySelector('footer.totals')),
  };
}

const storedTurn = (messages: Message[]): TurnView => turnsFromMessages(messages, 1)[0]!;

describe('Turn: how it ended, live and after a reload (A9, A14, N10)', () => {
  it('a failed turn shows its error, the call that failed and what it cost', () => {
    const live = parts(liveTurn(failedSoloEvents(), 'solo'));
    expect(live.claude).toContain('No ha pogut respondre. Claude ha declinat.');
    expect(live.banner).toBe('El torn ha fallat. Claude no ha pogut respondre.');
    expect(live.totals).toContain('Total 6.300 tokens');
    expect(plain(live.totals)).toContain('≈ 0,0225 €');
    expect(parts(storedTurn(failedSoloMessages()))).toEqual(live);
  });

  it('a cancelled turn says so after a reload, with what it cost', () => {
    const live = parts(liveTurn(cancelledDuelEvents(), 'duel'));
    // The owner's stop and a server shutdown (a restart, a deploy) both cancel a turn, and
    // neither the event nor the outcome says which: the banner does not blame the owner.
    expect(live.banner).toBe("Aquest torn s'ha aturat.");
    expect(live.totals).toContain('Total 1.115 tokens');
    expect(plain(live.totals)).toContain('≈ 0,0054 €');
    const stored = parts(storedTurn(cancelledDuelMessages()));
    expect(stored.banner).toBe(live.banner);
    expect(stored.totals).toBe(live.totals);
    expect(stored.chatgpt).toBe(live.chatgpt);
    // Claude was interrupted: nothing of it was stored.
    expect(stored.claude).toContain('No ha respost.');
  });

  it('a duel whose call failed late shows it, and its whole cost, after a reload', () => {
    const live = parts(liveTurn(lateFailureDuelEvents(), 'duel'));
    expect(live.claude).toContain('No ha pogut respondre. Claude ha declinat.');
    expect(live.totals).toContain('Total 6.587 tokens');
    expect(plain(live.totals)).toContain('≈ 0,0252 €');
    // No answer says what kind of cost Claude's refusal was: it is not passed off as either.
    expect(plain(live.totals)).toContain(
      "(Cost del torn a preus d'API · valor inclòs a la subscripció: 0,0027 € · altres crides: 0,0225 €)",
    );
    expect(live.banner).toBe('');
    expect(parts(storedTurn(lateFailureDuelMessages()))).toEqual(live);
  });

  it("a fallback's declined attempt counts in the real API cost, live and after a reload (A8)", () => {
    const cost = (turn: TurnView) => {
      const el = render(Turn, { turn, plannedRounds: 0 }).querySelector('footer.totals .cost');
      expect(el).not.toBeNull();
      return { text: plain(textOf(el)), title: plain(el!.getAttribute('title') ?? '') };
    };
    const live = cost(liveTurn(fallbackSoloEvents(), 'solo'));
    // 0.370625 $ at 0.86 €/$: the answer Opus 4.8 served (0.1305 $) and the attempt Fable 5.1
    // declined (0.240125 $), both real API spend; the answer alone would say 0,112 €.
    expect(live.title).toBe("Cost del torn a preus d'API · cost real d'API: 0,319 €");
    expect(live.text).toMatch(/^≈ 0,319 €/);
    expect(live.text).toContain(`(${live.title})`); // what a screen reader gets
    expect(cost(storedTurn(fallbackSoloMessages()))).toEqual(live);
  });

  it('a turn that never ended says so, even with every answer stored', () => {
    const stored = parts(storedTurn(withOutcome(lateFailureDuelMessages(), null)));
    expect(stored.banner).toBe('Aquest torn no es va completar.');
    expect(stored.chatgpt).toContain('Resposta de ChatGPT');
  });

  it("a debate's failed revision shows in its round after a reload", () => {
    const revision = (turn: TurnView) =>
      textOf(render(Turn, { turn, plannedRounds: 1 }).querySelector('section[aria-label="Revisió de ChatGPT"]'));
    const live = revision(liveTurn(failedRevisionEvents(), 'debate'));
    expect(live).toContain('Temps esgotat.');
    expect(revision(storedTurn(failedRevisionMessages()))).toBe(live);
  });

  it("a debate's failed first answer shows on its card after a reload", () => {
    const live = parts(liveTurn(oneSurvivorDebateEvents(), 'debate', QUICK_DEBATE));
    expect(live.claude).toContain('No ha pogut respondre. Temps esgotat.');
    const failures = [{ agent: 'claude' as const, kind: 'timeout', message: 'Temps esgotat.', round: 0 }];
    const stored = storedTurn(
      withOutcome(quickDebateMessages({ agent: 'chatgpt', content: 'Resposta de ChatGPT', degraded: true }, ['chatgpt']), quickDebateOutcome(failures)),
    );
    expect(parts(stored).claude).toBe(live.claude);
  });
});

describe('Turn: totals count every token the calls processed (A7)', () => {
  const cached: Usage = {
    input_tokens: 3,
    output_tokens: 100,
    cache_read_tokens: 10_000,
    cache_write_tokens: 20_000,
    reasoning_tokens: 50,
    cost_usd: 0.1325,
  };
  const solo = (total: Usage): TurnEvent[] =>
    sequence('req-t', [
      { type: 'turn.started', conversation_id: 1, turn_id: 1, mode: 'solo', new_conversation: false },
      { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
      { type: 'stream.delta', stream_id: 'c', section: 'text', text: 'Resposta' },
      { type: 'stream.completed', stream_id: 'c', message_id: 2, usage: total, latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
      {
        type: 'turn.completed', conversation_id: 1, turn_id: 1, final_message_ids: [2], usage: total,
        savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null }, consensus: null, cached: false,
      },
    ]);

  it('input, cache reads and writes and output, never the reasoning twice', () => {
    const el = render(Turn, { turn: liveTurn(solo(cached), 'solo'), plannedRounds: 0 });
    const totals = el.querySelector('footer.totals')!;
    expect(textOf(totals)).toContain('Total 30.103 tokens');
    const tokens = totals.querySelector('[title]')!;
    expect(tokens.getAttribute('title')).toBe(
      "3 d'entrada · 10.000 llegits de la memòria cau · 20.000 escrits a la memòria cau · 100 de sortida (50 de raonament)",
    );
  });

  it('shows a total made only of cache reads', () => {
    const el = render(Turn, { turn: liveTurn(solo({ ...cached, input_tokens: 0, output_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0 }), 'solo'), plannedRounds: 0 });
    expect(textOf(el.querySelector('footer.totals'))).toContain('Total 10.000 tokens');
  });
});

describe('Turn: the attachments of the question', () => {
  const image: Attachment = {
    id: 21,
    name: 'grafic.png',
    kind: 'image',
    mime: 'image/png',
    size: 240_000,
    pages: null,
    width: 1200,
    height: 800,
    sha256: 'b'.repeat(64),
    created_at: '2026-09-28T10:00:00Z',
    has_thumbnail: true,
    text_available: false,
    estimated_tokens: 1247,
    pdf_notes: null,
  };
  const pdf: Attachment = {
    ...image,
    id: 22,
    name: 'informe.pdf',
    kind: 'pdf',
    mime: 'application/pdf',
    pages: 12,
    width: null,
    height: null,
    has_thumbnail: false,
    text_available: true,
    estimated_tokens: 43_200,
  };

  const names = (el: HTMLElement) => [...el.querySelectorAll('.question .att .name')].map((n) => textOf(n));

  it('shows their cards with the question, live and after a reload', () => {
    const live = createLiveTurn({ requestId: 'r-a', question: 'Què diuen?', mode: 'solo', target: 'claude', attachments: [image, pdf] });
    const liveEl = render(Turn, { turn: live, plannedRounds: 0 });
    expect(names(liveEl)).toEqual(['grafic.png', 'informe.pdf']);
    expect(liveEl.querySelector('.question .att img')?.getAttribute('src')).toBe('/api/attachments/21/thumbnail');

    const [stored] = turnsFromMessages([
      { id: 1, turn_id: 5, kind: 'question', content: 'Què diuen?', agent: null, round: 0, final: true, created_at: '2026-09-28T10:00:00Z', meta: { mode: 'solo', target: 'claude', attachments: [image, pdf] } },
      { id: 2, turn_id: 5, kind: 'answer', content: 'Diuen…', agent: 'claude', round: 0, final: true, created_at: '2026-09-28T10:00:01Z', meta: {} },
    ]);
    const storedEl = render(Turn, { turn: stored!, plannedRounds: 0 });
    expect(names(storedEl)).toEqual(['grafic.png', 'informe.pdf']);
    expect(textOf(storedEl.querySelector('.question'))).toContain('12 pàgines');
  });

  it('a question without attachments shows none', () => {
    const el = render(Turn, { turn: createLiveTurn({ requestId: 'r-b', question: 'Hola', mode: 'solo' }), plannedRounds: 0 });
    expect(el.querySelector('.question .attachments')).toBeNull();
  });

  it('clicking one opens its preview', async () => {
    const { viewer } = await import('../lib/viewer.svelte');
    const el = render(Turn, { turn: createLiveTurn({ requestId: 'r-c', question: 'Mira', mode: 'solo', attachments: [image, pdf] }), plannedRounds: 0 });
    el.querySelectorAll<HTMLButtonElement>('.question .att button.body')[1]!.click();
    expect(viewer.current).toEqual(pdf);
    viewer.close();
  });
});

describe('Turn in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  /** The two-round debate of the fixtures, live, which reaches a consensus in round 2. */
  function debate(): TurnView {
    const turn = createLiveTurn({
      requestId: 'req-1',
      question: 'Question?',
      mode: 'debate',
      options: { debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: true },
      conversationId: 7,
    });
    for (const ev of debateEvents()) applyTurnEvent(turn, ev);
    return turn;
  }

  it("in English: a debate's stepper, the scores of each review, its consensus and its totals", () => {
    i18n.set('en');
    const root = render(Turn, { turn: debate(), plannedRounds: 2 });
    expect(root.querySelector('article.turn')?.getAttribute('aria-label')).toBe('Turn: Question?');
    expect(root.querySelector('.stepper ol')?.getAttribute('aria-label')).toBe('Council progress');
    expect([...root.querySelectorAll('.stepper li')].map(textOf)).toEqual([
      'Answers (done)',
      'Review 1 (done)',
      'Review 2 (done)',
      'Synthesis (done)',
    ]);
    expect([...root.querySelectorAll('.cols .card .subtitle')].map(textOf)).toEqual(['Initial answer', 'Initial answer']);
    const rounds = [...root.querySelectorAll('details.round > summary')];
    expect(rounds.map((summary) => textOf(summary.querySelector('.title')))).toEqual(['Review 1', 'Review 2']);
    const scores = (summary: Element) =>
      [...summary.querySelectorAll('.score')].map((score) => [textOf(score.querySelector('b')), textOf(score.querySelector('.sr-only'))]);
    expect(rounds.map(scores)).toEqual([
      [['70', ''], ['90', '(no changes)']],
      [['92', '(no changes)'], ['88', '(no changes)']],
    ]);
    const consensus = root.querySelector('.card.synthesis .chip.good')!;
    expect(textOf(consensus)).toBe('Consensus in round 2 · 92/88');
    expect(consensus.getAttribute('title')).toBe('Final agreement (threshold 85) — Claude: 92, ChatGPT: 88');
    expect(textOf(root.querySelector('.card.synthesis .by'))).toBe('by Claude');
    const used = root.querySelector('.totals .used')!;
    expect(used.getAttribute('title')).toBe('1,250 input · 50 read from the cache · 161 output');
    expect(textOf(used)).toMatch(/^Total 1,461 tokens/);
    expect(textOf(used.querySelector('.sr-only'))).toBe('(1,250 input · 50 read from the cache · 161 output)');
    expect(used.querySelector('b')?.textContent).toBe('1,461');
    expect(textOf(root.querySelector('.totals button.savings'))).toBe('−3.2k tokens saved≈ €0.0034');
    expect(textOf(root.querySelector('.totals .tip strong'))).toBe('Tokens saved in this turn');
  });

  it('in Spanish: how a turn ended', () => {
    i18n.set('es');
    const failed = parts(liveTurn(failedSoloEvents(), 'solo'));
    // The error itself is the server's, in the language of the connection that ran the turn.
    expect(failed.banner).toBe('El turno ha fallado. Claude no ha pogut respondre.');
    expect(failed.claude).toContain('No ha podido responder.');
    expect(failed.totals).toContain('Total 6300 tokens');
    const cancelled = parts(storedTurn(cancelledDuelMessages()));
    expect(cancelled.banner).toBe('Este turno se ha detenido.');
    expect(cancelled.claude).toContain('No ha respondido.');
  });

  it('follows a change of language', () => {
    const root = render(Turn, { turn: debate(), plannedRounds: 2 });
    expect(textOf(root.querySelector('.stepper li'))).toBe('Respostes (fet)');
    i18n.set('en');
    flushSync();
    expect(textOf(root.querySelector('.stepper li'))).toBe('Answers (done)');
    i18n.set('es');
    flushSync();
    expect(textOf(root.querySelector('.card.synthesis .chip.good'))).toBe('Consenso en la ronda 2 · 92/88');
    expect(textOf(root.querySelector('.totals .used b'))).toBe('1461');
  });
});
