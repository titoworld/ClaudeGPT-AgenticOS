# Arquitectura

> Consell d'IA personal: Claude i ChatGPT responen, es critiquen i sintetitzen una resposta millor, gastant els mínims tokens. Un sol usuari, autoallotjat en un VPS.

## Visió general

```
Navegador (Svelte 5 + three.js)
   │  HTTPS / HTTP/3 · una connexió WebSocket persistent
   ▼
Caddy (TLS automàtic, capçaleres de seguretat, compressió)
   │  xarxa interna de Docker
   ▼
Aplicació Python (FastAPI + uvicorn/uvloop)
   ├── web/          API REST, WebSocket, sessió, fitxers estàtics del frontend
   ├── security/     contrasenya argon2id, TOTP, sessions, límits d'intents, capçaleres
   ├── orchestrator/ motor de torns: solo · duel · debat, compactació, memòria cau, comptabilitat
   ├── providers/    Claude i ChatGPT, cadascun en mode cli · api · fake
   └── storage/      SQLite (WAL): converses, missatges, ús, estalvis, memòria cau, sessions
        │
        ├── CLI oficial de Claude Code  (subscripció Pro/Max, OAuth)   ─┐ mode "cli"
        ├── CLI oficial de Codex        (subscripció ChatGPT, OAuth)   ─┘
        └── SDK d'Anthropic / OpenAI    (claus d'API)                     mode "api"
```

## Mòduls i contractes

| Mòdul | Contracte | Responsabilitat |
| --- | --- | --- |
| `domain.py` | tipus compartits | `AgentName`, `TurnMode`, `Usage`, opcions de debat |
| `providers/base.py` | `Provider` | Converteix una `GenerationRequest` en un flux de `TextDelta` + un `GenerationResult` |
| `orchestrator/store.py` | `Store` | Persistència que necessita el motor (implementada per `storage`) |
| `orchestrator/events.py` | esdeveniments | Missatges servidor → client d'un torn ([PROTOCOL.md](PROTOCOL.md)) |
| `orchestrator/types.py` | `TurnRequest`, `EngineConfig` | Entrada del motor |
| `config.py` | `Settings` | Configuració del procés (variables `AOS_*`) |

## Modes de torn

- **Solo:** respon un sol agent. El més barat.
- **Duel:** tots dos responen en paral·lel i es mostren costat a costat.
- **Debat:**
  1. *Respostes inicials* en paral·lel.
  2. *Rondes de revisió* (per defecte fins a 2): cada agent rep la pregunta, la seva resposta i la de l'altre, i retorna una crítica breu, la seva resposta millorada (o `UNCHANGED` si no cal canviar-la) i un grau d'acord 0–100.
  3. *Parada per consens:* si tots dos superen el llindar (per defecte 85), no es fan més rondes.
  4. *Síntesi:* l'agent sintetitzador combina les dues respostes finals i els punts de desacord en la resposta definitiva.

## Estalvi de tokens

| Tècnica | Com funciona | Com es mesura |
| --- | --- | --- |
| Prompt de sistema propi | Les CLI s'executen amb un prompt de sistema curt en lloc del d'agent de programació, i sense eines | – |
| Context mínim als debats | Les revisions només veuen la pregunta i les dues últimes respostes, no tota la transcripció | – |
| Historial canònic | A l'historial de la conversa només hi van la pregunta i la resposta final (la síntesi), no les rondes intermèdies | – |
| Compactació | Quan l'historial supera el llindar, els missatges antics es resumeixen amb el model ràpid i es conserven els últims | tokens de l'historial original − tokens del context compactat |
| Parada per consens | S'ometen les rondes que queden | tokens mitjans d'una ronda × rondes omeses |
| `UNCHANGED` | Un agent d'acord no reescriu la resposta | longitud de la resposta no reescrita |
| Memòria cau de respostes | Una pregunta idèntica (mateix mode, agents i context) es respon sense cridar cap model | tokens del torn original |
| Memòria cau del proveïdor | Prefixos estables (prompt de sistema primer) perquè Anthropic i OpenAI reaprofitin el càlcul | `cache_read_tokens` |

## Proveïdors

Cada agent té tres modes, escollits amb `AOS_CLAUDE_MODE` i `AOS_CHATGPT_MODE`:

- **`cli`** (per defecte): executa la CLI oficial (`claude`, `codex`) sense eines, amb el prompt per l'entrada estàndard, en un directori buit i amb un entorn mínim. Autenticada amb la teva subscripció (OAuth) un sol cop al VPS.
- **`api`**: SDK oficial amb clau d'API (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) i *prompt caching*.
- **`fake`**: respostes deterministes per a proves i per provar la interfície sense gastar res.

## Seguretat (un sol usuari)

- Només Caddy és accessible des de fora (80/443); l'aplicació escolta a la xarxa interna.
- Inici de sessió amb contrasenya (argon2id) **i** codi TOTP; bloqueig exponencial després d'intents fallits.
- Sessions al servidor (només se'n desa el hash), cookie `__Host-` HttpOnly, Secure, SameSite=Strict, caducitat per inactivitat i absoluta.
- Comprovació d'`Origin` a totes les peticions que canvien estat i al WebSocket.
- CSP estricta (`script-src 'self'`), HSTS, `frame-ancestors 'none'`; el markdown dels models es neteja amb DOMPurify.
- Les CLI s'executen sense eines ni *shell*, sense accés als secrets de l'aplicació, amb temps màxim i matant tot el grup de processos en cancel·lar.
- Contenidors sense root, `no-new-privileges`, sense *capabilities*.

## Latència i connexió

- Una sola connexió WebSocket persistent (sense *handshakes* per petició), amb *ping/pong* i reconnexió automàtica.
- Els torns continuen al servidor si es talla la connexió; en reconnectar, el client recupera els esdeveniments pendents (`turn.subscribe`).
- Les dues IA treballen en paral·lel; el text arriba en *streaming*.
- uvloop + httptools, HTTP/3 a Caddy, fitxers estàtics amb hash i memòria cau llarga, three.js carregat de manera diferida perquè la interfície aparegui a l'instant.
