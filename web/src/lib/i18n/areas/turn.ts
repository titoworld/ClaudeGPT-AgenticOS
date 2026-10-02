// Texts of views/Chat, Turn, AnswerCard, AnswerMeta, DebateStepper, RevisionRound, SavingsChip,
// StreamStatus, TruncationNote, AgreementMeter, Markdown, PlainText, lib/costs.ts,
// lib/debate-steps.ts, lib/code-blocks.ts, lib/hidden-chars.ts, lib/markdown.ts and the
// synthesis note of lib/turns.svelte.ts. English is the source: Spanish and Catalan have the
// same keys and parameters (TypeScript checks it).

export const en = {
  synthesis: {
    degraded: (who: string) => `The synthesis could not be written: this is ${who}'s last answer.`,
    failed: (missing: string) => `${missing} could not write the synthesis`,
    doneBy: (lead: string, who: string) => `${lead}; ${who} wrote it.`,
    writingBy: (lead: string, who: string) => `${lead}; ${who} is writing it now.`,
    triedBy: (lead: string, who: string) => `${lead}; ${who} tried.`,
  },
  incomplete: 'This turn was not completed.',
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
};
