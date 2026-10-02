# 0002. Subscripcions via les CLI oficials, amb claus d'API com a alternativa

- Estat: Acceptat
- Data: 2026-09-27

## Context

El propietari vol fer servir les seves subscripcions (Claude Pro/Max i ChatGPT Plus/Pro) en lloc de pagar per token amb claus d'API. Els SDK d'API (`anthropic`, `openai`) només accepten claus o credencials de consola, mai la subscripció. Les úniques vies oficials que fan servir la subscripció són les CLI de cada proveïdor: Claude Code (`claude`) i Codex (`codex`).

La recerca (setembre 2026, versions 2.1.283 i 0.157.1) va verificar:

- `claude -p` amb `--input-format/--output-format stream-json` dona *streaming* real de text, ús de tokens per torn i finestres de límit de la subscripció (`rate_limit_event`). Amb `--tools ""`, `--system-prompt` propi i sense configuració de projecte, la petició és petita i no pot executar res.
- `codex exec --json` no dona *streaming* de text; `codex app-server` (JSON-RPC per stdio, experimental) sí, amb ús de tokens i límits de la subscripció, i un sol procés pot atendre diversos fils en paral·lel.
- Termes: el Help Center d'Anthropic (16/06/2026) inclou l'ús de `claude -p` en projectes propis com a ús del pla; està prohibit extreure el token OAuth per cridar l'API directament o oferir el login a tercers. OpenAI recomana claus d'API per a l'automatització; l'ús de la subscripció via Codex no està prohibit explícitament però tampoc beneït.

## Decisió

- Cada agent té tres modes: `cli` (per defecte, subscripció via CLI oficial sense modificar), `api` (clau d'API amb *prompt caching*) i `fake` (demostració i proves).
- Claude en mode `cli`: un procés `claude -p` per crida, amb un *pool* de processos preescalfats per amagar el temps d'arrencada; el prompt s'envia per stdin amb `client_composed: true`; entorn i directori de treball aïllats.
- ChatGPT en mode `cli`: un sol procés `codex app-server` persistent amb un client JSON-RPC propi; fils efímers en mode només lectura, sense eines i amb les instruccions base substituïdes pel nostre prompt.
- No es fa servir l'SDK oficial de Codex per Python ni el Claude Agent SDK: arrosseguen binaris de 138 MB i 229 MB i no aporten res que el client propi no cobreixi.
- Mai s'extreuen ni es reutilitzen tokens OAuth fora de les CLI.

## Alternatives considerades

- **Només claus d'API:** simple i sense risc de termes, però no és el que vol el propietari. Queda com a mode `api`.
- **`codex exec --json`:** sense *streaming* de text i ~0,5 s d'arrencada per crida.
- **Proxies de tercers que reutilitzen la sessió:** contraris als termes.

## Conseqüències

- Les versions de les CLI queden fixades a la imatge Docker; `app-server` és experimental i una actualització pot trencar el protocol.
- Si Anthropic o OpenAI canvien els termes o la facturació, n'hi ha prou de canviar `AOS_*_MODE=api`.
- Una `ANTHROPIC_API_KEY` a l'entorn de la CLI passaria per davant de la subscripció: l'entorn dels processos és una llista tancada.

## Nota (2026-09-27): les eines de Codex

La decisió no canvia; aquesta nota corregeix una afirmació de la secció «Decisió». «Sense eines» només és exacte per a Claude: amb `--tools ""` la petició no declara cap eina. Una revisió de seguretat amb Codex 0.157.1 va comprovar que el catàleg de models que porta incorporat activa, per als models `gpt-6-*`, el mode de codi i els subagents, i que la configuració no ho desactiva. ChatGPT continua rebent:

- una eina de codi (`exec`) que s'executa en un procés fill de l'`app-server` (`codex-code-mode`), en un entorn aïllat V8 sense accés als fitxers ni a la xarxa;
- les eines de subagents (`spawn_agent` i relacionades), que obren fils nous dins del mateix `app-server`.

Una injecció de prompt (un text enganxat o la resposta de Claude dins d'un debat) pot fer que ChatGPT les faci servir: gastar CPU amb bucles o obrir subagents que continuen consumint el pla quan la crida ja ha acabat. Mitigacions aplicades:

- `agents.max_threads=1`: com a màxim un subagent alhora (0 no s'accepta). No limita quants n'obre una crida, perquè en interrompre'n un en queda lliure la plaça;
- l'aplicació interromp de seguida qualsevol torn d'un fil que no pertanyi a una crida en curs;
- una crida en què ChatGPT fa servir subagents (n'obre o els dona feina) més de 3 vegades s'atura amb un error;
- quan ja no hi ha cap crida en curs, l'aplicació reinicia el procés d'`app-server` que ha obert subagents: els seus fils, encara que s'hagin aturat, no alliberen la memòria;
- límit de CPU del contenidor de l'aplicació (`cpus`, `APP_CPUS` a `.env`);
- l'estat SQLite i els registres de Codex, que guarden el text de cada crida, viuen en un tmpfs privat (`/run/codex-state`, `AOS_CODEX_STATE_DIR`), fora de `CODEX_HOME`, dels volums i de les còpies de seguretat. L'aplicació n'esborra els registres abans d'engegar cada procés: no sobreviuen al procés i un tmpfs ple no li impedeix tornar a arrencar.

Treure-les del tot vol dir fixar un catàleg de models propi sense aquestes eines (`model_catalog_json`): congelaria la llista de models (els nous només funcionarien pel seu identificador) i caldria refer-lo a cada versió de Codex. Queda pendent de provar-ho amb el servei real de ChatGPT; si es fa, o si una versió nova de Codex les permet desactivar, caldrà revisar aquesta nota.
