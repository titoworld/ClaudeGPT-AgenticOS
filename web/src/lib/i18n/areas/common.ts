// Texts shared by the whole interface: the modes, how each agent is connected, the
// language picker. English is the source: Spanish and Catalan have the same keys and
// parameters (TypeScript checks it).

export const en = {
  modes: {
    solo: { label: 'Solo', description: 'One AI answers. The fastest and cheapest.' },
    duel: { label: 'Duel', description: 'Claude and ChatGPT answer at once, side by side.' },
    debate: {
      label: 'Council',
      description: 'They answer, critique each other round by round and synthesize the best answer. If they reach a consensus, they stop early.',
    },
    refine: { label: 'Refine', description: 'Both AIs improve a single document round after round until you stop them.' },
  },
  providerModes: { cli: 'Subscription', api: 'API', fake: 'Demo' },
  language: 'Language',
};

export const es: typeof en = {
  modes: {
    solo: { label: 'Solo', description: 'Responde una sola IA. Lo más rápido y económico.' },
    duel: { label: 'Duelo', description: 'Claude y ChatGPT responden a la vez, uno al lado del otro.' },
    debate: {
      label: 'Consejo',
      description: 'Responden, se critican por rondas y sintetizan la mejor respuesta. Si llegan a un consenso, paran antes.',
    },
    refine: { label: 'Perfecciona', description: 'Las dos IA mejoran un solo documento ronda tras ronda hasta que las detengas.' },
  },
  providerModes: { cli: 'Suscripción', api: 'API', fake: 'Demo' },
  language: 'Idioma',
};

export const ca: typeof en = {
  modes: {
    solo: { label: 'Solo', description: 'Respon una sola IA. El més ràpid i econòmic.' },
    duel: { label: 'Duel', description: 'Claude i ChatGPT responen alhora, costat a costat.' },
    debate: {
      label: 'Consell',
      description: 'Responen, es critiquen per rondes i sintetitzen la millor resposta. Si arriben a un consens, paren abans.',
    },
    refine: { label: 'Perfecciona', description: "Les dues IA milloren un sol document ronda rere ronda fins que l'aturis." },
  },
  providerModes: { cli: 'Subscripció', api: 'API', fake: 'Demo' },
  language: 'Idioma',
};
