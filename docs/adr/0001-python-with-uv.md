# 0001. Python with uv as the project's base

- Status: Accepted
- Date: 2026-09-27

## Context

The project needs a main language before the architecture is defined, so that it has tests, lint and CI from the start. It will be developed mostly with Claude Code on the web, so every session has to be able to install dependencies and validate changes without manual intervention.

## Decision

- Python >= 3.12 (3.13 for development, pinned in `.python-version`), with a `src/` layout and the `agentic_os` package.
- uv for the environment, the dependencies and the lockfile (`uv.lock`).
- pytest for tests, ruff for lint and formatting, mypy in strict mode for types.
- GitHub Actions runs the four checks with Python 3.12 and 3.13.

## Alternatives considered

- **TypeScript:** a good fit if the main interface were web, but the ecosystem of agents and AI is broader in Python.
- **pip + venv / Poetry:** uv is faster, also manages the Python version and produces a reproducible lockfile.

## Consequences

- The official Anthropic and OpenAI SDKs for Python will be available when the provider layer is implemented.
- Strict typing requires annotating all the code, in exchange for catching errors earlier, which is especially useful when an agent writes a good part of the code.
