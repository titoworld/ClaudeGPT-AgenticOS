import { describe, expect, it } from 'vitest';
import { i18n } from './i18n/index.svelte';
import {
  findModel,
  isValidModelId,
  modelHint,
  modelOptionLabel,
  modelOverridesPayload,
  normalizeModel,
  parseOverrides,
  shortModel,
  validateModelId,
} from './models';
import type { AgentModels } from './protocol';

describe('validateModelId (MODEL_ID_PATTERN)', () => {
  it.each([
    'gpt-6-sol',
    'claude-opus-5-5',
    'opus',
    'claude-opus-5-5[1m]',
    'anthropic/claude-sonnet-5',
    'us.anthropic.claude-opus-5-5-20260101-v1:0',
    'model@2026-01-01',
    '4o',
    'a'.repeat(100),
  ])('accepts %s', (id) => {
    expect(validateModelId(id)).toBeNull();
    expect(isValidModelId(id)).toBe(true);
  });

  it('trims surrounding spaces', () => {
    expect(validateModelId('  gpt-6-sol  ')).toBeNull();
  });

  it.each([
    ['', "Escriu l'identificador del model."],
    ['   ', "Escriu l'identificador del model."],
    ['a'.repeat(101), 'Com a màxim 100 caràcters.'],
  ])('rejects %j with a Catalan message', (id, message) => {
    expect(validateModelId(id)).toBe(message);
  });

  it.each(['-leading-dash', '.hidden', 'gpt 6', 'model;rm -rf', 'emoji🙂', 'quote"s', 'back\\slash', '_x'])(
    'rejects %s',
    (id) => {
      expect(validateModelId(id)).toMatch(/^Identificador no vàlid/);
    },
  );
});

describe('modelOverridesPayload (turn.start "models")', () => {
  it('is undefined when nothing is overridden', () => {
    expect(modelOverridesPayload({})).toBeUndefined();
    expect(modelOverridesPayload({ claude: null, chatgpt: undefined })).toBeUndefined();
    expect(modelOverridesPayload({ claude: '  ' })).toBeUndefined();
  });

  it('sends only explicit, valid choices, trimmed', () => {
    expect(modelOverridesPayload({ claude: ' opus ', chatgpt: null })).toEqual({ claude: 'opus' });
    expect(modelOverridesPayload({ claude: 'opus', chatgpt: 'gpt-6-sol' })).toEqual({
      claude: 'opus',
      chatgpt: 'gpt-6-sol',
    });
    expect(modelOverridesPayload({ claude: 'bad id', chatgpt: 'gpt-6-sol' })).toEqual({ chatgpt: 'gpt-6-sol' });
  });

  it('keeps only the agents taking part in the turn', () => {
    const choices = { claude: 'opus', chatgpt: 'gpt-6-sol' };
    expect(modelOverridesPayload(choices, ['chatgpt'])).toEqual({ chatgpt: 'gpt-6-sol' });
    expect(modelOverridesPayload({ claude: 'opus' }, ['chatgpt'])).toBeUndefined();
  });
});

describe('parseOverrides (localStorage)', () => {
  it('reads valid stored choices', () => {
    expect(parseOverrides('{"claude":"opus","chatgpt":"gpt-6-sol"}')).toEqual({ claude: 'opus', chatgpt: 'gpt-6-sol' });
  });

  it('drops malformed data', () => {
    expect(parseOverrides(null)).toEqual({});
    expect(parseOverrides('not json')).toEqual({});
    expect(parseOverrides('["opus"]')).toEqual({});
    expect(parseOverrides('{"claude":42,"chatgpt":"bad id","gemini":"x"}')).toEqual({});
  });
});

describe('labels', () => {
  it('shortens model ids for the composer', () => {
    expect(shortModel('claude-opus-5-5')).toBe('opus-5-5');
    expect(shortModel('claude-sonnet-5-20260115')).toBe('sonnet-5');
    expect(shortModel('anthropic/claude-haiku-4-latest')).toBe('haiku-4');
    expect(shortModel('claude-opus-5-5[1m]')).toBe('opus-5-5');
    expect(shortModel('gpt-6-sol')).toBe('gpt-6-sol');
    expect(shortModel('opus')).toBe('opus');
  });

  const catalog: AgentModels = {
    mode: 'cli',
    default_model: 'opus',
    fast_model: 'haiku',
    live: true,
    models: [
      { id: 'opus', label: 'Claude Opus 5.5', description: 'El més capaç', is_default: true, context_window: 1_000_000 },
      { id: 'haiku', label: 'haiku', description: '', is_default: false, context_window: null },
    ],
  };

  it('finds listed models and describes them', () => {
    expect(findModel(catalog, 'opus')?.label).toBe('Claude Opus 5.5');
    expect(findModel(catalog, 'nou-model')).toBeNull();
    expect(findModel(null, 'opus')).toBeNull();
    expect(modelOptionLabel(catalog.models[0]!)).toBe('Claude Opus 5.5 · opus');
    expect(modelOptionLabel(catalog.models[1]!)).toBe('haiku');
    expect(modelHint(catalog.models[0]!)).toBe('El més capaç · 1M tokens de context');
    expect(modelHint(catalog.models[1]!)).toBe('');
  });

  it("in English and Spanish (the description is the server's, already in the language of the request)", () => {
    const model = { ...catalog.models[0]!, description: 'The most capable', context_window: 200_000 };
    try {
      i18n.set('en');
      expect(modelHint(model)).toBe('The most capable · 200k tokens of context');
      expect(validateModelId('')).toBe('Type the model ID.');
      expect(validateModelId('a'.repeat(101))).toBe('At most 100 characters.');
      expect(validateModelId('gpt 6')).toMatch(/^Not a valid ID: no spaces/);
      i18n.set('es');
      expect(modelHint({ ...model, description: 'El más capaz', context_window: 1_500_000 })).toBe('El más capaz · 1,5M tokens de contexto');
      expect(validateModelId(' ')).toBe('Escribe el identificador del modelo.');
      expect(validateModelId('_x')).toMatch(/^Identificador no válido: sin espacios/);
    } finally {
      i18n.set('ca');
    }
  });
});

describe('normalizeModel (pricing.normalize_model, N13)', () => {
  // tests/fixtures/model_ids.json: the vectors pytest checks agentic_os.pricing.normalize_model with.
  const node = globalThis as unknown as {
    process: { getBuiltinModule(id: 'node:fs'): { readFileSync(path: string, encoding: 'utf8'): string } };
  };
  const dir = (import.meta as ImportMeta & { dirname: string }).dirname;
  const file = `${dir}/../../../tests/fixtures/model_ids.json`;
  const { vectors } = JSON.parse(node.process.getBuiltinModule('node:fs').readFileSync(file, 'utf8')) as {
    vectors: { id: string; key: string }[];
  };

  it('has the shared vectors', () => {
    expect(vectors.length).toBeGreaterThan(20);
  });

  it.each(vectors)('gives $id the key $key, like the server', ({ id, key }) => {
    expect(normalizeModel(id)).toBe(key);
  });

  // What the Python function gives beyond valid ids: str.strip() whitespace, any
  // Unicode digit for \d, and `$` that also matches before a final newline.
  it.each([
    ['\x1cclaude-opus-5\x85', 'claude-opus-5'],
    ['\ufeffclaude-opus-5', '\ufeffclaude-opus-5'],
    ['claude-opus-5-\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668', 'claude-opus-5'],
    ['foo-latest\n[1m]', 'foo\n'],
    ['a[b][c]', 'a[b]'],
    ['x/[1m]', ''],
    ['claude-opus-5[1m]-latest', 'claude-opus-5[1m]'],
    ['claude-opus-5-latest-latest', 'claude-opus-5-latest'],
  ])('keeps the Python semantics for %j', (id, key) => {
    expect(normalizeModel(id)).toBe(key);
  });
});
