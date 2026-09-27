import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Agent, ModelCatalog, ProviderStatus, RuntimeSettings } from './protocol';

// A small fake of the server: /api/models bakes the saved models into
// default_model / fast_model like server/catalog.py effective_models().
const server = vi.hoisted(() => ({
  runtime: null as unknown as RuntimeSettings,
  providerModel: { claude: 'claude-sonnet-provider', chatgpt: 'gpt-provider' } as Record<string, string>,
  modelsFail: false,
}));

vi.mock('./api', () => {
  class ApiError extends Error {
    status = 500;
    retryAfter = null;
  }
  const agents = ['claude', 'chatgpt'] as const;
  return {
    ApiError,
    setUnauthorizedHandler: () => {},
    api: {
      settings: async () => structuredClone(server.runtime),
      saveSettings: async (s: RuntimeSettings) => {
        server.runtime = structuredClone(s);
        return structuredClone(s);
      },
      models: async () => {
        if (server.modelsFail) throw new ApiError('no');
        const out: Record<string, unknown> = {};
        for (const a of agents) {
          out[a] = {
            mode: 'cli',
            default_model: server.runtime.models[a] || server.providerModel[a],
            fast_model: server.runtime.fast_models[a] || `${a}-fast-provider`,
            models: [],
            live: true,
          };
        }
        return out as unknown as ModelCatalog;
      },
      providers: async (): Promise<ProviderStatus[]> =>
        agents.map((a) => ({ agent: a, mode: 'cli', available: true, model: server.providerModel[a]!, detail: '', limits: [] })),
      pricing: async () => ({ fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' }, prices: [] }),
      spend: async () => {
        throw new ApiError('no');
      },
    },
  };
});

import { api } from './api';
import { app } from './app.svelte';
import { DEFAULT_SETTINGS, normalizeSettings } from './settings';

/** The catalog as loaded now (TypeScript keeps the null assigned before the load). */
const catalog = (): ModelCatalog | null => app.catalog;

const withModels = (models: Record<Agent, string | null>, fast: Record<Agent, string | null>) =>
  normalizeSettings({ ...DEFAULT_SETTINGS, models, fast_models: fast });

describe('default models after the owner clears a saved one (F3)', () => {
  beforeEach(async () => {
    server.modelsFail = false;
    server.runtime = withModels({ claude: 'claude-opus-saved', chatgpt: null }, { claude: 'claude-haiku-saved', chatgpt: null });
    app.settings = normalizeSettings(await api.settings());
    app.providers = await api.providers();
    app.catalog = null;
    app.catalogStale = false;
    await app.loadModels();
    expect(app.modelFor('claude')).toBe('claude-opus-saved');
    expect(catalog()?.claude.fast_model).toBe('claude-haiku-saved');
  });

  it('follows the server once the catalog is fetched again', async () => {
    await app.saveSettings(withModels({ claude: null, chatgpt: null }, { claude: null, chatgpt: null }));
    await app.loadModels(); // what the drawer and the model menu call when they open
    expect(app.modelFor('claude')).toBe('claude-sonnet-provider');
    expect(app.defaultModel('claude')).toBe('claude-sonnet-provider');
    expect(catalog()?.claude.default_model).toBe('claude-sonnet-provider');
    expect(catalog()?.claude.fast_model).toBe('claude-fast-provider');
    expect(app.catalogStale).toBe(false);
  });

  it('never shows the cleared model, even while the catalog cannot be fetched', async () => {
    server.modelsFail = true;
    await app.saveSettings(withModels({ claude: null, chatgpt: null }, { claude: 'claude-haiku-saved', chatgpt: null }));
    await app.loadModels();
    expect(app.catalogError).not.toBeNull();
    expect(app.catalogStale).toBe(true);
    expect(app.modelFor('claude')).toBe('claude-sonnet-provider');
  });

  it('shows a newly saved model at once and does not refetch when models did not change', async () => {
    await app.saveSettings(withModels({ claude: 'claude-opus-new', chatgpt: null }, { claude: 'claude-haiku-saved', chatgpt: null }));
    expect(app.modelFor('claude')).toBe('claude-opus-new');
    await app.loadModels();
    const models = vi.spyOn(api, 'models');
    await app.saveSettings(normalizeSettings({ ...server.runtime, use_cache: false }));
    expect(models).not.toHaveBeenCalled();
    models.mockRestore();
  });
});
