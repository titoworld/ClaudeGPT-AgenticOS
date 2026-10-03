# Roadmap

## Phase 0 · Setup ✅

- [x] GitHub repository with `main` as the default branch
- [x] Python base (uv, pytest, ruff, strict mypy), CI and a session start hook for Claude Code on the web

## Phase 1 · Vision and architecture ✅

- [x] Research: official CLIs with a subscription, SDKs, web stack and deployment ([ADR 0002](adr/0002-subscriptions-via-official-clis.md))
- [x] Architecture, internal contracts and client-server protocol ([ARCHITECTURE.md](ARCHITECTURE.md), [PROTOCOL.md](PROTOCOL.md))

## Phase 2 · A council of Claude and ChatGPT (MVP) ✅

- [x] Providers: Claude Code (subscription) and the Anthropic API; Codex app-server (subscription) and the OpenAI API; a demo mode
- [x] Turn engine: solo, duel and council, with stop on consensus, compaction and cache ([ADR 0003](adr/0003-council-and-token-savings.md))
- [x] Login with a password and TOTP, sessions, login attempt limits
- [x] A server with a WebSocket that survives connection drops
- [x] An interface with a 3D scene, the council view, a command palette and a dashboard ([ADR 0004](adr/0004-web-and-deployment.md))
- [x] Deployment with Docker Compose + Caddy, and a step-by-step guide
- [x] Model choice per agent (live list + any model id)
- [x] Tokens and cost in euros, a monthly budget and the percentage used of the subscriptions

## Phase 3 · Ideas for later

To decide with the owner, based on real use:

- [x] Attachments to questions: images, PDFs and text files, with thumbnails, in every mode ([ADR 0009](adr/0009-attachments.md))
- [x] PDFs for ChatGPT with the subscription: the extracted text, checked by Claude, and warnings about pages without text, unreadable or with hidden text (P7b, [ADR 0009](adr/0009-attachments.md))
- [x] The Refine mode: both AIs improve a single document round after round, without oversizing it, until you stop them (P8, [ADR 0010](adr/0010-refine-mode.md))
- [x] The interface in English, Spanish and Catalan, and the repository in English ([ADR 0011](adr/0011-internationalization.md))
- [ ] Export conversations (Markdown/PDF)
- [ ] Alerts when a subscription window or the budget gets close to its limit (email or Telegram)
- [ ] Council templates for common tasks (reviewing code, writing, research)
- [ ] Scheduled automatic backups
