# 0002. Subscriptions through the official CLIs, with API keys as an alternative

- Status: Accepted
- Date: 2026-09-27

## Context

The owner wants to use their subscriptions (Claude Pro/Max and ChatGPT Plus/Pro) instead of paying per token with API keys. The API SDKs (`anthropic`, `openai`) only accept keys or console credentials, never the subscription. The only official ways to use the subscription are each provider's CLI: Claude Code (`claude`) and Codex (`codex`).

The research (September 2026, versions 2.1.283 and 0.157.1) verified that:

- `claude -p` with `--input-format/--output-format stream-json` gives real text *streaming*, token usage per turn and the subscription's limit windows (`rate_limit_event`). With `--tools ""`, a custom `--system-prompt` and no project configuration, the request is small and cannot execute anything.
- `codex exec --json` does not stream text; `codex app-server` (JSON-RPC over stdio, experimental) does, with token usage and the subscription's limits, and a single process can serve several threads in parallel.
- Terms: Anthropic's Help Center (16 June 2026) counts using `claude -p` in your own projects as use of your plan; extracting the OAuth token to call the API directly, or offering the login to third parties, is forbidden. OpenAI recommends API keys for automation; using the subscription through Codex is not explicitly forbidden, but not blessed either.

## Decision

- Each agent has three modes: `cli` (the default: the subscription, through the unmodified official CLI), `api` (an API key, with *prompt caching*) and `fake` (demo and tests).
- Claude in `cli` mode: one `claude -p` process per call, with a *pool* of pre-warmed processes to hide the startup time; the prompt goes over stdin with `client_composed: true`; an isolated environment and working directory.
- ChatGPT in `cli` mode: a single persistent `codex app-server` process with our own JSON-RPC client; ephemeral threads in read-only mode, without tools and with the base instructions replaced by our prompt.
- Neither the official Codex SDK for Python nor the Claude Agent SDK is used: they drag in binaries of 138 MB and 229 MB and add nothing that our own client does not cover.
- OAuth tokens are never extracted or reused outside the CLIs.

## Alternatives considered

- **API keys only:** simple and with no risk regarding the terms, but not what the owner wants. It remains as the `api` mode.
- **`codex exec --json`:** no text *streaming*, and ~0.5 s of startup per call.
- **Third-party proxies that reuse the session:** against the terms.

## Consequences

- The CLI versions are pinned in the Docker image; `app-server` is experimental, and an update can break the protocol.
- If Anthropic or OpenAI change their terms or billing, switching to `AOS_*_MODE=api` is enough.
- An `ANTHROPIC_API_KEY` in the CLI's environment would take precedence over the subscription: the processes' environment is a closed list.

## Note (2026-09-27): Codex's tools

The decision does not change; this note corrects a statement in the "Decision" section. "Without tools" is only exact for Claude: with `--tools ""` the request declares no tool. A security review with Codex 0.157.1 found that the model catalog it ships with turns on code mode and subagents for the `gpt-6-*` models, and that the configuration does not turn them off. ChatGPT still gets:

- a code tool (`exec`) that runs in a child process of the `app-server` (`codex-code-mode`), in an isolated V8 environment without access to files or to the network;
- the subagent tools (`spawn_agent` and related ones), which open new threads inside the same `app-server`.

A prompt injection (a pasted text, or Claude's answer within a debate) can make ChatGPT use them: burning CPU with loops, or opening subagents that keep consuming the plan after the call has ended. Mitigations applied:

- `agents.max_threads=1`: at most one subagent at a time (0 is not accepted). It does not limit how many a call opens, because interrupting one frees its slot;
- the app immediately interrupts any turn of a thread that does not belong to a call in progress;
- a call in which ChatGPT uses subagents (opens them or gives them work) more than 3 times is stopped with an error;
- once no call is in progress, the app restarts an `app-server` process that has opened subagents: their threads, even once stopped, do not free their memory;
- a CPU limit on the app's container (`cpus`, `APP_CPUS` in `.env`);
- Codex's SQLite state and logs, which keep the text of each call, live in a private tmpfs (`/run/codex-state`, `AOS_CODEX_STATE_DIR`), outside `CODEX_HOME`, the volumes and the backups. The app deletes the logs before starting each process: they do not outlive the process, and a full tmpfs does not stop it from starting again.

Removing them entirely means pinning a model catalog of our own without these tools (`model_catalog_json`): it would freeze the list of models (new ones would only work by their id) and would have to be redone for every Codex version. This is still to be tested against the real ChatGPT service; if it is done, or if a new version of Codex allows turning them off, this note will need revisiting.
