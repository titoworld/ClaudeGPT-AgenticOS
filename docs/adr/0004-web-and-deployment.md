# 0004. Web interface and deployment

- Status: Accepted
- Date: 2026-09-27

## Context

The tool is for a single owner, exposed to the Internet from a VPS, and it has to be very visual, fast and secure.

## Decision

- **Backend:** FastAPI + uvicorn (uvloop, httptools), SQLite in WAL mode. One persistent WebSocket connection per tab; turns keep running on the server if the connection drops, and the client recovers their events by sequence number.
- **Frontend:** Svelte 5 (runes) + Vite + TypeScript. A 3D scene with three.js (`WebGLRenderer` + `EffectComposer` + bloom), loaded lazily; `WebGPURenderer` was ruled out because in r186 it fails without falling back to WebGL in some browsers. Custom SVG charts (3.7 KB) instead of 50 KB libraries. Markdown with marked + DOMPurify.
- **Security:** argon2id password + TOTP with reuse protection, server-side sessions stored as a hash, a `__Host-` SameSite=Strict cookie, an `Origin` check, a strict CSP without inline scripts, and a persistent exponential lockout.
- **Deployment:** Docker Compose with Caddy (automatic TLS, HTTP/3) as the only exposed service; the app runs without root, without *capabilities* and with a read-only file system; the official CLIs are included as pinned native binaries.

## Alternatives considered

- **React / Vue:** heavier for a project of this size; Svelte compiles to little JavaScript.
- **Nginx + Certbot:** more pieces to maintain than Caddy.
- **VPN-only access (Tailscale/WireGuard):** more secure but less convenient; it is documented as an optional hardening step, together with an IP allowlist.

## Consequences

- The image weighs about 800 MB because of the CLI binaries.
- No Node at run time: only in the build stage.
