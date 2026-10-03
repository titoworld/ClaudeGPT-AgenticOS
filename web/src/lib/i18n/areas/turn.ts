// Texts of views/Chat, Turn, AnswerCard, AnswerMeta, DebateStepper, RevisionRound, SavingsChip,
// StreamStatus, TruncationNote, AgreementMeter, Markdown, PlainText, lib/costs.ts,
// lib/debate-steps.ts, lib/code-blocks.ts, lib/hidden-chars.ts, lib/markdown.ts and the
// synthesis note of lib/turns.svelte.ts. English is the source: Spanish and Catalan have the
// same keys and parameters (TypeScript checks it).

import { plural } from '../index.svelte';

export const en = {
  synthesis: {
    degraded: (who: string) => `The synthesis could not be written: this is ${who}'s last answer.`,
    failed: (missing: string) => `${missing} could not write the synthesis`,
    doneBy: (lead: string, who: string) => `${lead}; ${who} wrote it.`,
    writingBy: (lead: string, who: string) => `${lead}; ${who} is writing it now.`,
    triedBy: (lead: string, who: string) => `${lead}; ${who} tried.`,
  },
  incomplete: 'This turn was not completed.',

  // views/Chat.svelte
  chat: {
    loading: 'Loading the conversation…',
    retry: 'Try again',
    summary: 'Earlier part compacted to save tokens',
    empty: 'This conversation has no messages yet.',
    toBottom: 'Jump to the end',
    /** What screen readers hear while a turn runs, and when it ends. */
    running: 'Generating the answer…',
    finished: 'Answer complete.',
  },

  // Turn.svelte
  label: (question: string) => `Turn: ${question}`,
  attachments: 'Attachments',
  compacting: 'Compacting the history to save tokens…',
  initialAnswer: 'Initial answer',
  failed: 'The turn failed.',
  stopped: 'This turn was stopped.',
  /** The turn's tokens in its footer: **bold** is the number. */
  total: (tokens: string) => `Total **${tokens}** tokens`,
  allCached: 'All from the cache',
  consensus: {
    none: 'No consensus',
    /** Followed by the scores: «Consensus in round 2 · 92/88». */
    reached: (round: number) => `Consensus in round ${round}`,
    /** Followed by each agent's score: «… — Claude: 92, ChatGPT: 88». */
    title: (threshold: number) => `Final agreement (threshold ${threshold})`,
  },

  // AnswerCard.svelte
  card: {
    /** Before the name of the agent that wrote a synthesis. */
    by: 'by',
    noAnswer: 'Did not answer.',
    failed: 'Could not answer.',
    interrupted: 'Answer interrupted.',
  },

  // AnswerMeta.svelte
  meta: {
    model: 'Model',
    tokens: (input: string, output: string) => `${input} → ${output} tokens`,
    tokensTitle: (input: string, output: string) => `${input} input tokens · ${output} output`,
    cacheReadTitle: "Input tokens reused from the provider's cache",
    cacheWriteTitle: 'Input tokens the provider saved to its cache to reuse them',
    latency: 'Total response time',
    ttft: 'Time to first token',
    cached: 'from the cache',
  },

  // Each kind of token a call processed (lib/costs.ts tokenBreakdown, AnswerMeta.svelte).
  tokens: {
    input: (n: string) => `${n} input`,
    cacheRead: (n: string) => `${n} read from the cache`,
    cacheWrite: (n: string) => `${n} written to the cache`,
    output: (n: string) => `${n} output`,
    reasoning: (n: string) => `${n} reasoning`,
  },

  // lib/debate-steps.ts, DebateStepper.svelte (and the round of RevisionRound.svelte)
  steps: {
    label: 'Council progress',
    answers: 'Answers',
    review: (round: number) => `Review ${round}`,
    synthesis: 'Synthesis',
    /** Each step's state, for screen readers. */
    states: { done: 'done', active: 'in progress', pending: 'pending', skipped: 'skipped', failed: 'stopped' },
    skippedByConsensus: 'skipped by consensus',
  },

  // RevisionRound.svelte
  review: {
    of: (agent: string) => `${agent}'s review`,
    unchanged: 'No changes',
    /** After an agent's score, for screen readers. */
    unchangedScore: 'no changes',
    modelNote: 'Note from the model:',
    quote: { open: '“', close: '”' },
    critique: 'Critique',
    error: 'Error',
    revised: 'Revised answer',
    incomplete: 'Incomplete review',
    keepsPrevious: 'The previous answer stays.',
    keptIncomplete: 'The answer kept is incomplete',
  },

  // AgreementMeter.svelte
  agreement: {
    label: 'Agreement',
    of: (agent: string) => `${agent}'s agreement`,
    pending: 'Pending',
    value: (value: number, threshold: number) => `${value} of 100 (threshold ${threshold})`,
    threshold: (threshold: number) => `Consensus threshold: ${threshold}`,
  },

  // StreamStatus.svelte
  status: {
    streaming: 'Writing…',
    waiting: 'Thinking…',
    done: 'Done',
    failed: 'Error',
    truncated: 'Incomplete',
    interrupted: 'Interrupted',
  },

  // TruncationNote.svelte: what is incomplete, before why.
  truncated: 'Incomplete answer',

  // SavingsChip.svelte
  savings: {
    saved: (tokens: string) => `−${tokens} tokens saved`,
    title: 'Tokens saved in this turn',
    kinds: { cache: 'Cache', compaction: 'Compaction', early_stop: 'Stop at consensus', unchanged: 'No changes' },
    total: 'Total',
    value: 'Approximate value',
    valueNote: 'Approximate value: each kind of saving at the price of what it avoided.',
  },

  // lib/costs.ts: what a cost is
  costs: {
    basis: {
      api: 'Real API cost',
      equivalent: 'Equivalent value at API prices — included in the subscription',
    },
    estimated: 'Estimated cost at API prices',
    /** A cost's tooltip: what it is, and the dollars it came from. */
    usd: (what: string, amount: string) => `${what} ($${amount})`,
    turn: 'Turn cost at API prices',
    parts: { api: 'real API cost', equivalent: 'value included in the subscription', other: 'other calls' },
    /** The exchange rate: «$1 = €0.86 · ECB, 25 Sept». */
    fx: (rate: string, source: string) => `$1 = €${rate} · ${source}`,
    ecb: 'ECB',
    manual: 'manual',
  },

  // lib/costs.ts spendLine: the month line of each agent in the sidebar
  spend: {
    spent: 'Spent',
    valueUsed: 'Value used',
    ofBudget: (eur: string, budget: string) => `${eur} of ${budget}`,
    withPlan: (eur: string, plan: string) => `${eur} · plan ${plan}`,
    budgetTitle: (when: string, eur: string, budget: string, percent: string) =>
      `${when}: API spend of ${eur} against a budget of ${budget} (${percent}).`,
    noBudgetTitle: (when: string, eur: string) => `${when}: API spend of ${eur}, with no monthly budget set.`,
    planTitle: (when: string, eur: string, percent: string, plan: string) =>
      `${when}: ${eur} of value used at API prices, ${percent} of the ${plan} the plan costs.`,
    noPlanTitle: (when: string, eur: string) => `${when}: ${eur} of value used at API prices, included in the subscription.`,
    unpriced: (n: number) =>
      plural(n, '1 call with a model of unknown price is not counted.', `${n} calls with models of unknown price are not counted.`),
  },

  // lib/code-blocks.ts: the bar of a code block
  code: {
    /** A block without a language. */
    code: 'code',
    hidden: 'Invisible characters',
    hiddenTitle: (how: string) => `This code contains invisible or direction-control characters that can hide what it does. ${how}`,
    removed: 'The answer had too many to mark them all: the ones in this block were removed.',
    collapsed: 'There were too many to mark them one by one: they were removed, and how many there were is shown instead.',
    marked: 'They show as ⟨U+…⟩ and are copied as such.',
    copy: 'Copy',
    copyCode: 'Copy the code',
    copied: 'Copied',
    error: 'Error',
  },

  // lib/hidden-chars.ts: the marks of hidden characters, and the toast after a copy
  hidden: {
    mark: (codePoint: string) => `Invisible or direction-control character (${codePoint})`,
    collapsed: (count: number) => plural(count, '1 invisible character removed', `${count} invisible characters removed`),
    collapsedTitle: (count: number) =>
      `There were too many invisible or direction-control characters to mark them one by one: ${plural(count, '1 was', `${count} were`)} removed from this block, and they are not copied either.`,
    copiedOne: 'The answer had 1 invisible character: it was copied as ⟨U+…⟩.',
    removedOne: 'The answer had 1 invisible character: it was not copied.',
    copiedAll: (total: number) => `The answer had ${total} invisible characters: they were copied as ⟨U+…⟩.`,
    removedAll: (total: number) => `The answer had ${total} invisible characters: they were not copied.`,
    copiedSome: (total: number, revealed: number, removed: number) =>
      `The answer had ${total} invisible characters: ${revealed} ${plural(revealed, 'was', 'were')} copied as ⟨U+…⟩ and ${removed} ${plural(removed, 'was', 'were')} not.`,
  },

  // lib/markdown.ts: an image of an answer, shown as a link
  markdown: {
    image: 'Image',
    imageNamed: (alt: string) => `Image: ${alt}`,
  },
};

export const es: typeof en = {
  synthesis: {
    degraded: (who: string) => `No se ha podido hacer la síntesis: se muestra la última respuesta de ${who}.`,
    failed: (missing: string) => `${missing} no ha podido hacer la síntesis`,
    doneBy: (lead: string, who: string) => `${lead}; la ha hecho ${who}.`,
    writingBy: (lead: string, who: string) => `${lead}; ahora la hace ${who}.`,
    triedBy: (lead: string, who: string) => `${lead}; lo ha intentado ${who}.`,
  },
  incomplete: 'Este turno no se completó.',

  chat: {
    loading: 'Cargando la conversación…',
    retry: 'Volver a intentarlo',
    summary: 'Parte anterior compactada para ahorrar tokens',
    empty: 'Esta conversación todavía no tiene mensajes.',
    toBottom: 'Bajar al final',
    running: 'Generando la respuesta…',
    finished: 'Respuesta completada.',
  },

  label: (question: string) => `Turno: ${question}`,
  attachments: 'Adjuntos',
  compacting: 'Compactando el historial para ahorrar tokens…',
  initialAnswer: 'Respuesta inicial',
  failed: 'El turno ha fallado.',
  stopped: 'Este turno se ha detenido.',
  total: (tokens: string) => `Total **${tokens}** tokens`,
  allCached: 'Todo desde la caché',
  consensus: {
    none: 'Sin consenso',
    reached: (round: number) => `Consenso en la ronda ${round}`,
    title: (threshold: number) => `Acuerdo final (umbral ${threshold})`,
  },

  card: {
    by: 'por',
    noAnswer: 'No ha respondido.',
    failed: 'No ha podido responder.',
    interrupted: 'Respuesta interrumpida.',
  },

  meta: {
    model: 'Modelo',
    tokens: (input: string, output: string) => `${input} → ${output} tokens`,
    tokensTitle: (input: string, output: string) => `${input} tokens de entrada · ${output} de salida`,
    cacheReadTitle: 'Tokens de entrada reaprovechados de la caché del proveedor',
    cacheWriteTitle: 'Tokens de entrada que el proveedor ha guardado en su caché para reaprovecharlos',
    latency: 'Tiempo total de respuesta',
    ttft: 'Tiempo hasta el primer token',
    cached: 'desde la caché',
  },

  tokens: {
    input: (n: string) => `${n} de entrada`,
    cacheRead: (n: string) => `${n} leídos de la caché`,
    cacheWrite: (n: string) => `${n} escritos en la caché`,
    output: (n: string) => `${n} de salida`,
    reasoning: (n: string) => `${n} de razonamiento`,
  },

  steps: {
    label: 'Progreso del consejo',
    answers: 'Respuestas',
    review: (round: number) => `Revisión ${round}`,
    synthesis: 'Síntesis',
    states: { done: 'hecho', active: 'en curso', pending: 'pendiente', skipped: 'omitido', failed: 'detenido' },
    skippedByConsensus: 'omitido por consenso',
  },

  review: {
    of: (agent: string) => `Revisión de ${agent}`,
    unchanged: 'Sin cambios',
    unchangedScore: 'sin cambios',
    modelNote: 'Nota del modelo:',
    quote: { open: '«', close: '»' },
    critique: 'Crítica',
    error: 'Error',
    revised: 'Respuesta revisada',
    incomplete: 'Revisión incompleta',
    keepsPrevious: 'Se mantiene la respuesta anterior.',
    keptIncomplete: 'La respuesta que se mantiene está incompleta',
  },

  agreement: {
    label: 'Acuerdo',
    of: (agent: string) => `Acuerdo de ${agent}`,
    pending: 'Pendiente',
    value: (value: number, threshold: number) => `${value} de 100 (umbral ${threshold})`,
    threshold: (threshold: number) => `Umbral de consenso: ${threshold}`,
  },

  status: {
    streaming: 'Escribiendo…',
    waiting: 'Pensando…',
    done: 'Hecho',
    failed: 'Error',
    truncated: 'Incompleta',
    interrupted: 'Interrumpido',
  },

  truncated: 'Respuesta incompleta',

  savings: {
    saved: (tokens: string) => `−${tokens} tokens ahorrados`,
    title: 'Tokens ahorrados en este turno',
    kinds: { cache: 'Caché', compaction: 'Compactación', early_stop: 'Parada por consenso', unchanged: 'Sin cambios' },
    total: 'Total',
    value: 'Valor aproximado',
    valueNote: 'Valor aproximado: cada tipo de ahorro al precio de lo que se ha evitado.',
  },

  costs: {
    basis: {
      api: 'Coste real de la API',
      equivalent: 'Valor equivalente a precios de API — incluido en la suscripción',
    },
    estimated: 'Coste estimado a precios de API',
    usd: (what: string, amount: string) => `${what} (${amount} $)`,
    turn: 'Coste del turno a precios de API',
    parts: { api: 'coste real de API', equivalent: 'valor incluido en la suscripción', other: 'otras llamadas' },
    fx: (rate: string, source: string) => `1 $ = ${rate} € · ${source}`,
    ecb: 'BCE',
    manual: 'manual',
  },

  spend: {
    spent: 'Gastado',
    valueUsed: 'Valor aprovechado',
    ofBudget: (eur: string, budget: string) => `${eur} de ${budget}`,
    withPlan: (eur: string, plan: string) => `${eur} · plan ${plan}`,
    budgetTitle: (when: string, eur: string, budget: string, percent: string) =>
      `${when}: gasto de API de ${eur} sobre un presupuesto de ${budget} (${percent}).`,
    noBudgetTitle: (when: string, eur: string) => `${when}: gasto de API de ${eur}, sin presupuesto mensual definido.`,
    planTitle: (when: string, eur: string, percent: string, plan: string) =>
      `${when}: valor aprovechado de ${eur} a precios de API, el ${percent} de los ${plan} que cuesta el plan.`,
    noPlanTitle: (when: string, eur: string) =>
      `${when}: valor aprovechado de ${eur} a precios de API, incluido en la suscripción.`,
    unpriced: (n: number) =>
      plural(n, '1 llamada con un modelo sin precio conocido no cuenta.', `${n} llamadas con modelos sin precio conocido no cuentan.`),
  },

  code: {
    code: 'código',
    hidden: 'Caracteres invisibles',
    hiddenTitle: (how: string) =>
      `Este código contiene caracteres invisibles o de control de dirección que pueden ocultar lo que hace. ${how}`,
    removed: 'La respuesta tenía demasiados para marcarlos todos: los de este bloque se han eliminado.',
    collapsed: 'Había demasiados para marcarlos uno a uno: se han eliminado y en su lugar se muestra cuántos eran.',
    marked: 'Se muestran como ⟨U+…⟩ y se copian igual.',
    copy: 'Copiar',
    copyCode: 'Copiar el código',
    copied: 'Copiado',
    error: 'Error',
  },

  hidden: {
    mark: (codePoint: string) => `Carácter invisible o de control de dirección (${codePoint})`,
    collapsed: (count: number) => plural(count, '1 carácter invisible eliminado', `${count} caracteres invisibles eliminados`),
    collapsedTitle: (count: number) =>
      `Había demasiados caracteres invisibles o de control de dirección para marcarlos uno a uno: ${plural(count, 'se ha eliminado 1', `se han eliminado ${count}`)} de este bloque, que tampoco se copian.`,
    copiedOne: 'La respuesta tenía 1 carácter invisible: se ha copiado como ⟨U+…⟩.',
    removedOne: 'La respuesta tenía 1 carácter invisible: no se ha copiado.',
    copiedAll: (total: number) => `La respuesta tenía ${total} caracteres invisibles: se han copiado como ⟨U+…⟩.`,
    removedAll: (total: number) => `La respuesta tenía ${total} caracteres invisibles: no se han copiado.`,
    copiedSome: (total: number, revealed: number, removed: number) =>
      `La respuesta tenía ${total} caracteres invisibles: ${revealed} ${plural(revealed, 'se ha copiado', 'se han copiado')} como ⟨U+…⟩ y ${removed} no ${plural(removed, 'se ha copiado', 'se han copiado')}.`,
  },

  markdown: {
    image: 'Imagen',
    imageNamed: (alt: string) => `Imagen: ${alt}`,
  },
};

export const ca: typeof en = {
  synthesis: {
    degraded: (who: string) => `No s'ha pogut fer la síntesi: es mostra l'última resposta de ${who}.`,
    failed: (missing: string) => `${missing} no ha pogut fer la síntesi`,
    doneBy: (lead: string, who: string) => `${lead}; l'ha feta ${who}.`,
    writingBy: (lead: string, who: string) => `${lead}; ara la fa ${who}.`,
    triedBy: (lead: string, who: string) => `${lead}; ho ha intentat ${who}.`,
  },
  incomplete: 'Aquest torn no es va completar.',

  chat: {
    loading: 'Carregant la conversa…',
    retry: 'Torna-ho a provar',
    summary: 'Part anterior compactada per estalviar tokens',
    empty: 'Aquesta conversa encara no té missatges.',
    toBottom: 'Baixa al final',
    running: 'Generant la resposta…',
    finished: 'Resposta completada.',
  },

  label: (question: string) => `Torn: ${question}`,
  attachments: 'Adjunts',
  compacting: "Compactant l'historial per estalviar tokens…",
  initialAnswer: 'Resposta inicial',
  failed: 'El torn ha fallat.',
  stopped: "Aquest torn s'ha aturat.",
  total: (tokens: string) => `Total **${tokens}** tokens`,
  allCached: 'Tot des de la memòria cau',
  consensus: {
    none: 'Sense consens',
    reached: (round: number) => `Consens a la ronda ${round}`,
    title: (threshold: number) => `Acord final (llindar ${threshold})`,
  },

  card: {
    by: 'per',
    noAnswer: 'No ha respost.',
    failed: 'No ha pogut respondre.',
    interrupted: 'Resposta interrompuda.',
  },

  meta: {
    model: 'Model',
    tokens: (input: string, output: string) => `${input} → ${output} tokens`,
    tokensTitle: (input: string, output: string) => `${input} tokens d'entrada · ${output} de sortida`,
    cacheReadTitle: "Tokens d'entrada reaprofitats de la memòria cau del proveïdor",
    cacheWriteTitle: "Tokens d'entrada que el proveïdor ha desat a la seva memòria cau per reaprofitar-los",
    latency: 'Temps total de resposta',
    ttft: 'Temps fins al primer token',
    cached: 'des de la memòria cau',
  },

  tokens: {
    input: (n: string) => `${n} d'entrada`,
    cacheRead: (n: string) => `${n} llegits de la memòria cau`,
    cacheWrite: (n: string) => `${n} escrits a la memòria cau`,
    output: (n: string) => `${n} de sortida`,
    reasoning: (n: string) => `${n} de raonament`,
  },

  steps: {
    label: 'Progrés del consell',
    answers: 'Respostes',
    review: (round: number) => `Revisió ${round}`,
    synthesis: 'Síntesi',
    states: { done: 'fet', active: 'en curs', pending: 'pendent', skipped: 'omès', failed: 'aturat' },
    skippedByConsensus: 'omès per consens',
  },

  review: {
    of: (agent: string) => `Revisió de ${agent}`,
    unchanged: 'Sense canvis',
    unchangedScore: 'sense canvis',
    modelNote: 'Nota del model:',
    quote: { open: '«', close: '»' },
    critique: 'Crítica',
    error: 'Error',
    revised: 'Resposta revisada',
    incomplete: 'Revisió incompleta',
    keepsPrevious: 'Es manté la resposta anterior.',
    keptIncomplete: 'La resposta que es manté és incompleta',
  },

  agreement: {
    label: 'Acord',
    of: (agent: string) => `Acord de ${agent}`,
    pending: 'Pendent',
    value: (value: number, threshold: number) => `${value} de 100 (llindar ${threshold})`,
    threshold: (threshold: number) => `Llindar de consens: ${threshold}`,
  },

  status: {
    streaming: 'Escrivint…',
    waiting: 'Pensant…',
    done: 'Fet',
    failed: 'Error',
    truncated: 'Incompleta',
    interrupted: 'Interromput',
  },

  truncated: 'Resposta incompleta',

  savings: {
    saved: (tokens: string) => `−${tokens} tokens estalviats`,
    title: 'Tokens estalviats en aquest torn',
    kinds: { cache: 'Memòria cau', compaction: 'Compactació', early_stop: 'Parada per consens', unchanged: 'Sense canvis' },
    total: 'Total',
    value: 'Valor aproximat',
    valueNote: "Valor aproximat: cada tipus d'estalvi al preu del que s'ha evitat.",
  },

  costs: {
    basis: {
      api: "Cost real de l'API",
      equivalent: "Valor equivalent a preus d'API — inclòs a la subscripció",
    },
    estimated: "Cost estimat a preus d'API",
    usd: (what: string, amount: string) => `${what} (${amount} $)`,
    turn: "Cost del torn a preus d'API",
    parts: { api: "cost real d'API", equivalent: 'valor inclòs a la subscripció', other: 'altres crides' },
    fx: (rate: string, source: string) => `1 $ = ${rate} € · ${source}`,
    ecb: 'BCE',
    manual: 'manual',
  },

  spend: {
    spent: 'Gastat',
    valueUsed: 'Valor aprofitat',
    ofBudget: (eur: string, budget: string) => `${eur} de ${budget}`,
    withPlan: (eur: string, plan: string) => `${eur} · pla ${plan}`,
    budgetTitle: (when: string, eur: string, budget: string, percent: string) =>
      `${when}: despesa d'API de ${eur} sobre un pressupost de ${budget} (${percent}).`,
    noBudgetTitle: (when: string, eur: string) => `${when}: despesa d'API de ${eur}, sense pressupost mensual definit.`,
    planTitle: (when: string, eur: string, percent: string, plan: string) =>
      `${when}: valor aprofitat de ${eur} a preus d'API, el ${percent} dels ${plan} que costa el pla.`,
    noPlanTitle: (when: string, eur: string) => `${when}: valor aprofitat de ${eur} a preus d'API, inclòs a la subscripció.`,
    unpriced: (n: number) =>
      plural(n, '1 crida amb un model sense preu conegut no hi compta.', `${n} crides amb models sense preu conegut no hi compten.`),
  },

  code: {
    code: 'codi',
    hidden: 'Caràcters invisibles',
    hiddenTitle: (how: string) =>
      `Aquest codi conté caràcters invisibles o de control de direcció que poden amagar què fa. ${how}`,
    removed: "La resposta en tenia massa per marcar-los tots: els d'aquest bloc s'han eliminat.",
    collapsed: "N'hi havia massa per marcar-los un per un: s'han eliminat i en lloc seu es mostra quants eren.",
    marked: 'Es mostren com a ⟨U+…⟩ i es copien igual.',
    copy: 'Copia',
    copyCode: 'Copia el codi',
    copied: 'Copiat',
    error: 'Error',
  },

  hidden: {
    mark: (codePoint: string) => `Caràcter invisible o de control de direcció (${codePoint})`,
    collapsed: (count: number) => plural(count, '1 caràcter invisible eliminat', `${count} caràcters invisibles eliminats`),
    collapsedTitle: (count: number) =>
      `Hi havia massa caràcters invisibles o de control de direcció per marcar-los un per un: ${plural(count, "se n'ha eliminat 1", `se n'han eliminat ${count}`)} d'aquest bloc, que tampoc no es copien.`,
    copiedOne: "La resposta tenia 1 caràcter invisible: s'ha copiat com a ⟨U+…⟩.",
    removedOne: "La resposta tenia 1 caràcter invisible: no s'ha copiat.",
    copiedAll: (total: number) => `La resposta tenia ${total} caràcters invisibles: s'han copiat com a ⟨U+…⟩.`,
    removedAll: (total: number) => `La resposta tenia ${total} caràcters invisibles: no s'han copiat.`,
    copiedSome: (total: number, revealed: number, removed: number) =>
      `La resposta tenia ${total} caràcters invisibles: ${revealed} s'han copiat com a ⟨U+…⟩ i ${removed} no s'han copiat.`,
  },

  markdown: {
    image: 'Imatge',
    imageNamed: (alt: string) => `Imatge: ${alt}`,
  },
};
