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
   ├── server/       API REST, WebSocket, sessió, fitxers estàtics del frontend
   ├── security/     contrasenya argon2id, TOTP, sessions, límits d'intents, capçaleres
   ├── orchestrator/ motor de torns: solo · duel · debat, compactació, memòria cau, comptabilitat
   ├── providers/    Claude i ChatGPT, cadascun en mode cli · api · fake
   ├── storage/      SQLite (WAL): converses, missatges, ús, estalvis, memòria cau, sessions
   └── pricing · fx  preus per model (USD/MTok) i canvi USD→EUR del BCE
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
| `pricing.py` | `ModelPrice`, `estimate_cost_usd` | Preus per defecte i propis; cost de cada crida |
| `fx.py` | `FxRate` | Tipus de canvi diari del BCE amb valor manual de reserva |

## Modes de torn

- **Solo:** respon un sol agent. El més barat.
- **Duel:** tots dos responen en paral·lel i es mostren costat a costat.
- **Debat:**
  1. *Respostes inicials* en paral·lel.
  2. *Rondes de revisió* (per defecte fins a 2): cada agent rep la pregunta, la seva resposta i la de l'altre, i retorna una crítica breu, la seva resposta millorada (o `UNCHANGED`, amb una nota curta opcional, si no cal canviar-la) i un grau d'acord 0–100.
  3. *Parada per consens:* si tots dos superen el llindar (per defecte 85), no es fan més rondes.
  4. *Síntesi:* l'agent sintetitzador combina les dues respostes finals i els punts de desacord en la resposta definitiva.

## Estalvi de tokens

| Tècnica | Com funciona | Com es mesura |
| --- | --- | --- |
| Prompt de sistema propi | Les CLI s'executen amb un prompt de sistema curt en lloc del d'agent de programació: la de Claude sense cap eina i la de Codex sense les que es poden desactivar ([Seguretat](#seguretat-un-sol-usuari)) | – |
| Context mínim als debats | Les revisions només veuen la pregunta i les dues últimes respostes, no tota la transcripció | – |
| Historial canònic | A l'historial de la conversa només hi van la pregunta i la resposta final (la síntesi), no les rondes intermèdies | – |
| Compactació | Quan l'historial supera el llindar, els missatges antics es resumeixen amb el model ràpid i es conserven els últims | tokens de l'historial original − tokens del context compactat |
| Parada per consens | S'ometen les rondes que queden | tokens mitjans d'una ronda × rondes omeses |
| `UNCHANGED` | Un agent d'acord no reescriu la resposta (com a molt hi afegeix una nota curta) | longitud de la resposta no reescrita |
| Memòria cau de respostes | Una pregunta idèntica (mateix mode, agents, models i context) es respon sense cridar cap model. Si no se sap quin model respondrà (l'estat del proveïdor tarda, falla o diu que no està disponible), el torn no llegeix ni desa la memòria cau | tokens del torn original |
| Memòria cau del proveïdor | Prefixos estables (prompt de sistema primer) perquè Anthropic i OpenAI reaprofitin el càlcul | `cache_read_tokens` |

## Proveïdors

Cada agent té tres modes, escollits amb `AOS_CLAUDE_MODE` i `AOS_CHATGPT_MODE`:

- **`cli`** (per defecte): executa la CLI oficial (`claude`, `codex`) amb el prompt per l'entrada estàndard, en un directori buit i amb un entorn mínim: Claude sense cap eina i Codex en mode només lectura, sense les eines que es poden desactivar ([Seguretat](#seguretat-un-sol-usuari)). Autenticada amb la teva subscripció (OAuth) un sol cop al VPS.
- **`api`**: SDK oficial amb clau d'API (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) i *prompt caching*.
- **`fake`**: respostes deterministes per a proves i per provar la interfície sense gastar res.

## Models, costos i límits

- **Models:** cada agent té un model per defecte i un de ràpid (per als resums), configurables des de la interfície. La llista es demana en directe al proveïdor (API de models d'Anthropic i d'OpenAI, `model/list` de Codex; a la CLI de Claude, els àlies `opus`, `sonnet`, `haiku` i `fable`, que sempre apunten a l'última versió). També s'accepta qualsevol identificador, per fer servir un model nou el mateix dia que surt.
- **Cost:** el motor calcula el cost de cada crida amb la taula de preus (USD per milió de tokens, editable). En mode API és el cost real; en mode subscripció és el *valor equivalent* a preus d'API. La interfície ho mostra en euros amb el tipus del BCE.
- **Percentatge usat:** en mode subscripció, les finestres de 5 hores i setmanal que informen Anthropic i OpenAI; en mode API, el pressupost mensual en euros; i, si indiques el preu del pla, quant valor n'has tret aquest mes.

## Integritat de les respostes

Una resposta pot ser completa, pot estar tallada o pot ser una negativa, i el sistema no les confon mai ([ADR 0005](adr/0005-integritat-de-les-respostes.md)).

- **Resposta tallada:** és una resposta parcial útil, però mai completa. Es desa amb `truncated` i el motiu (`finish_reason`) i la interfície la mostra com a incompleta. El torn no entra a la memòria cau. En un debat continua alimentant les revisions i la síntesi, però el prompt la marca com a incompleta. Si no hi ha cap text, la crida falla dient que s'ha esgotat el límit de sortida, i el cost es registra igualment. Un resum de compactació tallat no es fa servir mai.
- **Negativa:** és un error propi (`RefusalError`), no reintentable, amb el seu missatge i el seu cost. El text emès abans de la negativa no es desa mai. La CLI de Claude, davant d'una negativa, torna a preguntar pel seu compte una vegada; el proveïdor l'atura abans (vegeu la taula de sota).
- **Flux interromput:** és un error reintentable, mai una resposta completa. Passa quan la connexió cau a mig flux, quan el flux acaba sense l'esdeveniment final o quan Codex reintenta una resposta que ja s'estava mostrant. El motor només el reintenta si encara no ha mostrat res.
- **Revisions:** l'analitzador tracta com a text les etiquetes escrites dins de codi i les etiquetes d'obertura de la mateixa secció. Un bloc de codi que no es tanca mai no era codi. Si una etiqueta de tancament va seguida de text, es decideix amb la següent etiqueta: si torna a aparèixer la mateixa, la primera era text; si comença una altra secció o s'acaba la resposta, tancava la secció, i el text del mig (un encapçalament, una salutació) es descarta. `UNCHANGED` sol conserva la resposta anterior; en majúscules, a més, pot anar seguit d'una nota curta a la mateixa línia, que es desa a part.

### Pressupost de sortida

`max_output_tokens` és el màxim de tokens de sortida **facturats** d'una crida, amb el raonament inclòs. Cap adaptador no l'apuja. El raonament es tria a part, amb `reasoning`:

- respostes, revisions i síntesis: 16.000 tokens, amb el raonament per defecte;
- resums: 2.000 tokens, amb el raonament `off`.

Cada proveïdor ho aplica així:

| Proveïdor | Límit de sortida | Raonament `off` |
| --- | --- | --- |
| API de Claude | `max_tokens` exacte. Si no hi cap el pressupost mínim de pensament (1.024), no pensa. `stop_reason: "max_tokens"` dona una resposta tallada. | Pensament desactivat |
| API d'OpenAI | `max_output_tokens` exacte. `response.incomplete` dona una resposta tallada. | L'esforç més baix que accepta el model: `none` a GPT-6 Sol i Luna, `minimal` als primers GPT-5 i `low` a la resta |
| CLI de Claude | `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, calculat pel proveïdor, forma part de la clau dels processos calents. La CLI l'aplica a cada petició que fa. Quan una resposta s'atura per `max_tokens`, la CLI 2.1.283 la reprèn pel seu compte (fins a 3 vegades) amb una petició nova uns 10 ms després; després d'una negativa, torna a preguntar una vegada. Cada petició d'aquestes tornaria a facturar tot el context. Per això el proveïdor mata el grup de processos amb SIGKILL tan bon punt llegeix aquest `stop_reason` i acaba la crida amb l'ús d'aquella petició: una resposta tallada o una negativa. Amb SIGTERM no n'hi ha prou, perquè la CLI s'atura ordenadament i envia la petició igualment. Comprovat amb la CLI real contra una API simulada en local. | `--thinking disabled` |
| Codex (app-server 0.157.1) | El protocol no té cap camp per al límit. La crida atura el torn (`turn/interrupt`) quan el text visible estimat (caràcters / 4) supera el pressupost. És aproximat: no compta el raonament, i l'ús és el que informa Codex. | `low`, el nivell més baix del catàleg |

## Seguretat (un sol usuari)

- Només Caddy és accessible des de fora (80/443); l'aplicació escolta a la xarxa interna. El cos de les peticions té un màxim d'1 MiB: Caddy el passa a l'aplicació a mesura que arriba, sense acumular-lo en memòria, i l'aplicació respon 408 i tanca la connexió si no ha arribat sencer en 15 s (Caddy talla als 30 s), així que una pujada lenta no ocupa cap connexió gaire estona; els WebSockets no passen per aquest límit.
- Inici de sessió amb contrasenya (argon2id) **i** codi TOTP; bloqueig exponencial després d'intents fallits. Un navegador on ja s'ha entrat (cookie de dispositiu conegut) només es bloqueja pels seus propis errors; `agentic-os reset-throttle` aixeca tots els bloquejos.
- Sessions al servidor (només se'n desa el hash), cookie `__Host-` HttpOnly, Secure, SameSite=Strict, caducitat per inactivitat i absoluta.
- Comprovació d'`Origin` a totes les peticions que canvien estat i al WebSocket.
- CSP estricta (`script-src 'self'`), HSTS, `frame-ancestors 'none'`; el markdown dels models es neteja amb DOMPurify.
- Les CLI s'executen sense *shell* ni accés als secrets de l'aplicació, amb temps màxim i matant tot el grup de processos en cancel·lar. La de Claude no té cap eina. Codex 0.157.1 encara ofereix a ChatGPT una eina de codi en un procés fill (entorn aïllat V8, sense fitxers ni xarxa) i eines de subagents: l'aplicació només en deixa córrer un alhora (`agents.max_threads=1`), interromp de seguida els torns que no són de cap crida en curs, atura la crida que en fa servir més de 3 vegades i, quan ja no hi ha cap crida en curs, reinicia el procés de Codex que n'hagi obert algun ([ADR 0002](adr/0002-subscripcions-via-cli-oficials.md)). L'estat i els registres de Codex, que contenen els prompts, viuen en un tmpfs privat, i els registres s'esborren cada vegada que s'engega Codex. Per això `agentic-os doctor` engega el seu propi Codex amb un directori d'estat temporal, que esborra en acabar: mai no comparteix el de l'aplicació en marxa. També `claude --version` i `codex --version` s'executen amb la llista tancada de variables d'entorn de cada CLI.
- Contenidors sense root (l'aplicació amb l'usuari 10001 i Caddy amb el 10002; només `caddy-init` corre uns segons com a root, sense xarxa i amb només les *capabilities* que necessita `chown`, per donar els volums de Caddy al seu usuari), `no-new-privileges`, sense *capabilities* efectives i amb límits de memòria, CPU i processos.

## Latència i connexió

- Una sola connexió WebSocket persistent (sense *handshakes* per petició), amb *ping/pong* i reconnexió automàtica.
- Els torns continuen al servidor si es talla la connexió; en reconnectar, el client recupera els esdeveniments pendents (`turn.subscribe`).
- El procés de Codex es recupera sol. Cada petició té un temps màxim que inclou escriure-la, perquè un procés encallat que deixa de llegir l'entrada no pugui bloquejar les altres. Quan s'allibera una crida, un procés que no respon es reinicia, i després de fallades seguides els reinicis s'espacien com a molt 30 s. Si una crida no rep resposta en començar, una petició barata distingeix un procés encallat, que es reinicia de seguida, d'un d'ocupat: aquest continua servint les altres crides i es reinicia quan queda lliure.
- Les dues IA treballen en paral·lel; el text arriba en *streaming*.
- uvloop + httptools, HTTP/3 a Caddy, fitxers estàtics amb hash i memòria cau llarga, three.js carregat de manera diferida perquè la interfície aparegui a l'instant.
