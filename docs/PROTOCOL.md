# Protocol client ↔ servidor

Contracte entre el frontend (`web/`) i el backend (`src/agentic_os/server/`). Tot és JSON en UTF-8. Els noms de camps són en anglès i en `snake_case`. Els tipus TypeScript equivalents són a `web/src/lib/protocol.ts`.

## Autenticació i seguretat comuna

- Sessió amb una cookie `__Host-aos_session` (HttpOnly, Secure, SameSite=Strict, Path=/). En desenvolupament sense HTTPS (`AOS_SECURE_COOKIES=false`) la cookie es diu `aos_session` i no és `Secure`. La sessió caduca després de `AOS_SESSION_IDLE_HOURS` sense activitat (72 h) i als `AOS_SESSION_MAX_DAYS` (30 dies).
- Dispositiu conegut: cada inici de sessió correcte posa també una cookie `__Host-aos_device` (HttpOnly, Secure, SameSite=Strict, Path=/; sense HTTPS es diu `aos_device` i no és `Secure`) amb un testimoni aleatori que dura 1 any i se substitueix per un de nou a cada inici de sessió. El logout la conserva; `agentic-os init` i `agentic-os reset-sessions` obliden tots els dispositius. Un intent d'inici de sessió que la porta només es limita pel comptador d'errors d'aquell dispositiu (no pel de l'adreça ni pel global), de manera que ningú no pot bloquejar el propietari des d'un navegador on ja ha entrat.
- Totes les rutes sota `/api/` requereixen sessió, excepte `GET /api/health`, `GET /api/auth/state` i `POST /api/auth/login`.
- Les peticions que canvien estat (`POST`, `PUT`, `PATCH`, `DELETE`) i l'*handshake* del WebSocket han de portar una capçalera `Origin` present a `Settings.allowed_origins`; si no, `403`.
- Errors HTTP: cos `{"detail": "missatge en català"}`. `400` si el client talla la connexió abans d'enviar tot el cos, `401` sense sessió, `403` origen no permès, `404`, `408` si el cos no arriba sencer en 15 s (des que el servidor el comença a llegir), `413` cos massa gran (1 MiB, pel `Content-Length` o comptat mentre arriba), `422` validació (també els nombres fora de rang, per grans que siguin), `429` massa intents (amb capçalera `Retry-After` i camp `retry_after` en segons).
- Una resposta que surt abans que el servidor hagi rebut tot el cos de la petició (`403`, `401`, `413` pel `Content-Length`, `429`, `408`, o un cos enviat a una ruta que no el llegeix) porta `Connection: close` i el servidor tanca la connexió: el client no la pot reutilitzar. Les peticions sense cos o amb el cos llegit sencer mantenen la connexió.

## REST

| Mètode i ruta | Cos / paràmetres | Resposta |
| --- | --- | --- |
| `GET /api/health` | – | `{"status": "ok"}` |
| `GET /api/auth/state` | – | `{"authenticated": bool, "setup_required": bool}` (`setup_required`: encara no s'ha executat `agentic-os init`) |
| `POST /api/auth/login` | `{"password": str, "totp": str}` | `204` + cookies de sessió i de dispositiu; `401`; `429` |
| `POST /api/auth/logout` | – | `204` (esborra la cookie de sessió i tanca els WebSockets d'aquesta sessió; la de dispositiu es conserva) |
| `GET /api/providers` | – | `[ProviderStatus]` |
| `GET /api/models` | `?refresh=1` opcional (ignora la memòria cau) | `ModelCatalog` |
| `GET /api/pricing` | – | `Pricing` |
| `GET /api/spend` | – | `MonthSpend` (mes en curs, per a les barres de pressupost) |
| `GET /api/settings` | – | `RuntimeSettings` |
| `PUT /api/settings` | `RuntimeSettings` (les claus que falten prenen el valor per defecte) | `RuntimeSettings` |
| `GET /api/conversations` | `?limit=50&before=<id>` | `[ConversationSummary]`, les més recents primer |
| `GET /api/conversations/{id}` | – | `ConversationDetail` |
| `PATCH /api/conversations/{id}` | `{"title": str}` | `ConversationSummary` |
| `DELETE /api/conversations/{id}` | – | `204` |
| `GET /api/stats` | `?days=30` (1–365) | `Stats` |
| `GET /api/ws` | WebSocket | vegeu més avall |

### Tipus

```ts
type Agent = "claude" | "chatgpt";
type TurnMode = "solo" | "duel" | "debate";
type MessageKind = "question" | "answer" | "revision" | "synthesis";

interface Usage {
  input_tokens: number;        // entrada no servida des de memòria cau
  output_tokens: number;       // inclou el raonament
  cache_read_tokens: number; cache_write_tokens: number;
  reasoning_tokens: number;
  cost_usd: number | null;     // cost estimat (preus d'API); null si el model no té preu
}

interface ProviderStatus {
  agent: Agent; mode: "cli" | "api" | "fake";
  available: boolean; model: string; detail: string;   // detail en català
  limits: { window: string;            // "5h", "7d"...
            used_percent: number | null;   // 0–100 (pot passar de 100)
            resets_at: string | null;  // ISO 8601
            status: string }[];        // "allowed" | "warning" | "rejected"
}

interface ModelInfo {
  id: string;                  // valor que s'envia al proveïdor (id d'API, àlies de la CLI...)
  label: string; description: string;
  is_default: boolean; context_window: number | null;
}

interface ModelCatalog {
  claude: AgentModels; chatgpt: AgentModels;
}
interface AgentModels {
  mode: "cli" | "api" | "fake";
  default_model: string;       // el que es fa servir si no se'n tria cap
  fast_model: string;          // el de les crides internes (resums)
  models: ModelInfo[];         // llista en directe del proveïdor, o una de reserva
  live: boolean;               // false si la llista és la de reserva
}
// Qualsevol id que compleixi ^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}$ és vàlid:
// així es poden fer servir models nous encara que no surtin a la llista.

interface FxRate {
  eur_per_usd: number;
  as_of: string | null;        // data (AAAA-MM-DD) del tipus del BCE
  source: "ecb" | "manual";
}

interface ModelPrice {         // USD per milió de tokens, com els publiquen els proveïdors
  input: number; output: number; cache_read: number; cache_write: number;
}

interface Pricing {
  fx: FxRate;
  prices: (ModelPrice & { model: string; source: "default" | "custom" })[];
}
// prices: exactament la taula amb què es calculen els costos, ordenada per model: els
// preus per defecte amb els del propietari al damunt (un preu propi substitueix el per
// defecte del mateix model normalitzat, sense prefix de proveïdor, data ni context).

interface RuntimeSettings {
  default_mode: TurnMode;                 // per defecte "debate"
  default_target: Agent;                  // agent del mode solo
  debate: { rounds: number;               // 0–4, per defecte 2
            consensus_threshold: number;  // 50–100, per defecte 85
            synthesizer: Agent };         // per defecte "claude"
  use_cache: boolean;                     // per defecte true
  compaction_threshold_tokens: number;    // 1000–100000, per defecte 6000
  models: Record<Agent, string | null>;       // model per defecte; null = el del proveïdor
  fast_models: Record<Agent, string | null>;  // model per als resums; null = el del proveïdor
  prices: Record<string, ModelPrice>;         // preus propis (substitueixen o afegeixen models);
                                              // 422 si una clau no identifica cap model un cop
                                              // normalitzada ("openai/") o si dues són el mateix
  fx: { mode: "auto" | "manual";              // auto: BCE diari, amb el manual de reserva
        eur_per_usd: number };                // 0,2–5, per defecte 0,86
  budgets_eur: Record<Agent, number | null>;  // pressupost mensual de l'ús per API
  plans_eur: Record<Agent, number | null>;    // preu mensual de la subscripció
}

interface AgentSpend {
  api_usd: number;             // cost real de les crides en mode api
  equivalent_usd: number;      // valor de les crides en mode cli a preus d'API
  unpriced_calls: number;      // crides de models sense preu conegut
  budget_eur: number | null;  budget_used: number | null;   // 0–1+ (api_usd en € / pressupost); null sense pressupost
  plan_eur: number | null;    plan_value: number | null;    // 0–1+ (equivalent en € / preu del pla); null sense preu
}
// equivalent_usd inclou tota crida que no és d'API i té preu (també les de demostració si els poses preu).

interface MonthSpend {
  month: string;               // "AAAA-MM" (UTC)
  fx: FxRate;
  by_agent: Record<Agent, AgentSpend>;
}

interface ConversationSummary {
  id: number; title: string;
  created_at: string; updated_at: string;   // ISO 8601 UTC
  last_mode: TurnMode | null; message_count: number;
}

interface Message {
  id: number; turn_id: number; kind: MessageKind; content: string;
  agent: Agent | null; round: number; final: boolean;
  meta: Record<string, unknown>;   // vegeu "Metadades de missatge"
  created_at: string;
}

interface ConversationDetail extends ConversationSummary {
  summary: string | null;          // resum de la compactació, si n'hi ha
  messages: Message[];             // tots, els més antics primer
}

interface Stats {
  days: number;
  totals: { calls: number; errors: number; cost_usd: number;
            by_agent: Record<Agent, Usage & { calls: number }> };
  savings: { cache: number; compaction: number; early_stop: number;
             unchanged: number; total: number; cost_usd: number | null };
             // cost_usd: valor dels estalvis de la finestra; com els tokens, es conserva
             // encara que s'esborri la conversa (null si cap estalvi no té preu)
  daily: { date: string; agent: Agent; input_tokens: number; output_tokens: number;
           cache_read_tokens: number; cost_usd: number }[];   // date: dia natural UTC
  savings_daily: { date: string; kind: "cache" | "compaction" | "early_stop" | "unchanged";
                   tokens: number }[];
  latency: Record<Agent, { p50_ms: number | null; p95_ms: number | null;
                           ttft_p50_ms: number | null }>;
  turns: { solo: number; duel: number; debate: number };
  consensus: { debates: number; reached: number; avg_rounds: number | null };
  costs: { fx: FxRate;
           by_agent: Record<Agent, { api_usd: number; equivalent_usd: number;
                                     unpriced_calls: number }> };
  month: MonthSpend;
}
```

### Metadades de missatge (`meta`)

- Pregunta (`question`): `mode`, `target`, `options`, `models` (models triats per a aquest torn, si n'hi ha) i `compaction_usage` (`Usage` de totes les crides de resum del torn, si n'hi ha hagut).
- Respostes (`answer`, `revision`, `synthesis`): `model`, `usage` (amb `cost_usd`), `cost_basis` (`"api"`: cost real; `"equivalent"`: mode subscripció, valor a preus d'API), `latency_ms`, `ttft_ms`, `cached` (si ve de la memòria cau).
- Revisió (`revision`): a més, `critique` (text), `agreement` (0–100 o `null`), `unchanged` (bool). Si `unchanged` és cert, `content` conté la resposta anterior que es conserva; també la conté (amb `unchanged: false`) quan la revisió es va tallar abans de la resposta.
- Síntesi (`synthesis`): a més, `consensus` (`{reached, round, scores}`) i `degraded: true` si s'ha desat sense cridar cap model.
- Missatges finals del torn (els de `final_message_ids`): `savings` (el mateix objecte que `turn.completed`). L'últim missatge final porta també `unstored_usage` (`Usage`) si hi ha hagut crides facturades que no han deixat cap missatge (errors, respostes buides, refusades).
- Total d'un torn recarregat: la suma dels `usage` dels seus missatges, més `compaction_usage` i `unstored_usage`. És igual al `usage` de `turn.completed`, llevat que en un duel una crida fallida acabi després que l'altre agent hagi desat la seva resposta.

## WebSocket `/api/ws`

Una sola connexió persistent per pestanya. El servidor tanca amb el codi:

- `4401` si no hi ha sessió en connectar i també quan la sessió s'acaba amb el socket obert: al moment si és un logout d'aquest servidor, i com a molt en 30 s si l'ha revocada `agentic-os reset-sessions` o ha caducat (el servidor la torna a comprovar cada 30 s encara que el client no enviï res).
- `4403` si l'origen no és vàlid.
- `1013` si el client no rep prou ràpid: té 4096 missatges pendents d'enviar, o 20.000 esdeveniments o més (cada esdeveniment d'un reenviament de `turn.subscribe` compta; un sol reenviament pot ser més llarg, així que qualsevol torn es pot recuperar), quan n'arriba un altre. Ha de reconnectar i fer `turn.subscribe` des de l'últim `seq` que té.
- `1011` si hi ha un error intern.

Cada missatge del client (amb `type`) comprova la sessió. Un `ping` la comprova però no compta com a activitat: no allarga la caducitat per inactivitat, de manera que una pestanya oberta sense ús no manté la sessió viva.

### Client → servidor

```jsonc
{"type": "turn.start", "request_id": "uuid", "text": "…", "mode": "debate",
 "target": "claude", "conversation_id": null,
 "options": {"debate": {"rounds": 2, "consensus_threshold": 85, "synthesizer": "claude"},
             "use_cache": true},
 "models": {"claude": "opus", "chatgpt": "gpt-6-sol"}}
{"type": "turn.cancel", "request_id": "uuid"}
{"type": "turn.subscribe", "request_id": "uuid", "after_seq": 12}   // després d'una reconnexió
{"type": "ping", "t": 1727450000000}
```

`mode`, `target`, `options` (també parcials) i `models` són opcionals: s'apliquen els `RuntimeSettings`. A `models` (i als `RuntimeSettings`), `null` o `""` vol dir el model per defecte; els identificadors es netegen d'espais. Límit: 3 torns simultanis i un de sol per conversa.

### Servidor → client

En connectar: `{"type": "hello", "version": "0.2.0", "providers": [ProviderStatus], "fx": FxRate, "active_turns": [{"request_id", "conversation_id" (null fins al turn.started d'una conversa nova), "last_seq"}]}`. `active_turns` només inclou els torns en curs.

Cada esdeveniment d'un torn porta `request_id` i `seq` (enter creixent dins del torn, començant per 1). El servidor guarda els esdeveniments dels torns en curs i dels acabats fa menys de 5 minuts: `turn.subscribe` reenvia els que tenen `seq > after_seq` i després continua en directe; si el torn no existeix respon `{"type": "turn.unknown", "request_id"}`. Una connexió rep cada torn una sola vegada: un `turn.subscribe` d'un torn que la connexió ja rep (perquè l'ha començat o ja s'hi ha subscrit) s'ignora, sense resposta, ja que ja té tots els esdeveniments des del primer `after_seq`. **Un torn continua encara que es talli la connexió**; només `turn.cancel` l'atura.

| `type` | Camps | Significat |
| --- | --- | --- |
| `turn.started` | `conversation_id`, `turn_id`, `mode`, `new_conversation` | Pregunta desada |
| `phase` | `phase` (`answer`, `revision`, `synthesis`, `compaction`), `round` | Canvi de fase (`compaction` pot arribar abans de `turn.started`) |
| `stream.started` | `stream_id`, `agent`, `kind`, `round`, `model` | Un model comença a respondre |
| `stream.delta` | `stream_id`, `section` (`text`, `critique`, `answer`), `text` | Fragment de text |
| `stream.completed` | `stream_id`, `message_id`, `usage`, `latency_ms`, `ttft_ms`, `agreement`, `unchanged`, `cost_basis` | Resposta acabada i desada |
| `stream.failed` | `stream_id`, `error: {kind, message}` | Aquell model ha fallat (el torn pot continuar amb l'altre) |
| `turn.completed` | `conversation_id`, `turn_id`, `final_message_ids`, `usage`, `savings`, `consensus`, `cached` | Torn acabat |
| `turn.failed` | `error: {kind, message}` | Torn avortat |
| `turn.cancelled` | – | Cancel·lat per l'usuari |

Altres: `{"type": "pong", "t"}` (retorna el mateix `t`) i `{"type": "error", "code", "message", "request_id"?}` per a missatges invàlids o límits (`code`: `invalid`, `busy`, `duplicate`, `unavailable`, `too_large`, `internal`; `request_id` quan es rebutja un `turn.start`).

`savings` = `{"cache", "compaction", "early_stop", "unchanged", "total", "cost_usd"}` (tokens estimats estalviats i el seu valor aproximat: les respostes conservades al preu de sortida del seu model, la compactació al preu d'entrada de les crides que portaven el context, les rondes omeses al cost mitjà de les revisions del torn i un encert de memòria cau al cost del torn original; `null` si no se'n pot posar preu a cap). `consensus` = `{"reached": bool, "round": int, "scores": {"claude": int, "chatgpt": int}}` o `null` fora del mode debat.

### Ordre típic d'un debat

1. `turn.started` → `phase(answer, 0)` → dos `stream.started` (Claude i ChatGPT en paral·lel) amb els seus `stream.delta` (`section: "text"`) i `stream.completed`.
2. Per a cada ronda `r`: `phase(revision, r)` → dos fluxos amb `section` `critique` i després `answer`; `stream.completed` porta `agreement`.
3. Si tots dos arriben al llindar de consens, s'aturen les rondes (estalvi `early_stop`).
4. `phase(synthesis, r)` → un flux de l'agent sintetitzador → `turn.completed`.
