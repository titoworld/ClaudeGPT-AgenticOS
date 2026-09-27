# Protocol client ↔ servidor

Contracte entre el frontend (`web/`) i el backend (`src/agentic_os/web/`). Tot és JSON en UTF-8. Els noms de camps són en anglès i en `snake_case`.

## Autenticació i seguretat comuna

- Sessió amb una cookie `__Host-aos_session` (HttpOnly, Secure, SameSite=Strict, Path=/). En desenvolupament sense HTTPS (`AOS_SECURE_COOKIES=false`) la cookie es diu `aos_session` i no és `Secure`.
- Totes les rutes sota `/api/` requereixen sessió, excepte `GET /api/health`, `GET /api/auth/state` i `POST /api/auth/login`.
- Les peticions que canvien estat (`POST`, `PUT`, `PATCH`, `DELETE`) i l'*handshake* del WebSocket han de portar una capçalera `Origin` present a `Settings.allowed_origins`; si no, `403`.
- Errors HTTP: cos `{"detail": "missatge"}`. `401` sense sessió, `403` origen no permès, `404`, `422` validació, `429` massa intents (amb capçalera `Retry-After` i camp `retry_after` en segons).

## REST

| Mètode i ruta | Cos / paràmetres | Resposta |
| --- | --- | --- |
| `GET /api/health` | – | `{"status": "ok"}` |
| `GET /api/auth/state` | – | `{"authenticated": bool, "setup_required": bool}` (`setup_required`: encara no s'ha executat `agentic-os init`) |
| `POST /api/auth/login` | `{"password": str, "totp": str}` | `204` + cookie; `401`; `429` |
| `POST /api/auth/logout` | – | `204` |
| `GET /api/providers` | – | `[ProviderStatus]` |
| `GET /api/settings` | – | `RuntimeSettings` |
| `PUT /api/settings` | `RuntimeSettings` | `RuntimeSettings` |
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
  input_tokens: number; output_tokens: number;
  cache_read_tokens: number; cache_write_tokens: number;
  reasoning_tokens: number; cost_usd: number | null;
}

interface ProviderStatus {
  agent: Agent; mode: "cli" | "api" | "fake";
  available: boolean; model: string; detail: string;
}

interface RuntimeSettings {
  default_mode: TurnMode;                 // per defecte "debate"
  default_target: Agent;                  // agent del mode solo
  debate: { rounds: number;               // 0–4, per defecte 2
            consensus_threshold: number;  // 50–100, per defecte 85
            synthesizer: Agent };         // per defecte "claude"
  use_cache: boolean;                     // per defecte true
  compaction_threshold_tokens: number;    // 1000–100000, per defecte 6000
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
             unchanged: number; total: number };
  daily: { date: string; agent: Agent; input_tokens: number; output_tokens: number;
           cache_read_tokens: number }[];
  savings_daily: { date: string; kind: "cache" | "compaction" | "early_stop" | "unchanged";
                   tokens: number }[];
  latency: Record<Agent, { p50_ms: number | null; p95_ms: number | null;
                           ttft_p50_ms: number | null }>;
  turns: { solo: number; duel: number; debate: number };
  consensus: { debates: number; reached: number; avg_rounds: number | null };
}
```

### Metadades de missatge (`meta`)

- Pregunta (`question`): `mode`, `target`, `options`.
- Respostes (`answer`, `revision`, `synthesis`): `model`, `usage`, `latency_ms`, `ttft_ms`, `cached` (si ve de la memòria cau).
- Revisió (`revision`): a més, `critique` (text), `agreement` (0–100 o `null`), `unchanged` (bool).

## WebSocket `/api/ws`

Una sola connexió persistent per pestanya. El servidor tanca amb el codi `4401` si no hi ha sessió i `4403` si l'origen no és vàlid.

### Client → servidor

```jsonc
{"type": "turn.start", "request_id": "uuid", "text": "…", "mode": "debate",
 "target": "claude", "conversation_id": null,
 "options": {"debate": {"rounds": 2, "consensus_threshold": 85, "synthesizer": "claude"},
             "use_cache": true}}
{"type": "turn.cancel", "request_id": "uuid"}
{"type": "turn.subscribe", "request_id": "uuid", "after_seq": 12}   // després d'una reconnexió
{"type": "ping", "t": 1727450000000}
```

`options` i `target` són opcionals (s'apliquen els `RuntimeSettings`). Límit: 3 torns simultanis i un de sol per conversa.

### Servidor → client

En connectar: `{"type": "hello", "version": "0.2.0", "providers": [ProviderStatus], "active_turns": [{"request_id", "conversation_id", "last_seq"}]}`.

Cada esdeveniment d'un torn porta `request_id` i `seq` (enter creixent dins del torn, començant per 1). El servidor guarda els esdeveniments dels torns en curs i dels acabats fa menys de 5 minuts: `turn.subscribe` reenvia els que tenen `seq > after_seq` i després continua en directe; si el torn no existeix respon `{"type": "turn.unknown", "request_id"}`. **Un torn continua encara que es talli la connexió**; només `turn.cancel` l'atura.

| `type` | Camps | Significat |
| --- | --- | --- |
| `turn.started` | `conversation_id`, `turn_id`, `mode`, `new_conversation` | Pregunta desada |
| `phase` | `phase` (`answer`, `revision`, `synthesis`, `compaction`), `round` | Canvi de fase |
| `stream.started` | `stream_id`, `agent`, `kind`, `round`, `model` | Un model comença a respondre |
| `stream.delta` | `stream_id`, `section` (`text`, `critique`, `answer`), `text` | Fragment de text |
| `stream.completed` | `stream_id`, `message_id`, `usage`, `latency_ms`, `ttft_ms`, `agreement`, `unchanged` | Resposta acabada i desada |
| `stream.failed` | `stream_id`, `error: {kind, message}` | Aquell model ha fallat (el torn pot continuar amb l'altre) |
| `turn.completed` | `conversation_id`, `turn_id`, `final_message_ids`, `usage`, `savings`, `consensus`, `cached` | Torn acabat |
| `turn.failed` | `error: {kind, message}` | Torn avortat |
| `turn.cancelled` | – | Cancel·lat per l'usuari |

Altres: `{"type": "pong", "t"}` i `{"type": "error", "message", "request_id"?}` per a missatges invàlids o límits.

`savings` = `{"cache", "compaction", "early_stop", "unchanged", "total"}` (tokens estimats estalviats). `consensus` = `{"reached": bool, "round": int, "scores": {"claude": int, "chatgpt": int}}` o `null` fora del mode debat.

### Ordre típic d'un debat

1. `turn.started` → `phase(answer, 0)` → dos `stream.started` (Claude i ChatGPT en paral·lel) amb els seus `stream.delta` (`section: "text"`) i `stream.completed`.
2. Per a cada ronda `r`: `phase(revision, r)` → dos fluxos amb `section` `critique` i després `answer`; `stream.completed` porta `agreement`.
3. Si tots dos arriben al llindar de consens, s'aturen les rondes (estalvi `early_stop`).
4. `phase(synthesis, r)` → un flux de l'agent sintetitzador → `turn.completed`.
