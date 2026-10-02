# 0004. Interfície web i desplegament

- Estat: Acceptat
- Data: 2026-09-27

## Context

L'eina és per a un sol propietari, exposada a Internet des d'un VPS, i ha de ser molt visual, ràpida i segura.

## Decisió

- **Backend:** FastAPI + uvicorn (uvloop, httptools), SQLite en mode WAL. Una connexió WebSocket persistent per pestanya; els torns continuen al servidor si es talla la connexió i el client en recupera els esdeveniments per número de seqüència.
- **Frontend:** Svelte 5 (runes) + Vite + TypeScript. Escena 3D amb three.js (`WebGLRenderer` + `EffectComposer` + bloom), carregada de manera diferida; `WebGPURenderer` es va descartar perquè a r186 falla sense tornar a WebGL en alguns navegadors. Gràfics SVG propis (3,7 KB) en lloc de llibreries de 50 KB. Markdown amb marked + DOMPurify.
- **Seguretat:** contrasenya argon2id + TOTP amb protecció de reutilització, sessions al servidor amb hash, cookie `__Host-` SameSite=Strict, comprovació d'`Origin`, CSP estricta sense scripts en línia, bloqueig exponencial persistent.
- **Desplegament:** Docker Compose amb Caddy (TLS automàtic, HTTP/3) com a únic servei exposat; l'aplicació corre sense root, sense *capabilities* i amb el sistema de fitxers de només lectura; les CLI oficials s'inclouen com a binaris natius fixats.

## Alternatives considerades

- **React / Vue:** més pesats per a aquesta mida; Svelte compila a poc JavaScript.
- **Nginx + Certbot:** més peces a mantenir que Caddy.
- **Accés només per VPN (Tailscale/WireGuard):** més segur però menys còmode; queda documentat com a reforç opcional juntament amb la llista d'IP permeses.

## Conseqüències

- La imatge pesa uns 800 MB pels binaris de les CLI.
- Sense Node en temps d'execució: només a l'etapa de construcció.
