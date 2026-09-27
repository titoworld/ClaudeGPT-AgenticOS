<script lang="ts">
  import { onMount, tick } from 'svelte';
  import BrandMark from '../components/BrandMark.svelte';
  import CopyButton from '../components/CopyButton.svelte';
  import Icon from '../components/Icon.svelte';
  import { ApiError, app } from '../lib/app.svelte';

  const SETUP_COMMAND = 'docker compose exec -it app agentic-os init';
  const uid = $props.id();

  let password = $state('');
  let code = $state('');
  let error: string | null = $state(null);
  let busy = $state(false);
  let lockedUntil: number | null = $state(null);
  let now = $state(Date.now());
  let passwordInput: HTMLInputElement | undefined = $state();
  let codeInput: HTMLInputElement | undefined = $state();

  const remaining = $derived(lockedUntil ? Math.max(0, Math.ceil((lockedUntil - now) / 1000)) : 0);
  const locked = $derived(remaining > 0);
  const countdown = $derived(
    remaining >= 60 ? `${Math.floor(remaining / 60)} min ${String(remaining % 60).padStart(2, '0')} s` : `${remaining} s`,
  );

  onMount(() => {
    if (app.auth === 'login') passwordInput?.focus();
  });

  $effect(() => {
    if (!lockedUntil) return;
    const id = setInterval(() => {
      now = Date.now();
      if (lockedUntil && now >= lockedUntil) {
        lockedUntil = null;
        error = null;
      }
    }, 250);
    return () => clearInterval(id);
  });

  function setCode(raw: string): void {
    code = raw.replace(/\D/g, '').slice(0, 6);
    if (codeInput && codeInput.value !== code) codeInput.value = code;
    if (code.length === 6 && password && !locked) void submit();
  }

  function onPaste(e: ClipboardEvent): void {
    const text = e.clipboardData?.getData('text') ?? '';
    if (!text) return;
    e.preventDefault();
    setCode(text);
  }

  async function submit(): Promise<void> {
    if (busy || locked) return;
    if (!password) {
      error = 'Escriu la contrasenya.';
      passwordInput?.focus();
      return;
    }
    if (code.length !== 6) {
      error = "El codi de l'aplicació d'autenticació té 6 xifres.";
      codeInput?.focus();
      return;
    }
    busy = true;
    error = null;
    try {
      await app.login(password, code);
    } catch (err) {
      code = '';
      if (err instanceof ApiError && err.status === 429) {
        lockedUntil = Date.now() + Math.max(1, err.retryAfter ?? 60) * 1000;
        now = Date.now();
        error = null;
      } else if (err instanceof ApiError && err.status === 401) {
        error = 'La contrasenya o el codi no són correctes.';
      } else if (err instanceof ApiError && err.status === 403) {
        error = "Aquest origen no té permís per iniciar sessió. Revisa l'adreça que fas servir.";
      } else if (err instanceof ApiError) {
        error = err.message || "No s'ha pogut iniciar la sessió.";
      } else {
        error = 'No es pot connectar amb el servidor. Comprova la connexió.';
      }
      busy = false;
      await tick(); // the input is enabled again only after this render
      codeInput?.focus();
    } finally {
      busy = false;
    }
  }
</script>

<main class="login">
  <div class="card glass">
    <div class="head">
      <div class="mark"><BrandMark size={56} /></div>
      <h1>ClaudeGPT <span>OS</span></h1>
      <p class="tagline">El teu consell privat de Claude i ChatGPT</p>
    </div>

    {#if app.auth === 'setup'}
      <div class="setup">
        <h2><Icon name="terminal" size={18} />Cal configurar l'accés</h2>
        <p>
          Encara no hi ha cap contrasenya ni codi TOTP. Executa aquesta ordre al servidor i segueix els passos:
        </p>
        <div class="command">
          <code>{SETUP_COMMAND}</code>
          <CopyButton text={SETUP_COMMAND} label="Copia l'ordre" />
        </div>
        <button type="button" class="btn primary wide" onclick={() => void app.checkAuth()}>
          <Icon name="refresh" size={16} />Ja està, torna-ho a comprovar
        </button>
      </div>
    {:else if app.auth === 'unreachable'}
      <div class="setup">
        <h2><Icon name="alert" size={18} />No es pot connectar amb el servidor</h2>
        <p>Comprova que el servei està en marxa i que tens connexió a internet.</p>
        <button type="button" class="btn primary wide" onclick={() => void app.checkAuth()}>
          <Icon name="refresh" size={16} />Torna-ho a provar
        </button>
      </div>
    {:else}
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        novalidate>
        <label class="field">
          <span>Contrasenya</span>
          <input
            bind:this={passwordInput}
            bind:value={password}
            class="input"
            type="password"
            name="password"
            autocomplete="current-password"
            required
            disabled={busy} />
        </label>

        <label class="field">
          <span>Codi de verificació</span>
          <input
            bind:this={codeInput}
            value={code}
            oninput={(e) => setCode(e.currentTarget.value)}
            onpaste={onPaste}
            class="input code"
            type="text"
            name="totp"
            inputmode="numeric"
            autocomplete="one-time-code"
            pattern="[0-9]*"
            placeholder="000000"
            aria-describedby="{uid}-code-hint"
            required
            disabled={busy || locked} />
          <span class="digits" aria-hidden="true">
            {#each Array.from({ length: 6 }, (_, i) => i) as i (i)}<i class:on={i < code.length}></i>{/each}
          </span>
          <small class="hint" id="{uid}-code-hint">Les 6 xifres de la teva aplicació d'autenticació. S'envia sol.</small>
        </label>

        <div class="messages" aria-live="assertive">
          {#if locked}
            <p class="error" role="alert">
              <Icon name="clock" size={16} />Massa intents. Torna-ho a provar d'aquí a {countdown}.
            </p>
          {:else if error}
            <p class="error" role="alert"><Icon name="alert" size={16} />{error}</p>
          {/if}
        </div>

        <button type="submit" class="btn primary wide" disabled={busy || locked}>
          {#if busy}<span class="spinner" aria-hidden="true"></span>Entrant…{:else}<Icon name="lock" size={16} />Entra{/if}
        </button>
      </form>
    {/if}

    <p class="foot">Accés privat: només el propietari pot entrar.</p>
  </div>
</main>

<style>
  .login {
    position: relative;
    z-index: 1;
    display: grid;
    place-items: center;
    min-height: 100%;
    padding: 1.5rem 1rem;
    overflow-y: auto;
  }

  .card {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 1.4rem;
    width: min(25rem, 100%);
    padding: 2rem 1.8rem 1.4rem;
    border-radius: 22px;
    border-color: rgb(255 255 255 / 0.12);
    animation: rise-in 500ms var(--ease-out);
  }

  .head {
    display: grid;
    justify-items: center;
    gap: 0.4rem;
    text-align: center;
  }

  .mark {
    margin-bottom: 0.4rem;
    animation: float 6s ease-in-out infinite;
  }

  h1 {
    font-size: 1.6rem;
    font-weight: 750;
    letter-spacing: -0.02em;
  }

  h1 span {
    background: linear-gradient(90deg, var(--claude-glow), #c9a2ff, var(--chatgpt-glow));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }

  .tagline {
    color: var(--text-secondary);
    font-size: var(--text-sm);
  }

  form {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 1rem;
  }

  .code {
    font-family: var(--font-mono);
    font-size: 1.4rem;
    letter-spacing: 0.55em;
    text-align: center;
    padding-left: 1.1rem;
  }

  .code::placeholder {
    color: rgb(255 255 255 / 0.14);
  }

  .digits {
    display: flex;
    justify-content: center;
    gap: 0.45rem;
    margin-top: 0.1rem;
  }

  .digits i {
    width: 1.6rem;
    height: 3px;
    border-radius: 2px;
    background: rgb(255 255 255 / 0.1);
    transition: background var(--dur-fast) var(--ease-out);
  }

  .digits i.on {
    background: linear-gradient(90deg, var(--claude), var(--chatgpt));
  }

  .messages:empty {
    display: none;
  }

  .error {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.6rem 0.75rem;
    border-radius: var(--radius-sm);
    border: 1px solid rgb(255 93 108 / 0.4);
    background: rgb(255 93 108 / 0.1);
    color: #ffc9ce;
    font-size: var(--text-sm);
  }

  .error :global(.icon) {
    margin-top: 0.15rem;
  }

  .wide {
    width: 100%;
    min-height: 2.75rem;
    font-size: var(--text-md);
  }

  .setup {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.9rem;
  }

  .setup h2 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: var(--text-lg);
    font-weight: 650;
  }

  .setup p {
    color: var(--text-secondary);
    font-size: var(--text-sm);
  }

  .command {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.55rem 0.5rem 0.55rem 0.8rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-strong);
    background: rgb(5 6 10 / 0.8);
  }

  .command code {
    flex: 1;
    min-width: 0;
    overflow-x: auto;
    font-family: var(--font-mono);
    font-size: 0.8rem;
    color: #a5e075;
    overflow-wrap: anywhere;
  }

  .foot {
    text-align: center;
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .spinner {
    width: 15px;
    height: 15px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.3);
    border-top-color: #fff;
    animation: spin 0.8s linear infinite;
  }

  @keyframes float {
    50% {
      transform: translateY(-5px);
    }
  }
</style>
