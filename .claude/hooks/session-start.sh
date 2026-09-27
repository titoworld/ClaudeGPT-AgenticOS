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
