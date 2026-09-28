# CLAUDE.md

Instruccions per a les sessions de Claude Code en aquest repositori.

## Idioma

- Parla amb el propietari en l'idioma en què t'escrigui (normalment català, de vegades castellà).
- Documentació (`README.md`, `docs/`) i textos de la interfície en català.
- Codi, identificadors i comentaris en anglès.
- Missatges de commit en anglès, amb [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).

## Què és

ClaudeGPT OS: un consell privat de Claude i ChatGPT per a un sol propietari, autoallotjat en un VPS. Llegeix [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md), [docs/PROTOCOL.md](docs/PROTOCOL.md) i [docs/adr/](docs/adr/) abans de fer canvis d'estructura. Les decisions noves d'arquitectura es proposen al propietari i es registren com a ADR.

## Comandes

```bash
uv sync                                   # dependències (el hook d'inici de sessió ja ho fa a la web)
uv run pytest                             # tests del backend
uv run ruff check . && uv run ruff format --check .
uv run mypy                               # tipus, mode estricte
cd web && npm run check && npm test && npm run build   # frontend
uv run agentic-os serve --dev             # servidor local (vegeu el README per a les variables)
```

Abans de cada commit han de passar totes aquestes comprovacions: és el mateix que executa la CI.

## Contractes

- El protocol client-servidor viu a `docs/PROTOCOL.md`, `web/src/lib/protocol.ts` i `src/agentic_os/server/`. Quan canviïs un missatge o una ruta, actualitza els tres alhora.
- Els contractes interns són `domain.py`, `providers/base.py`, `orchestrator/store.py`, `orchestrator/events.py` i `orchestrator/types.py`. Un canvi aquí afecta proveïdors, motor, emmagatzematge i servidor.
- La CSP del servidor (`server/middleware.py`) i la de `web/vite.config.ts` han de ser idèntiques (hi ha un test que ho comprova).

## Convencions

- Python >= 3.12, layout `src/`, tipat complet (mypy estricte), asyncio. `CancelledError` sempre es propaga i allibera recursos.
- Dependències només amb `uv add` / `npm install --save-exact`; mai editis `uv.lock` a mà. Justifica cada dependència nova.
- **Els tests no criden APIs reals ni fan login** (costen diners i requereixen xarxa). Fes servir `FakeProvider`, les CLI falses de `tests/providers/fixtures/` o transports simulats.
- Tot canvi de comportament porta el seu test. Frontend: vitest per a la lògica i per als components, que es munten a jsdom amb `web/src/lib/test-render.ts` (en mode test, `web/vite.config.ts` resol la condició `browser` de Svelte); revisa visualment els canvis d'interfície.

## Seguretat (no negociable)

- Mai extreguis ni reutilitzis tokens OAuth de les CLI fora de les mateixes CLI (ho prohibeixen els termes). Les CLI s'executen amb una llista tancada de variables d'entorn: no hi afegeixis claus d'API ni variables `AOS_*`.
- La CLI de Claude sempre porta `--tools ""`, `--setting-sources=`, `--strict-mcp-config`, `--disable-slash-commands` i `client_composed: true`; Codex sempre en mode només lectura i sense aprovacions.
- El Markdown dels models sempre passa per DOMPurify; res de `{@html}` amb contingut no sanejat. Res de scripts en línia (CSP).
- Els secrets només en variables d'entorn o al `.env` del servidor (fora del repositori).

## Git

- `main` és la branca per defecte i ha d'estar sempre en verd.
- Treballa en una branca i porta els canvis a `main` amb un pull request.
- Si canvies ordres, estructura o convencions, actualitza aquest fitxer i el README en el mateix canvi.
