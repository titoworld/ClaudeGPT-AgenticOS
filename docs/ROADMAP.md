# Full de ruta

## Fase 0 · Inicialització ✅

- [x] Repositori a GitHub amb `main` com a branca per defecte
- [x] Base de Python (uv, pytest, ruff, mypy estricte), CI i hook d'inici de sessió per a Claude Code a la web

## Fase 1 · Visió i arquitectura ✅

- [x] Recerca: CLI oficials amb subscripció, SDK, stack web i desplegament ([ADR 0002](adr/0002-subscriptions-via-official-clis.md))
- [x] Arquitectura, contractes interns i protocol client-servidor ([ARCHITECTURE.md](ARCHITECTURE.md), [PROTOCOL.md](PROTOCOL.md))

## Fase 2 · Consell de Claude i ChatGPT (MVP) ✅

- [x] Proveïdors: Claude Code (subscripció) i API d'Anthropic; Codex app-server (subscripció) i API d'OpenAI; mode de demostració
- [x] Motor de torns: solo, duel i consell, amb parada per consens, compactació i memòria cau ([ADR 0003](adr/0003-council-and-token-savings.md))
- [x] Inici de sessió amb contrasenya i TOTP, sessions, límits d'intents
- [x] Servidor amb WebSocket resistent a talls
- [x] Interfície amb escena 3D, vista del consell, paleta d'ordres i tauler ([ADR 0004](adr/0004-web-and-deployment.md))
- [x] Desplegament amb Docker Compose + Caddy i guia pas a pas
- [x] Selecció de model per agent (llista en directe + identificador lliure)
- [x] Tokens i cost en euros, pressupost mensual i percentatge d'ús de les subscripcions

## Fase 3 · Idees per a més endavant

Per decidir amb el propietari segons l'ús real:

- [x] Adjunts a les preguntes: imatges, PDF i fitxers de text, amb miniatures, a tots els modes ([ADR 0009](adr/0009-attachments.md))
- [x] PDF per a ChatGPT amb la subscripció: el text extret, contrastat per Claude, i avisos de les pàgines sense text, il·legibles o amb text amagat (P7b, [ADR 0009](adr/0009-attachments.md))
- [x] Mode «Perfecciona»: les dues IA milloren un sol document ronda rere ronda, sense sobredimensionar-lo, fins que l'atures (P8, [ADR 0010](adr/0010-refine-mode.md))
- [ ] Exportar converses (Markdown/PDF)
- [ ] Avisos quan una finestra de subscripció o el pressupost s'acosta al límit (correu o Telegram)
- [ ] Plantilles de consell per a tasques habituals (revisar codi, redactar, investigar)
- [ ] Còpies de seguretat automàtiques programades
