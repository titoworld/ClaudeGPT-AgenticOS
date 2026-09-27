<script lang="ts">
  import Icon, { type IconName } from './Icon.svelte';

  interface Props {
    onPick: (prompt: string) => void;
  }

  let { onPick }: Props = $props();

  const EXAMPLES = [
    "Explica'm la diferència entre TCP i UDP amb una analogia quotidiana.",
    'Revisa aquest pla: migrar una API REST a gRPC en dues setmanes amb un equip de tres persones.',
    'Quins riscos té guardar les sessions en JWT sense poder-les revocar, i com els mitigo?',
  ];

  const MODES: { icon: IconName; name: string; text: string }[] = [
    { icon: 'mode-solo', name: 'Solo', text: 'Respon una sola IA. El més ràpid i econòmic.' },
    { icon: 'mode-duel', name: 'Duel', text: 'Claude i ChatGPT responen alhora, costat a costat.' },
    {
      icon: 'mode-debate',
      name: 'Consell',
      text: 'Responen, es critiquen per rondes i sintetitzen la millor resposta. Si arriben a un consens, paren abans.',
    },
  ];
</script>

<section class="empty" aria-labelledby="empty-title">
  <div class="hero">
    <span class="orb claude" aria-hidden="true"></span>
    <span class="orb chatgpt" aria-hidden="true"></span>
    <h1 id="empty-title">Què vols preguntar al consell?</h1>
    <p>Claude i ChatGPT treballen junts per donar-te una resposta millor gastant menys tokens.</p>
  </div>

  <ul class="modes">
    {#each MODES as mode (mode.name)}
      <li>
        <Icon name={mode.icon} size={20} />
        <div>
          <strong>{mode.name}</strong>
          <span>{mode.text}</span>
        </div>
      </li>
    {/each}
  </ul>

  <div class="examples">
    <h2>Prova amb un exemple</h2>
    {#each EXAMPLES as example (example)}
      <button type="button" class="example" onclick={() => onPick(example)}>
        <Icon name="sparkles" size={16} />
        <span>{example}</span>
      </button>
    {/each}
  </div>
</section>

<style>
  .empty {
    display: grid;
    gap: 1.6rem;
    max-width: 46rem;
    margin: auto;
    padding: 2rem 0 1rem;
    animation: rise-in 500ms var(--ease-out);
  }

  .hero {
    position: relative;
    text-align: center;
    display: grid;
    gap: 0.6rem;
    justify-items: center;
  }

  .orb {
    position: absolute;
    top: -2.5rem;
    width: 9rem;
    height: 9rem;
    border-radius: 50%;
    filter: blur(40px);
    opacity: 0.35;
    pointer-events: none;
  }

  .orb.claude {
    left: 22%;
    background: var(--claude);
  }

  .orb.chatgpt {
    right: 22%;
    background: var(--chatgpt);
  }

  h1 {
    position: relative;
    font-size: clamp(1.4rem, 3vw, 2rem);
    font-weight: 700;
    letter-spacing: -0.02em;
    background: linear-gradient(90deg, #ffc2a3, #e3d4ff 50%, #a6f2dc);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }

  .hero p {
    position: relative;
    max-width: 34rem;
    color: var(--text-secondary);
  }

  .modes {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.75rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .modes li {
    display: flex;
    gap: 0.65rem;
    padding: 0.85rem 0.9rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: rgb(15 17 27 / 0.6);
    color: var(--accent);
  }

  .modes div {
    display: grid;
    align-content: start;
    gap: 0.2rem;
  }

  .modes strong {
    color: var(--text-primary);
    font-size: var(--text-sm);
  }

  .modes span {
    color: var(--text-secondary);
    font-size: var(--text-xs);
    line-height: 1.5;
  }

  .examples {
    display: grid;
    gap: 0.5rem;
  }

  h2 {
    font-size: var(--text-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  .example {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    width: 100%;
    padding: 0.75rem 0.95rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: rgb(15 17 27 / 0.55);
    color: var(--text-secondary);
    text-align: left;
    font-size: var(--text-sm);
    transition:
      border-color var(--dur-fast) var(--ease-out),
      background var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out),
      transform var(--dur-fast) var(--ease-out);
  }

  .example :global(.icon) {
    color: #d6a5ff;
  }

  .example:hover {
    border-color: rgb(139 156 255 / 0.45);
    background: rgb(139 156 255 / 0.08);
    color: var(--text-primary);
    transform: translateX(2px);
  }

  @media (max-width: 720px) {
    .modes {
      grid-template-columns: minmax(0, 1fr);
    }

    .orb {
      display: none;
    }
  }
</style>
