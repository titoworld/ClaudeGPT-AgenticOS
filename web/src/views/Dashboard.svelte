<script lang="ts">
  /**
   * Usage dashboard: consumption, savings and performance of both agents, plus
   * subscription limits. Data: api.stats(days) and api.providers().
   */
  import { onMount, untrack } from 'svelte';
  import { api, ApiError } from '../lib/api';
  import ChartCard from '../lib/charts/ChartCard.svelte';
  import HBars from '../lib/charts/HBars.svelte';
  import Meter from '../lib/charts/Meter.svelte';
  import StackedColumns from '../lib/charts/StackedColumns.svelte';
  import { chartTable } from '../lib/charts/table';
  import {
    AGENT_SERIES,
    COUNT_SERIES,
    dailySavings,
    dailyTokens,
    formatCompact,
    formatDuration,
    isEmpty,
    kpis,
    latencyData,
    MODE_LABEL,
    plural,
    providerCards,
    resetLabel,
    SAVING_KINDS,
    SAVING_LABEL,
    SAVING_SERIES,
    TURN_MODES,
    turnsData,
  } from '../lib/charts/usage';
  import { AGENT_LABEL, formatInt, formatPercent, formatTime, formatTokens, formatUsd } from '../lib/format';
  import { AGENTS, type ProviderStatus, type SavingKind, type Stats } from '../lib/protocol';

  const RANGES = [7, 30, 90] as const;
  type Range = (typeof RANGES)[number];

  const TECHNIQUE_TEXT: Record<SavingKind, string> = {
    cache:
      'Una pregunta idèntica (mateix mode, agents i context) es respon des de la memòria cau, sense cridar cap model.',
    compaction:
      'Quan l’historial creix massa, els missatges antics es resumeixen amb un model ràpid i només es conserven els últims.',
    early_stop: 'Si tots dos agents superen el llindar d’acord, s’ometen les rondes de debat que quedaven.',
    unchanged: 'Un agent que ja està d’acord ho indica en lloc de reescriure tota la resposta.',
  };

  const decimal = new Intl.NumberFormat('ca-ES', { maximumFractionDigits: 1 });

  let days = $state<Range>(30);
  let stats = $state.raw<Stats | null>(null);
  let providers = $state.raw<ProviderStatus[] | null>(null);
  let statsError = $state<string | null>(null);
  let providersError = $state<string | null>(null);
  let loading = $state(false);
  let updatedAt = $state<Date | null>(null);
  let now = $state(new Date());
  let seq = 0;

  function messageOf(err: unknown): string {
    if (err instanceof ApiError) return err.status === 401 ? 'La sessió ha caducat.' : err.message;
    return 'Comprova la connexió i torna-ho a provar.';
  }

  async function load(range: number, withProviders: boolean): Promise<void> {
    const id = ++seq;
    loading = true;
    const [s, p] = await Promise.allSettled([api.stats(range), withProviders ? api.providers() : Promise.resolve(null)]);
    if (id !== seq) return; // a newer request superseded this one
    if (s.status === 'fulfilled') {
      stats = s.value;
      statsError = null;
    } else {
      statsError = messageOf(s.reason);
    }
    if (withProviders) {
      if (p.status === 'fulfilled') {
        providers = p.value;
        providersError = null;
      } else {
        providersError = messageOf(p.reason);
      }
    }
    now = new Date();
    if (s.status === 'fulfilled') updatedAt = now;
    loading = false;
  }

  function refresh(): void {
    void load(days, true);
  }

  // Reload statistics whenever the range changes (providers only on first load
  // and on refresh). untrack: load() reads state that must not become a dependency.
  $effect(() => {
    const range = days;
    untrack(() => void load(range, providers === null));
  });

  // Keep "resets in ..." labels fresh.
  onMount(() => {
    const timer = window.setInterval(() => (now = new Date()), 60_000);
    return () => window.clearInterval(timer);
  });

  const k = $derived(stats ? kpis(stats) : null);
  const tokenDays = $derived(stats ? dailyTokens(stats) : []);
  const savingDays = $derived(stats ? dailySavings(stats) : []);
  const latency = $derived(stats ? latencyData(stats) : []);
  const turns = $derived(stats ? turnsData(stats) : []);
  const cards = $derived(providerCards(providers));

  const tokensTable = $derived(
    chartTable(tokenDays, AGENT_SERIES, {
      caption: 'Tokens consumits per dia i agent',
      categoryLabel: 'Dia',
      format: formatInt,
      total: true,
      skipEmpty: true,
    }),
  );
  const savingsTable = $derived(
    chartTable(savingDays, SAVING_SERIES, {
      caption: 'Tokens estalviats per dia i tècnica',
      categoryLabel: 'Dia',
      format: formatInt,
      total: true,
      skipEmpty: true,
    }),
  );
  const latencyTable = $derived(
    chartTable(latency, AGENT_SERIES, { caption: 'Latència per agent', categoryLabel: 'Mesura', format: formatDuration }),
  );
  const turnsTable = $derived(
    chartTable(turns, COUNT_SERIES, { caption: 'Torns per mode', categoryLabel: 'Mode', format: formatInt }),
  );

  const updatedText = $derived(
    loading ? 'Actualitzant…' : updatedAt ? `Actualitzat a les ${formatTime(updatedAt.toISOString())}` : '',
  );
</script>

<section class="dashboard" aria-labelledby="dashboard-title" aria-busy={loading}>
  <header class="top">
    <div class="heading">
      <h2 id="dashboard-title">Ús i estalvi</h2>
      <p>Consum, estalvi i rendiment de Claude i ChatGPT</p>
    </div>
    <div class="controls">
      <fieldset class="range">
        <legend class="sr-only">Període</legend>
        {#each RANGES as r (r)}
          <label>
            <input type="radio" name="dashboard-range" value={r} bind:group={days} />
            <span>{r} dies</span>
          </label>
        {/each}
      </fieldset>
      <button type="button" class="refresh" onclick={refresh} disabled={loading}>
        <svg viewBox="0 0 16 16" class:spin={loading} aria-hidden="true">
          <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3" />
        </svg>
        Actualitza
      </button>
    </div>
  </header>
  <p class="updated" role="status">{updatedText}</p>

  {#if statsError && stats}
    <div class="banner" role="alert">
      <span>No s’han pogut actualitzar les estadístiques. {statsError}</span>
      <button type="button" onclick={refresh}>Torna-ho a provar</button>
    </div>
  {/if}

  {#if !stats || !k}
    <div class="state" class:error={!!statsError}>
      {#if statsError}
        <p role="alert">No s’han pogut carregar les estadístiques. {statsError}</p>
        <button type="button" onclick={refresh}>Torna-ho a provar</button>
      {:else}
        <span class="loader" aria-hidden="true"></span>
        <p>Carregant les estadístiques…</p>
      {/if}
    </div>
  {:else}
    <div class="content" class:stale={loading}>
      <!-- KPI tiles -->
      <section class="kpis" aria-label="Resum del període">
        <article class="tile hero">
          <h3>Tokens estalviats</h3>
          <p class="value">{formatTokens(k.saved.total)}</p>
          {#if k.saved.ratio != null}
            <p class="sub">
              <strong>{formatPercent(k.saved.ratio)}</strong> menys del que s’hauria gastat sense optimitzacions
            </p>
            <Meter
              value={k.saved.ratio * 100}
              tone="neutral"
              label="Proporció estalviada"
              valueText="{formatPercent(k.saved.ratio)} estalviat"
            />
            <ul class="breakdown" aria-label="Estalvi per tècnica">
              {#each SAVING_KINDS as kind (kind)}
                <li>
                  <span class="swatch" style:background="var(--saving-{kind.replace('_', '-')})"></span>
                  <span class="name">{SAVING_LABEL[kind]}</span>
                  <strong>{formatTokens(k.saved.byKind[kind])}</strong>
                </li>
              {/each}
            </ul>
          {:else}
            <p class="sub">Encara no hi ha consum en aquest període.</p>
          {/if}
        </article>

        <article class="tile">
          <h3>Tokens consumits</h3>
          <p class="value">{formatTokens(k.consumed.total)}</p>
          {#if k.consumed.total > 0}
            <div class="split" aria-hidden="true">
              {#each AGENTS as a (a)}
                {#if k.consumed.byAgent[a] > 0}
                  <span style:flex-grow={k.consumed.byAgent[a]} style:background="var(--{a})"></span>
                {/if}
              {/each}
            </div>
          {/if}
          <ul class="agents">
            {#each AGENTS as a (a)}
              <li><span class="dot" style:background="var(--{a})"></span>{AGENT_LABEL[a]}<strong>{formatTokens(k.consumed.byAgent[a])}</strong></li>
            {/each}
          </ul>
          <p class="sub">
            {plural(k.consumed.calls, 'crida', 'crides', formatInt(k.consumed.calls))} · {plural(
              k.consumed.errors,
              'error',
              'errors',
              formatInt(k.consumed.errors),
            )}
          </p>
        </article>

        <article class="tile">
          <h3>Cost estimat</h3>
          <p class="value">{k.cost.total == null ? '—' : formatUsd(k.cost.total)}</p>
          {#if k.cost.total == null}
            <p class="sub">Només es calcula en mode API. Amb la subscripció no hi ha cost per token.</p>
          {:else}
            <ul class="agents">
              {#each AGENTS as a (a)}
                <li><span class="dot" style:background="var(--{a})"></span>{AGENT_LABEL[a]}<strong>{formatUsd(k.cost.byAgent[a])}</strong></li>
              {/each}
            </ul>
          {/if}
        </article>

        <article class="tile">
          <h3>Torns</h3>
          <p class="value">{formatInt(k.turns.total)}</p>
          <p class="sub">
            {TURN_MODES.map((m) => `${MODE_LABEL[m]} ${formatInt(k.turns.byMode[m])}`).join(' · ')}
          </p>
        </article>

        <article class="tile">
          <h3>Debats amb consens</h3>
          <p class="value">{k.consensus.rate == null ? '—' : formatPercent(k.consensus.rate)}</p>
          {#if k.consensus.debates > 0}
            <p class="sub">
              {formatInt(k.consensus.reached)} de {plural(k.consensus.debates, 'debat', 'debats', formatInt(k.consensus.debates))}
              {#if k.consensus.avgRounds != null}
                · {plural(k.consensus.avgRounds, 'ronda', 'rondes', decimal.format(k.consensus.avgRounds))} de mitjana
              {/if}
            </p>
          {:else}
            <p class="sub">Encara no hi ha debats en aquest període.</p>
          {/if}
        </article>

        <article class="tile latency">
          <h3>Latència</h3>
          <table>
            <thead>
              <tr>
                <th scope="col"><span class="sr-only">Agent</span></th>
                <th scope="col">p50</th>
                <th scope="col">p95</th>
                <th scope="col"><abbr title="Temps fins al primer token (p50)">1r token</abbr></th>
              </tr>
            </thead>
            <tbody>
              {#each AGENTS as a (a)}
                <tr>
                  <th scope="row"><span class="dot" style:background="var(--{a})"></span>{AGENT_LABEL[a]}</th>
                  <td>{formatDuration(k.latency[a].p50)}</td>
                  <td>{formatDuration(k.latency[a].p95)}</td>
                  <td>{formatDuration(k.latency[a].ttft)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </article>
      </section>

      <!-- Charts -->
      <section class="charts" aria-label="Gràfics">
        <ChartCard
          title="Tokens per dia"
          subtitle="Entrada i sortida de cada agent"
          legend={AGENT_SERIES}
          table={tokensTable}
          empty={isEmpty(tokenDays)}
          emptyText="Encara no hi ha consum en aquest període."
        >
          <StackedColumns
            data={tokenDays}
            series={AGENT_SERIES}
            label="Tokens consumits per dia i agent"
            format={formatInt}
            tickFormat={formatCompact}
          />
        </ChartCard>

        <ChartCard
          title="Estalvi per dia"
          subtitle="Tokens estalviats per cada tècnica"
          legend={SAVING_SERIES}
          table={savingsTable}
          empty={isEmpty(savingDays)}
          emptyText="Encara no hi ha estalvis en aquest període. Apareixeran amb la memòria cau, la compactació o el consens."
        >
          <StackedColumns
            data={savingDays}
            series={SAVING_SERIES}
            label="Tokens estalviats per dia i tècnica"
            format={formatInt}
            tickFormat={formatCompact}
          />
        </ChartCard>

        <ChartCard
          title="Latència per agent"
          subtitle="Temps de resposta complet: mediana i percentil 95"
          legend={AGENT_SERIES}
          table={latencyTable}
          empty={isEmpty(latency)}
          emptyText="Encara no hi ha respostes per mesurar la latència."
        >
          <HBars data={latency} series={AGENT_SERIES} label="Latència per agent" format={formatDuration} />
        </ChartCard>

        <ChartCard
          title="Torns per mode"
          subtitle="Quantes vegades has fet servir cada mode"
          table={turnsTable}
          empty={isEmpty(turns)}
          emptyText="Encara no hi ha torns en aquest període."
        >
          <HBars data={turns} series={COUNT_SERIES} label="Torns per mode" format={formatInt} />
        </ChartCard>
      </section>

      <!-- Subscription limits -->
      <section class="panel" aria-labelledby="limits-title">
        <div class="panel-head">
          <h3 id="limits-title">Límits de subscripció</h3>
          <p>Ús de les finestres de cada agent i quan es restableixen.</p>
        </div>
        {#if providersError && cards.length === 0}
          <p class="muted" role="alert">No s’ha pogut consultar l’estat dels agents. {providersError}</p>
        {:else if providers === null}
          <p class="muted">Consultant els agents…</p>
        {:else if cards.length === 0}
          <p class="muted">No hi ha cap agent configurat.</p>
        {:else}
          <div class="providers">
            {#each cards as c (c.agent)}
              <article class="provider">
                <header>
                  <span class="dot" style:background="var(--{c.agent})"></span>
                  <h4>{c.name}</h4>
                  <span class="badge">{c.modeLabel}</span>
                  <span class="availability" class:ok={c.available}>
                    <svg viewBox="0 0 16 16" aria-hidden="true">
                      {#if c.available}<path d="M3.5 8.5l3 3 6-7" />{:else}<path d="M4.5 4.5l7 7m0-7l-7 7" />{/if}
                    </svg>
                    {c.available ? 'Disponible' : 'No disponible'}
                  </span>
                </header>
                {#if c.model}<p class="model">{c.model}</p>{/if}
                {#if c.detail}<p class="detail">{c.detail}</p>{/if}
                {#if c.limits.length > 0}
                  <ul class="limits">
                    {#each c.limits as l (l.key)}
                      {@const reset = resetLabel(l.resetsAt, now)}
                      <li>
                        <div class="limit-head">
                          <span>{l.window}</span>
                          <span class="pct">{l.usedPercent == null ? 'Ús desconegut' : `${Math.round(l.usedPercent)} %`}</span>
                        </div>
                        {#if l.usedPercent != null}
                          <Meter value={l.usedPercent} tone={l.tone} label="{c.name}, {l.window.toLowerCase()}" />
                        {/if}
                        <div class="limit-foot">
                          <span class="tone {l.tone}">
                            <svg viewBox="0 0 16 16" aria-hidden="true">
                              {#if l.tone === 'ok'}
                                <path d="M3.5 8.5l3 3 6-7" />
                              {:else if l.tone === 'warning'}
                                <path d="M8 2.5l6 11H2zM8 6.5v3.5M8 12v.01" />
                              {:else if l.tone === 'critical'}
                                <path d="M8 1.8a6.2 6.2 0 1 0 0 12.4A6.2 6.2 0 0 0 8 1.8zM5.5 5.5l5 5m0-5l-5 5" />
                              {:else}
                                <path d="M8 1.8a6.2 6.2 0 1 0 0 12.4A6.2 6.2 0 0 0 8 1.8zM8 11.5v.01M6.3 6.3a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.6-.7 1.1" />
                              {/if}
                            </svg>
                            {l.statusLabel}
                          </span>
                          {#if reset}<span class="reset">{reset}</span>{/if}
                        </div>
                      </li>
                    {/each}
                  </ul>
                {:else}
                  <p class="muted">
                    {c.mode === 'api'
                      ? 'Amb clau d’API no hi ha límits de subscripció: es paga per token.'
                      : 'Encara no hi ha informació de límits. Apareixerà després de la primera resposta.'}
                  </p>
                {/if}
              </article>
            {/each}
          </div>
        {/if}
      </section>

      <!-- Saving techniques -->
      <section class="panel" aria-labelledby="techniques-title">
        <div class="panel-head">
          <h3 id="techniques-title">Com estalviem tokens</h3>
          <p>Les tècniques que redueixen el consum sense perdre qualitat.</p>
        </div>
        <ul class="techniques">
          {#each SAVING_KINDS as kind (kind)}
            <li>
              <span class="swatch" style:background="var(--saving-{kind.replace('_', '-')})"></span>
              <div>
                <h4>{SAVING_LABEL[kind]}</h4>
                <p>{TECHNIQUE_TEXT[kind]}</p>
              </div>
            </li>
          {/each}
          <li>
            <span class="swatch neutral"></span>
            <div>
              <h4>Memòria cau del proveïdor</h4>
              <p>
                El prompt de sistema va primer i no canvia, així Anthropic i OpenAI en reaprofiten el càlcul. En aquest
                període s’han llegit <strong>{formatTokens(k.consumed.cacheRead)}</strong> tokens de la seva memòria cau, més
                barats i no comptats com a estalvi.
              </p>
            </div>
          </li>
        </ul>
      </section>
    </div>
  {/if}
</section>

<style>
  .dashboard {
    container-type: inline-size;
    display: grid;
    gap: 16px;
    width: 100%;
    max-width: 1320px;
    margin: 0 auto;
    padding: clamp(16px, 3vw, 32px);
    box-sizing: border-box;
    color: var(--text-primary);
    font-family: var(--font-sans);
  }

  .top {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: flex-end;
    justify-content: space-between;
  }

  .heading h2 {
    margin: 0;
    font-size: var(--text-xl);
    font-weight: 650;
    letter-spacing: -0.01em;
  }

  .heading p {
    margin: 4px 0 0;
    color: var(--text-secondary);
    font-size: var(--text-sm);
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .range {
    display: inline-flex;
    margin: 0;
    padding: 3px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-1);
  }

  .range label {
    position: relative;
    cursor: pointer;
  }

  .range input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }

  .range span {
    display: block;
    padding: 5px 12px;
    border-radius: 999px;
    color: var(--text-secondary);
    font-size: var(--text-sm);
    transition:
      background var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }

  .range label:hover span {
    color: var(--text-primary);
  }

  .range input:checked + span {
    background: var(--surface-3);
    color: var(--text-primary);
    box-shadow: inset 0 0 0 1px var(--border-strong);
  }

  .range input:focus-visible + span {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }

  button {
    font: inherit;
  }

  .refresh,
  .banner button,
  .state button {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    padding: 6px 14px;
    border: 1px solid var(--border-strong);
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--text-primary);
    font-size: var(--text-sm);
    cursor: pointer;
    transition: background var(--dur-fast) var(--ease-out);
  }

  .refresh:hover:not(:disabled),
  .banner button:hover,
  .state button:hover {
    background: var(--surface-3);
  }

  .refresh:disabled {
    cursor: progress;
    opacity: 0.7;
  }

  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .refresh svg {
    width: 15px;
    height: 15px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .spin {
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .updated {
    min-height: 1.2em;
    margin: -8px 0 0;
    color: var(--text-muted);
    font-size: var(--text-xs);
  }

  .banner {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border: 1px solid color-mix(in srgb, var(--critical) 45%, transparent);
    border-radius: var(--radius-md);
    background: color-mix(in srgb, var(--critical) 10%, var(--surface-1));
    color: var(--text-primary);
    font-size: var(--text-sm);
  }

  .state {
    display: grid;
    gap: 12px;
    place-items: center;
    align-content: center;
    min-height: 280px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface-1);
    color: var(--text-secondary);
    text-align: center;
  }

  .state p {
    max-width: 48ch;
    margin: 0;
  }

  .loader {
    width: 22px;
    height: 22px;
    border: 2px solid var(--border-strong);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }

  .content {
    display: grid;
    gap: 16px;
    transition: opacity var(--dur-med) var(--ease-out);
  }

  /* Refetch keeps the frame: previous render stays, dimmed. */
  .content.stale {
    opacity: 0.55;
  }

  /* ------------------------------------------------------------ KPI tiles */

  .kpis {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  @container (min-width: 520px) {
    .kpis {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .hero {
      grid-column: span 2;
    }
  }

  /* Hero spans 2x2; the other five tiles fill the remaining 3x3 grid. */
  @container (min-width: 900px) {
    .kpis {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }

    .hero {
      grid-row: span 2;
    }
  }

  .tile {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
    padding: 16px 18px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface-1);
  }

  .tile h3 {
    margin: 0;
    color: var(--text-secondary);
    font-size: var(--text-sm);
    font-weight: 500;
  }

  .value {
    margin: 0;
    color: var(--text-primary);
    font-size: 1.875rem;
    font-weight: 600;
    line-height: 1.1;
    letter-spacing: -0.01em;
  }

  .sub {
    margin: 0;
    color: var(--text-muted);
    font-size: var(--text-xs);
    line-height: 1.45;
  }

  .sub strong {
    color: var(--text-primary);
    font-weight: 600;
  }

  .hero {
    justify-content: space-between;
    background:
      radial-gradient(120% 90% at 100% 0%, var(--chatgpt-soft), transparent 60%),
      radial-gradient(120% 90% at 0% 100%, var(--claude-soft), transparent 55%),
      var(--surface-1);
  }

  .hero .value {
    font-size: 3.25rem;
    line-height: 1;
  }

  .hero .sub {
    color: var(--text-secondary);
    font-size: var(--text-sm);
  }

  .breakdown {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 6px 20px;
    margin: 6px 0 0;
    padding: 0;
    list-style: none;
    font-size: var(--text-xs);
  }

  @container (min-width: 520px) {
    .breakdown {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  .breakdown li {
    display: flex;
    gap: 8px;
    align-items: center;
    min-width: 0;
  }

  .breakdown .swatch {
    margin-top: 0;
  }

  .breakdown .name {
    overflow: hidden;
    color: var(--text-secondary);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .breakdown strong {
    margin-left: auto;
    color: var(--text-primary);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .split {
    display: flex;
    gap: 2px;
    height: 6px;
    overflow: hidden;
    border-radius: 999px;
  }

  .split span {
    flex-basis: 0;
    min-width: 2px;
  }

  .agents {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
    color: var(--text-secondary);
    font-size: var(--text-xs);
  }

  .agents li {
    display: flex;
    gap: 6px;
    align-items: center;
  }

  .agents strong {
    margin-left: auto;
    color: var(--text-primary);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }

  .dot {
    display: inline-block;
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }

  .latency table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-xs);
  }

  .latency th,
  .latency td {
    padding: 4px 0;
    text-align: right;
    white-space: nowrap;
  }

  .latency thead th {
    color: var(--text-muted);
    font-weight: 500;
  }

  .latency tbody th {
    display: flex;
    gap: 6px;
    align-items: center;
    color: var(--text-secondary);
    font-weight: 400;
    text-align: left;
  }

  .latency td {
    padding-left: 10px;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
  }

  abbr {
    text-decoration: none;
    cursor: help;
  }

  /* ------------------------------------------------------------ charts */

  .charts {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-items: start;
    gap: 16px;
  }

  @container (min-width: 880px) {
    .charts {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  /* ------------------------------------------------------------ panels */

  .panel {
    display: grid;
    gap: 14px;
    padding: 18px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface-1);
  }

  .panel-head h3 {
    margin: 0;
    font-size: var(--text-md);
    font-weight: 600;
  }

  .panel-head p {
    margin: 2px 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
  }

  .muted {
    margin: 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
  }

  .providers {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  @container (min-width: 760px) {
    .providers {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  .provider {
    display: grid;
    align-content: start;
    gap: 10px;
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface-2);
  }

  .provider header {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .provider h4 {
    margin: 0;
    font-size: var(--text-md);
    font-weight: 600;
  }

  .badge {
    padding: 2px 8px;
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--text-secondary);
    font-size: var(--text-xs);
  }

  .availability {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    margin-left: auto;
    color: var(--critical);
    font-size: var(--text-xs);
  }

  .availability.ok {
    color: var(--good);
  }

  .availability svg,
  .tone svg {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .model {
    margin: -4px 0 0;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    overflow-wrap: anywhere;
  }

  .detail {
    margin: 0;
    color: var(--text-secondary);
    font-size: var(--text-sm);
  }

  .limits {
    display: grid;
    gap: 14px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .limit-head,
  .limit-foot {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    justify-content: space-between;
    font-size: var(--text-xs);
  }

  .limit-head {
    margin-bottom: 6px;
    color: var(--text-secondary);
  }

  .pct {
    color: var(--text-primary);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .limit-foot {
    margin-top: 6px;
    color: var(--text-muted);
  }

  .tone {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    color: var(--text-secondary);
  }

  .tone.ok svg {
    color: var(--accent);
  }

  .tone.warning svg {
    color: var(--warning);
  }

  .tone.critical svg {
    color: var(--critical);
  }

  .techniques {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 16px 28px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  @container (min-width: 640px) {
    .techniques {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @container (min-width: 1000px) {
    .techniques {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }

  .techniques li {
    display: flex;
    gap: 12px;
  }

  .techniques strong {
    color: var(--text-primary);
    font-weight: 600;
  }

  .swatch {
    flex: none;
    width: 10px;
    height: 10px;
    margin-top: 5px;
    border-radius: 3px;
  }

  .swatch.neutral {
    background: transparent;
    box-shadow: inset 0 0 0 1.5px var(--text-muted);
  }

  .techniques h4 {
    margin: 0;
    font-size: var(--text-sm);
    font-weight: 600;
  }

  .techniques p {
    margin: 3px 0 0;
    color: var(--text-secondary);
    font-size: var(--text-sm);
    line-height: 1.45;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }

  @media (prefers-reduced-motion: reduce) {
    .spin,
    .loader {
      animation-duration: 2.4s;
    }
  }
</style>
