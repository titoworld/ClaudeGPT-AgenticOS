# CLAUDE.md

Instructions for Claude Code sessions in this repository.

## Language

See [ADR 0011](docs/adr/0011-internationalization.md).

- Talk to the owner in the language they write in (usually Catalan, sometimes Spanish).
- Documentation (`README.md`, `docs/`) in English.
- The interface's texts live in the catalogs of `web/src/lib/i18n/areas/`, in English, Spanish and Catalan: English is the source, and TypeScript checks that the three languages have the same keys and parameters. Never hardcode a text in a component, and never keep one in a module-level constant (it would not follow a change of language). Bold and code inside a text are written `**…**` and `` `…` `` and drawn by `RichText`, never with `{@html}`.
- The server's texts live in `src/agentic_os/locales/` and are made with `agentic_os.i18n.t()`, in the language of the request, the connection or the command line. Caches shared by every client keep `lazy()` texts, never translated ones. Logic compares codes (such as `reason_code`), never translated texts.
- Every text for the models stays in English, whatever the language of the turn: the prompts (which ask them to answer in the language of the user's message), the notes and the markers around the attachments. Logs are in English too (`ProviderError.log_text`).
- The tests check the Catalan texts by default (`web/src/lib/i18n/test-setup.ts`, `tests/conftest.py`); the i18n tests check the other languages.
- Code, identifiers and comments in English.
- Commit messages in English, with [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).

## What it is

ClaudeGPT OS: a private council of Claude and ChatGPT for a single owner, self-hosted on a VPS. Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/PROTOCOL.md](docs/PROTOCOL.md) and [docs/adr/](docs/adr/) before making structural changes. New architecture decisions are proposed to the owner and recorded as ADRs.

## Commands

```bash
uv sync                                   # dependencies (the session start hook already does it on the web)
uv run pytest                             # backend tests
uv run ruff check . && uv run ruff format --check .
uv run mypy                               # types, strict mode
cd web && npm run check && npm test && npm run build   # frontend
uv run agentic-os serve --dev             # local server (see the README for the variables)
```

All these checks must pass before every commit: they are what the CI runs.

## Contracts

- The client-server protocol lives in `docs/PROTOCOL.md`, `web/src/lib/protocol.ts` and `src/agentic_os/server/`. When you change a message or a route, update all three together.
- The internal contracts are `domain.py`, `providers/base.py`, `orchestrator/store.py`, `orchestrator/events.py` and `orchestrator/types.py`. A change here affects the providers, the engine, the storage and the server.
- The server's CSP (`server/middleware.py`) and the one in `web/vite.config.ts` must be identical (a test checks it).
- The limits the documents give (sizes, queues, times...), the `AOS_*` variables named in the documentation and in the interface, and the files they cite must match the code: `tests/test_docs.py` checks it. If you change a constant, change the document too.

## Conventions

- Python >= 3.12, `src/` layout, full typing (strict mypy), asyncio. `CancelledError` always propagates and releases resources.
- Dependencies only with `uv add` / `npm install --save-exact`; never edit `uv.lock` by hand. Justify every new dependency.
- **Tests never call real APIs or log in** (that costs money and needs the network). Use `FakeProvider`, the fake CLIs in `tests/providers/fixtures/` or simulated transports.
- Tests must also work on macOS: process state is checked with `tests/portability.py` (never by reading `/proc` directly), and checks of a child process's environment ignore the variables the system adds to it (`without_platform_variables`).
- Every change of behavior comes with its test. Frontend: vitest for the logic and for the components, which are mounted in jsdom with `web/src/lib/test-render.ts` (in test mode, `web/vite.config.ts` resolves Svelte's `browser` condition); review interface changes visually. The showcase in `web/src/showcase/` mounts the whole app with example conversations, without a backend; if an interface change shows in the README's screenshots, retake them with the showcase.

## Security (non-negotiable)

- Never extract or reuse the CLIs' OAuth tokens outside the CLIs themselves (the terms forbid it). The CLIs run with a closed list of environment variables: do not add API keys or `AOS_*` variables to it.
- The Claude CLI always runs with `--tools ""`, `--setting-sources=`, `--strict-mcp-config`, `--disable-slash-commands` and `client_composed: true`; Codex always in read-only mode and without approvals.
- The models' Markdown always goes through DOMPurify; no `{@html}` with unsanitized content. No inline scripts (CSP).
- Secrets only in environment variables or in the server's `.env` (outside the repository).

## Git

- `main` is the default branch and must always be green.
- Work on a branch and bring the changes to `main` with a pull request.
- If you change commands, structure or conventions, update this file and the README in the same change.
