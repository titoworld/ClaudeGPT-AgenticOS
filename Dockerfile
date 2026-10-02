# syntax=docker/dockerfile:1
# ClaudeGPT OS image: Svelte SPA (Node, build only) + official Claude Code and
# Codex CLIs (native binaries, no Node at runtime) + FastAPI app (uv).
#
# The CLI versions (CLAUDE_CLI_VERSION, CODEX_CLI_VERSION below) are pinned and
# move together with the app code. To try another release, set them in .env
# and run `docker compose up -d --build`.

ARG PYTHON_IMAGE=python:3.13-slim-trixie
ARG NODE_IMAGE=node:24-trixie-slim
ARG UV_VERSION=0.12.19

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------- 1) frontend
# The output is plain static files, so build on the native platform even when
# cross-building the image for another architecture.
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
# No dependency needs an install script (only fsevents, macOS-only, has one).
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund --ignore-scripts
COPY web/ ./
RUN npm run build

# ------------------------------------------------- 2) official CLIs (binaries)
# npm is only used to fetch the per-platform native binaries of the pinned
# releases: the glibc build of Claude Code and the static musl build of Codex.
FROM ${NODE_IMAGE} AS clis
ARG CLAUDE_CLI_VERSION=2.1.283
ARG CODEX_CLI_VERSION=0.157.1
ARG TARGETARCH
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /clis
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund --ignore-scripts --prefix /clis \
      "@anthropic-ai/claude-code@${CLAUDE_CLI_VERSION}" \
      "@openai/codex@${CODEX_CLI_VERSION}"
RUN arch="${TARGETARCH:-$(uname -m)}"; \
    case "$arch" in \
      amd64 | x86_64) cc=linux-x64 cx=linux-x64 triple=x86_64-unknown-linux-musl ;; \
      arm64 | aarch64) cc=linux-arm64 cx=linux-arm64 triple=aarch64-unknown-linux-musl ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    install -d /out/cli \
 && install -m 0755 "node_modules/@anthropic-ai/claude-code-${cc}/claude" /out/cli/claude \
 && cp -a "node_modules/@openai/codex-${cx}/vendor/${triple}" /out/codex \
 && /out/cli/claude --version \
 && CODEX_HOME=/tmp/codex-home /out/codex/bin/codex --version

# ---------------------------------------------------------------- 3) runtime
FROM ${PYTHON_IMAGE} AS runtime

# The SDKs verify TLS against the OS trust store.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user with a fixed UID so volume ownership survives rebuilds. /data
# and the HOME (CLI logins) are volumes; everything else stays read-only.
# /run/codex-state holds Codex's SQLite state and logs, which record every
# prompt: docker-compose.yml mounts a private tmpfs there, so they never reach
# the volumes (or their backups).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home \
      --shell /usr/sbin/nologin app \
 && install -d -o app -g app -m 0700 /data /home/app/.claude /home/app/.codex \
      /run/codex-state \
 && install -d -m 0755 /opt/cli \
 && ln -s /opt/codex/bin/codex /opt/cli/codex

# Large and rarely changing: before the app layers.
COPY --from=clis /out/cli/claude /opt/cli/claude
COPY --from=clis /out/codex /opt/codex

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
# Dependencies first (cached until uv.lock changes), then the project itself,
# installed as a regular package. uv is mounted, not copied into the image.
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

COPY --from=web /src/web/dist /app/web/dist

ENV PATH=/app/.venv/bin:/opt/cli:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    AOS_DATA_DIR=/data \
    AOS_WEB_DIST=/app/web/dist \
    AOS_CLAUDE_CLI_PATH=/opt/cli/claude \
    AOS_CODEX_CLI_PATH=/opt/cli/codex \
    AOS_CODEX_STATE_DIR=/run/codex-state \
    CLAUDE_CONFIG_DIR=/home/app/.claude \
    CODEX_HOME=/home/app/.codex \
    DISABLE_AUTOUPDATER=1

# The working directory stays root-owned and read-only: never run from the
# writable HOME volume (the cwd ends up on sys.path).
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]
CMD ["agentic-os", "serve", "--host", "0.0.0.0", "--port", "8000"]
