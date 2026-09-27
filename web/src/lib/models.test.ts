import { describe, expect, it } from 'vitest';
import {
  findModel,
  isValidModelId,
  modelHint,
  modelOptionLabel,
  modelOverridesPayload,
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
});
