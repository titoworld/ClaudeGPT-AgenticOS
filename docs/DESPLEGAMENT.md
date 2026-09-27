# Desplegament en un VPS

Aquesta guia et porta, pas a pas, des d'un servidor buit fins a tenir ClaudeGPT OS funcionant a `https://el-teu-domini`, amb certificat, contrasenya i codi TOTP. Calcula uns 45 minuts la primera vegada.

No cal ser expert: copia les ordres tal com són i canvia només el que està marcat (el domini, el correu...).

## Què tindràs al final

```
Internet ──► Caddy (80/443: HTTPS automàtic, HTTP/3)
                │  xarxa interna de Docker (no surt a Internet)
                ▼
             Aplicació (FastAPI + interfície web)
                ├── CLI de Claude Code  ─► la teva subscripció de Claude
                └── CLI de Codex        ─► la teva subscripció de ChatGPT
```

- Només Caddy és accessible des de fora. L'aplicació no té cap port obert.
- Tot corre en contenidors Docker sense root, amb el sistema de fitxers de només lectura.
- Les dades viuen en quatre volums de Docker (vegeu [Còpies de seguretat](#còpies-de-seguretat)).

## Què necessites

- **Un VPS** amb 2 vCPU i 2–4 GB de RAM, 25 GB de disc, **Debian 12/13 o Ubuntu 24.04** (amd64 o arm64). Amb 2 GB de RAM l'script de preparació hi afegeix 2 GB de memòria d'intercanvi (*swap*).
- **Un domini o subdomini** on puguis crear registres DNS, per exemple `ia.example.com`.
- **Una clau SSH** al teu ordinador (si no en tens: `ssh-keygen -t ed25519`).
- **Les subscripcions** que vulguis fer servir: Claude Pro/Max i ChatGPT Plus/Pro. També pots fer servir claus d'API o combinar-ho.
- **Una aplicació d'autenticació** al mòbil: Aegis, Google Authenticator, 1Password, Bitwarden...

## 1. DNS

Al panell del teu proveïdor de domini, crea:

| Tipus | Nom | Valor |
| --- | --- | --- |
| `A` | `ia` (o el subdomini que vulguis) | IPv4 del VPS |
| `AAAA` | `ia` | IPv6 del VPS (només si en té) |

Comprova-ho des del teu ordinador (pot trigar uns minuts):

```bash
nslookup ia.example.com
```

Ha de respondre amb la IP del VPS **abans** d'arrencar l'aplicació: si no, el certificat no es pot obtenir i, després de diversos intents fallits, Let's Encrypt et fa esperar.

> Si fas servir Cloudflare, deixa el registre en mode «DNS only» (núvol gris). Amb el proxy de Cloudflare activat, Caddy no veu la IP real dels visitants.

Si el teu proveïdor de VPS té un tallafoc propi al panell (Hetzner, OVH, AWS...), obre-hi també **22/tcp, 80/tcp, 443/tcp i 443/udp**.

## 2. Preparar el servidor

Entra al VPS i fes-te root:

```bash
ssh usuari@IP-DEL-VPS
sudo -i
```

A partir d'aquí, **totes les ordres s'executen com a root**.

Instal·la git i descarrega el projecte a `/opt/claudegpt`:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/titoworld/ClaudeGPT-AgenticOS.git /opt/claudegpt
cd /opt/claudegpt
```

> Si el repositori és privat, GitHub et demanarà credencials: fes servir un *token* d'accés amb permís només de lectura, o una *deploy key*.

Executa l'script de preparació:

```bash
bash deploy/harden.sh
```

Què fa (el pots tornar a executar sense por, sempre deixa el mateix resultat):

1. Actualitza el sistema i activa les **actualitzacions de seguretat automàtiques** (si una actualització ho requereix, el servidor es reinicia a les 04:30 i l'aplicació torna a arrencar sola).
2. Activa el **tallafoc** (ufw): només SSH (amb límit d'intents), 80/tcp, 443/tcp i 443/udp.
3. **SSH només amb clau**: desactiva les contrasenyes. Abans comprova que el teu usuari té una clau autoritzada i et demana confirmació.
4. Crea memòria d'intercanvi si el servidor té poca RAM.
5. Instal·la **Docker** des del repositori oficial de Docker.

Opcions: `WITH_FAIL2BAN=1 bash deploy/harden.sh` afegeix fail2ban (bloqueja una hora les IP que fallen l'SSH 5 vegades).

> **No et quedis fora.** Quan l'script et pregunti per l'SSH, tingues **una altra sessió SSH oberta**. En acabar, obre un terminal nou i comprova que encara pots entrar amb `ssh usuari@IP-DEL-VPS`. Si no pots, la sessió que tens oberta et permet desfer-ho: `rm /etc/ssh/sshd_config.d/01-claudegpt.conf && systemctl restart ssh`.

## 3. Configurar

```bash
cd /opt/claudegpt
cp .env.example .env
chmod 600 .env
nano .env
```

Omple com a mínim:

- `DOMAIN`: el teu domini, sense `https://` (per exemple `ia.example.com`).
- `ACME_EMAIL`: el teu correu (Let's Encrypt t'avisa si un certificat no es pot renovar).

La resta ja té valors correctes. Desa amb `Ctrl+O`, `Enter` i surt amb `Ctrl+X`.

## 4. Arrencar

```bash
docker compose up -d --build
```

La primera vegada triga uns 5–10 minuts: descarrega unes imatges base, compila la interfície i inclou les CLI oficials (Claude Code 2.1.283 i Codex 0.157.1). La imatge final ocupa uns 900 MB.

Comprova que tot està en marxa:

```bash
docker compose ps
```

Els dos serveis (`app` i `caddy`) han d'estar `running` i `healthy`. Per veure com Caddy obté el certificat:

```bash
docker compose logs -f caddy
```

Quan vegis `certificate obtained successfully`, surt amb `Ctrl+C`.

## 5. Crear el propietari (contrasenya i TOTP)

```bash
docker compose exec -it app agentic-os init
```

1. Tria una contrasenya d'almenys 12 caràcters (millor una frase: «tres-gats-blaus-sota-la-pluja»).
2. Escaneja el codi QR amb l'aplicació d'autenticació. Si el terminal és massa petit o no l'escaneja, fes-lo més gran o escriu a l'aplicació la clau que es mostra sota el QR.
3. Escriu el codi de 6 xifres que et mostra l'aplicació per confirmar-ho.

Si mai perds el mòbil o oblides la contrasenya, torna a executar aquesta ordre des del servidor: substitueix el propietari i tanca totes les sessions obertes.

## 6. Connectar Claude (subscripció Pro/Max)

**Opció recomanada: token d'un any**

```bash
docker compose exec -it app claude setup-token
```

1. Copia l'enllaç que mostra i obre'l al navegador del teu ordinador.
2. Inicia la sessió amb el teu compte de Claude i autoritza l'accés.
3. Enganxa al terminal el codi que et dona la web.
4. L'ordre mostra un token que comença per `sk-ant-oat01-`. Obre `.env` (`nano .env`), treu el `#` de la línia `CLAUDE_CODE_OAUTH_TOKEN=` i enganxa-hi el token.
5. Aplica el canvi: `docker compose up -d`

El token dura un any i només serveix per fer peticions al model. Apunta't al calendari quan caduca.

**Alternativa: inici de sessió desat al servidor**

```bash
docker compose exec -it app claude auth login
```

Segueix els mateixos passos (enllaç, autoritzar, enganxar el codi). La sessió es desa al volum `app_home` i es renova sola; no cal tocar `.env`.

## 7. Connectar ChatGPT (subscripció Plus/Pro)

```bash
docker compose exec -it app codex login --device-auth
```

1. Obre l'enllaç que mostra (des del mòbil o l'ordinador) i inicia la sessió amb el teu compte de ChatGPT.
2. Escriu el codi que apareix al terminal.
3. Comprova-ho: `docker compose exec app codex login status`
4. Reinicia l'aplicació perquè el procés de Codex agafi la sessió nova: `docker compose restart app`

Si et diu que l'inici de sessió amb codi de dispositiu no està permès, busca l'opció a la configuració de seguretat del teu compte de ChatGPT (en comptes d'empresa o d'equip, l'ha d'activar l'administrador).

## 8. Alternativa: claus d'API

Si prefereixes pagar per ús (o combinar-ho: Claude amb subscripció i ChatGPT amb clau, per exemple), edita `.env`:

```bash
AOS_CLAUDE_MODE=api
ANTHROPIC_API_KEY=sk-ant-api03-...
AOS_CHATGPT_MODE=api
OPENAI_API_KEY=sk-proj-...
```

I aplica-ho amb `docker compose up -d`.

> **Important:** si Claude és en mode `cli`, deixa `ANTHROPIC_API_KEY` comentada. La CLI de Claude dona prioritat a aquesta clau per sobre de la subscripció i ho facturaria tot per API.

Per provar la interfície sense gastar res hi ha el mode `fake` (`AOS_CLAUDE_MODE=fake`, `AOS_CHATGPT_MODE=fake`).

## 9. Comprovar-ho tot

```bash
docker compose exec app agentic-os doctor
```

Revisa la configuració, la base de dades, el propietari, la interfície, les CLI i l'estat de cada proveïdor (incloent-hi els límits d'ús de la subscripció). Cada línia comença per `[ OK ]`, `[AVÍS]` o `[ERROR]`; al final diu si hi ha algun problema crític.

## 10. Primer inici de sessió

Obre `https://el-teu-domini` al navegador, escriu la contrasenya i el codi TOTP. Ja està!

- La sessió dura fins a 72 hores sense activitat i 30 dies com a màxim.
- Per tancar totes les sessions (per exemple, si has perdut un dispositiu): `docker compose exec app agentic-os reset-sessions`

## Actualitzar

```bash
cd /opt/claudegpt
git pull
docker compose up -d --build
docker image prune -f
```

- Fes-ho quan no hi hagi cap resposta en curs: l'aplicació es reinicia (uns segons) i els torns que s'estiguin generant es tallen. La interfície es reconnecta sola.
- Les versions de les CLI i de Caddy estan fixades al projecte i s'actualitzen amb `git pull`. L'aplicació parla amb cada CLI d'una manera molt concreta, sobretot amb Codex, i una versió nova pot trencar-ho.
- Si vols provar una altra versió abans que s'actualitzi el projecte, defineix `CLAUDE_CLI_VERSION` o `CODEX_CLI_VERSION` a `.env` i torna a executar `docker compose up -d --build`. Comprova-ho amb `agentic-os doctor`. Per tornar enrere, esborra la línia i reconstrueix.

## Còpies de seguretat

| Volum | Què conté | Si el perds... |
| --- | --- | --- |
| `claudegpt_app_data` | Base de dades SQLite: propietari (hash de la contrasenya i secret TOTP), sessions, converses, estadístiques i preferències | Perds les converses i cal tornar a fer `agentic-os init` |
| `claudegpt_app_home` | Inicis de sessió de Claude (`~/.claude`) i de Codex (`~/.codex`) | Cal tornar a iniciar sessió a les CLI |
| `claudegpt_caddy_data` | Certificats i compte de Let's Encrypt | Caddy els torna a demanar sol |
| `claudegpt_caddy_config` | Configuració interna de Caddy | Res; es regenera |

A més, **desa el fitxer `.env`**: té el token de Claude i les claus d'API.

> Les còpies contenen secrets (el secret TOTP i els tokens de les subscripcions): guarda-les xifrades i fora del servidor.

**Fer una còpia** (l'aplicació s'atura uns segons perquè la base de dades quedi coherent):

```bash
cd /opt/claudegpt
install -d -m 700 -o 10001 -g 10001 backups
docker compose stop app
docker run --rm -v claudegpt_app_data:/data:ro -v claudegpt_app_home:/home/app:ro \
  -v "$PWD/backups:/backup" claudegpt-os:latest \
  tar czf "/backup/claudegpt-$(date +%F).tar.gz" -C / data home/app
docker compose start app
cp .env "backups/env-$(date +%F)"
```

Després, descarrega-la al teu ordinador (des de l'ordinador, no des del VPS):

```bash
scp -r usuari@IP-DEL-VPS:/opt/claudegpt/backups ./claudegpt-backups
```

> Si has entrat amb un usuari que no és root, primer dona-li permís de lectura al VPS: `sudo chown -R usuari /opt/claudegpt/backups`.

**Restaurar** (al mateix servidor o a un de nou amb els passos 1–4 fets). Substitueix **tot** el contingut actual dels dos volums pel de la còpia:

```bash
cd /opt/claudegpt
docker compose stop app
docker run --rm -v claudegpt_app_data:/data -v claudegpt_app_home:/home/app \
  -v "$PWD/backups:/backup:ro" claudegpt-os:latest \
  sh -c 'find /data /home/app -mindepth 1 -delete && tar xzf /backup/claudegpt-AAAA-MM-DD.tar.gz -C /'
docker compose start app
```

Recupera també el `.env` (`cp backups/env-AAAA-MM-DD .env && chmod 600 .env`) i aplica'l amb `docker compose up -d`.

## Resolució de problemes

Primer de tot, sempre:

```bash
docker compose ps
docker compose exec app agentic-os doctor
docker compose logs --tail 100 app
docker compose logs --tail 100 caddy
```

**El navegador diu que el certificat no és vàlid o que la connexió s'ha tancat**
- Obre sempre la web pel domini, mai per la IP: per seguretat, Caddy tanca qualsevol connexió que no sigui per al teu domini.
- Comprova el DNS (`nslookup el-teu-domini`) i que els ports 80 i 443 estiguin oberts també al tallafoc del proveïdor.
- Mira els errors de Caddy: `docker compose logs caddy | grep -i error`. Si hi surt `rateLimited`, Let's Encrypt et fa esperar: corregeix la causa i espera una hora.

**Error 502 (Bad Gateway)**
- L'aplicació no respon. Mira `docker compose logs --tail 100 app`.
- Si diu que «La configuració (variables AOS_*) no és vàlida», revisa `.env` i aplica-ho amb `docker compose up -d`.
- Si s'ha quedat sense memòria (`docker compose ps` la mostra reiniciant-se; `dmesg | grep -i oom`), puja `mem_limit` al `docker-compose.yml` o el VPS a 4 GB.
- Durant una actualització és normal durant uns segons.

**La interfície diu que s'està reconnectant i no connecta (WebSocket)**
- `AOS_PUBLIC_ORIGIN` ha de coincidir exactament amb l'adreça del navegador: `https://`, sense barra final i amb el mateix nom (amb o sense `www`). Si no coincideix, el servidor rebutja la connexió per seguretat.
- Alguns antivirus o proxies d'empresa bloquegen els WebSockets: prova des d'una altra xarxa.

**No puc iniciar sessió**
- «Codi incorrecte»: l'hora del mòbil o del servidor no és correcta. Al servidor: `timedatectl` (ha de dir `System clock synchronized: yes`).
- «Massa intents»: espera el temps que indica; el bloqueig creix amb cada error.
- Contrasenya oblidada o mòbil perdut: `docker compose exec -it app agentic-os init`.

**Claude diu que la sessió ha caducat o no està connectat**
- Comprova-ho: `docker compose exec app claude auth status`
- Amb token: genera'n un de nou (`claude setup-token`, pas 6), substitueix-lo a `.env` i fes `docker compose up -d`.
- Amb inici de sessió desat: `docker compose exec -it app claude auth login`.

**ChatGPT diu que no està connectat**
- `docker compose exec app codex login status`; si cal, torna a fer el pas 7 i `docker compose restart app`.

**Límits d'ús de la subscripció**
- Les subscripcions tenen finestres d'ús (per exemple, de 5 hores i de 7 dies). El tauler i `agentic-os doctor` mostren el percentatge fet servir i quan es renova.
- Quan s'arriba al límit, aquell model falla fins que es renova. Mentrestant, fes servir l'altre model en mode Solo o passa temporalment al mode `api`.
- El mode Consell fa diverses crides per pregunta: fes-lo servir quan valgui la pena.

**Disc ple**
- `docker system df` per veure què ocupa; `docker image prune -f` i `docker builder prune -f` alliberen imatges i memòria cau de construccions antigues.

## Seguretat

**Què queda exposat a Internet**
- Només Caddy (80 i 443) i l'SSH. L'aplicació no té cap port publicat i viu en una xarxa interna.
- Caddy fa HTTPS amb HSTS, redirigeix HTTP a HTTPS i tanca les connexions que no són per al teu domini. Els registres d'accés no guarden cookies.
- Docker i ufw: els ports que publica Docker no passen per les regles d'ufw. Aquí només es publiquen el 80 i el 443, que han de ser públics. No afegeixis `ports:` a l'aplicació; per depurar, fes servir `127.0.0.1:PORT:PORT` i un túnel SSH.

**Inici de sessió i sessions**
- Contrasenya (argon2id) **i** codi TOTP, que no es pot reutilitzar. Bloqueig exponencial després d'intents fallits, que es manté encara que reiniciïs.
- Sessions desades al servidor (només se'n guarda el hash) amb una cookie `__Host-` HttpOnly, Secure i SameSite=Strict. Caduquen després de 72 hores sense activitat i als 30 dies (`AOS_SESSION_IDLE_HOURS`, `AOS_SESSION_MAX_DAYS`).

**Secrets**
- `.env` (permisos 600) i el volum `app_home` contenen credencials que donen accés a les teves subscripcions. Qui sigui root al VPS les pot fer servir: no comparteixis l'accés al servidor.
- Les CLI s'executen sense eines, en un directori buit i amb una llista tancada de variables d'entorn: no poden executar ordres ni llegir els secrets de l'aplicació.

**Reforços opcionals**
- `ALLOWED_IPS` a `.env`: només aquestes IP podran obrir la web.
- Una VPN (Tailscale o WireGuard) en lloc d'exposar la web a Internet: més segur, però menys còmode.

**Condicions d'ús de les subscripcions**

Fer servir les subscripcions a través de les CLI oficials és una zona que cada proveïdor regula a la seva manera. Llegeix-ne les condicions i decideix tu:

- **Anthropic.** L'article del Help Center «Use the Claude Agent SDK with your Claude plan» (juny del 2026) inclou l'ordre `claude -p` en projectes propis entre els usos que consumeixen els límits del teu pla. Les condicions d'ús prohibeixen compartir les credencials i l'accés automatitzat que no estigui permès explícitament. L'aplicació fa servir la CLI oficial sense modificar i mai extreu el token per cridar l'API directament.
- **OpenAI.** Recomana les claus d'API per a l'automatització. Fer servir la subscripció de ChatGPT a través de Codex en una aplicació com aquesta no està autoritzat explícitament: és sota la teva responsabilitat.
- **Recomanació:** un sol usuari (tu), ús interactiu i moderat, sense compartir l'accés amb ningú. Si tens dubtes o en fas un ús intensiu, fes servir el mode `api`.
