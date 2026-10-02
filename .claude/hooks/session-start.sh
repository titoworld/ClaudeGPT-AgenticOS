#!/bin/bash
# SessionStart hook: install project dependencies so tests and linters work
# from the first moment in Claude Code on the web sessions.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  pip install --quiet uv
fi

uv sync

# Expose the virtualenv tools (pytest, ruff, mypy...) directly on PATH.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

# Frontend dependencies (svelte-check, vitest, vite). npm keeps a copy of the
# lockfile in node_modules/.package-lock.json: reinstall only when the real
# lockfile is newer, so a resumed session does not wipe a working install.
if [ -f web/package-lock.json ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "session-start: npm not found, skipping web/ dependencies" >&2
  elif [ ! -f web/node_modules/.package-lock.json ] ||
    [ web/package-lock.json -nt web/node_modules/.package-lock.json ]; then
    npm ci --prefix web --no-audit --no-fund
  fi
fi
