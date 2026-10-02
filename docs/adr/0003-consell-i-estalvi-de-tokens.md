# 0003. Mode Consell i estratègia d'estalvi de tokens

- Estat: Acceptat
- Data: 2026-09-27

## Context

L'objectiu és obtenir millors respostes fent que Claude i ChatGPT es revisin mútuament, sense multiplicar el consum. Els sistemes multiagent típics reenvien tota la transcripció a cada agent i fan rondes fixes, cosa que dispara els tokens.

## Decisió

- Tres modes de torn: **Solo**, **Duel** i **Consell** (respostes en paral·lel → fins a N rondes de revisió → síntesi).
- Les revisions són autocontingudes: pregunta + resposta pròpia + resposta de l'altre. No reben l'historial de la conversa.
- Format de revisió estricte (`<critique>`, `<answer>`, `<agreement>`), amb `UNCHANGED` quan no cal reescriure i parada anticipada quan tots dos superen el llindar d'acord.
- L'historial de la conversa només conté la pregunta i la resposta final de cada torn; quan supera un llindar es compacta amb el model ràpid.
- Memòria cau de torns sencers per a preguntes idèntiques en el mateix context.
- Prompts de sistema estables (sense dates ni identificadors) perquè la memòria cau dels proveïdors funcioni.
- Cada estalvi es mesura i es mostra al tauler: memòria cau, compactació, parada per consens i respostes sense canvis.

## Alternatives considerades

- **Rondes fixes amb tota la transcripció:** més simple, però molt més car.
- **Votació amb més de dos agents:** fora d'abast; dos models amb síntesi ja capturen la major part del benefici.
- **Memòria vectorial (RAG):** innecessària per a un sol usuari i converses d'aquesta mida; es pot afegir més endavant.

## Conseqüències

- Els estalvis són estimacions (tokens ≈ caràcters / 4) excepte els que reporten els proveïdors (`cache_read_tokens`).
- La qualitat depèn que els models respectin el format de revisió; l'analitzador és tolerant i, si falten etiquetes, tracta el text com a resposta.
