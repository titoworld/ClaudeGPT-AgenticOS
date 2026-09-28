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
- Tot corre en contenidors Docker sense root (l'aplicació amb l'usuari 10001 i Caddy amb el 10002), amb el sistema de fitxers de només lectura. L'única excepció és `caddy-init`, que a cada arrencada dona els volums de Caddy al seu usuari: corre uns segons com a root, sense xarxa, i s'atura.
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
>
> Si l'SSH respon «Too many authentication failures», el teu agent d'SSH ofereix massa claus abans de la bona: indica-li quina ha de fer servir, `ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 usuari@IP-DEL-VPS`.

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

Els dos serveis (`app` i `caddy`) han d'estar `running` i `healthy`. (`caddy-init` no hi surt: ja ha acabat la seva feina.) Per veure com Caddy obté el certificat:

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
- Per tancar totes les sessions i oblidar els dispositius coneguts (per exemple, si has perdut un dispositiu): `docker compose exec app agentic-os reset-sessions`

## Actualitzar

Un cop al mes, **encara que `git pull` no porti res de nou**:

```bash
cd /opt/claudegpt
git pull
docker compose pull caddy
docker compose build --pull
docker compose up -d
docker compose restart caddy
docker image prune -f
```

- `--pull` baixa les imatges base més recents (Debian, Python, Node) amb els pegats de seguretat del mes; `docker compose pull caddy` fa el mateix amb la de Caddy. Sense això, Docker reaprofita les còpies antigues que ja té.
- `docker compose restart caddy` aplica els canvis de `deploy/Caddyfile` que porti `git pull`: Docker no els veu sol i Caddy continuaria amb la configuració antiga.
- Fes-ho quan no hi hagi cap resposta en curs: l'aplicació es reinicia (uns segons) i els torns que s'estiguin generant es tallen. La interfície es reconnecta sola.
- Les versions de les CLI i de Caddy estan fixades al projecte i s'actualitzen amb `git pull`. L'aplicació parla amb cada CLI d'una manera molt concreta, sobretot amb Codex, i una versió nova pot trencar-ho.
- Si vols provar una altra versió abans que s'actualitzi el projecte, defineix `CLAUDE_CLI_VERSION` o `CODEX_CLI_VERSION` a `.env` i torna a executar `docker compose up -d --build`. Comprova-ho amb `agentic-os doctor`. Per tornar enrere, esborra la línia i reconstrueix.

**El servidor.** Les actualitzacions automàtiques només inclouen les de seguretat de Debian/Ubuntu. Docker (i amb ell `containerd` i `runc`, que aïllen els contenidors) ve del repositori de Docker i no s'actualitza sol, a propòsit: una versió nova de Docker val més instal·lar-la quan hi ets. Aprofita l'actualització mensual:

```bash
apt-get update && apt-get upgrade
docker compose ps
```

Els contenidors continuen en marxa o tornen a arrencar sols; `docker compose ps` ho confirma. Si existeix el fitxer `/var/run/reboot-required`, reinicia el servidor (`reboot`) o espera el reinici automàtic de les 04:30.

**Un sol cop, en actualitzar una instal·lació anterior a aquests canvis** (setembre del 2026; si no calia, no fa cap mal):

- Esborra el registre antic de Codex, que guardava el text sencer de cada crida a ChatGPT (ara aquests fitxers viuen en memòria, a `/run/codex-state`, i s'esborren cada vegada que l'aplicació engega Codex): `docker compose exec app sh -c 'rm -f /home/app/.codex/logs_2.sqlite*'`. Les còpies de seguretat antigues també el contenen: esborra-les o guarda-les xifrades.
- Si tens còpies a `/opt/claudegpt/backups`, descarrega-les i esborra-les del servidor (`rm -rf /opt/claudegpt/backups`): ara es desen fora del repositori (vegeu [Còpies de seguretat](#còpies-de-seguretat)).
- Torna a executar `bash deploy/harden.sh`: treu el límit de 3 intents de l'SSH, que podia deixar fora qui té diverses claus a l'agent.
- Caddy ja no corre com a root: el servei `caddy-init` passa els seus volums al nou usuari tot sol.
- Si has fet alguna còpia amb les ordres antigues, comprova-la: si `tar` fallava, en quedava un fitxer incomplet que semblava bo. Al teu ordinador, `age -d -i claudegpt-backup.key claudegpt-AAAA-MM-DD.tar.gz.age | tar tzf - > /dev/null && echo Correcta` (sense xifrar, `tar tzf claudegpt-AAAA-MM-DD.tar.gz > /dev/null && echo Correcta`).

## Còpies de seguretat

| Volum | Què conté | Si el perds... |
| --- | --- | --- |
| `claudegpt_app_data` | Base de dades SQLite: propietari (hash de la contrasenya i secret TOTP), sessions, converses, estadístiques i preferències | Perds les converses i cal tornar a fer `agentic-os init` |
| `claudegpt_app_home` | Inicis de sessió de Claude (`~/.claude`) i de Codex (`~/.codex`). Els registres de Codex, que contenen els prompts, no hi són: viuen en memòria | Cal tornar a iniciar sessió a les CLI |
| `claudegpt_caddy_data` | Certificats i compte de Let's Encrypt | Caddy els torna a demanar sol |
| `claudegpt_caddy_config` | Configuració interna de Caddy | Res; es regenera |

A més, **desa el fitxer `.env`**: té el token de Claude i les claus d'API.

> Les còpies contenen secrets (el secret TOTP, els tokens de les subscripcions i les claus d'API). Es desen a `/var/backups/claudegpt`, un directori amb permisos 700 (només el teu usuari i root) que és **fora del repositori** (així un `git add` no les pot pujar mai). Xifra-les, descarrega-les i esborra-les del servidor.

**Xifratge (recomanat, un sol cop).** Al teu ordinador, instal·la [age](https://github.com/FiloSottile/age) i crea una clau: `age-keygen -o claudegpt-backup.key`. Mostra la clau pública (`age1...`): és la que faràs servir al servidor. La clau privada (el fitxer) no surt mai del teu ordinador; guarda'n una còpia en un lloc segur, perquè sense ella no podràs restaurar. Al servidor: `apt-get install -y age`.

**Fer una còpia** al servidor, com a root (`sudo -i`, com al pas 2). L'aplicació s'atura uns segons perquè la base de dades quedi coherent:

```bash
cd /opt/claudegpt
AGE_KEY=age1...        # la teva clau pública
DEST="/var/backups/claudegpt/claudegpt-$(date +%F).tar.gz.age"
umask 077
set -o pipefail        # si falla qualsevol pas de la còpia, falla tota
install -d -m 700 /var/backups/claudegpt
docker compose stop app
if docker run --rm -v claudegpt_app_data:/data:ro -v claudegpt_app_home:/home/app:ro \
     claudegpt-os:latest tar czf - -C / data home/app \
   | age -r "$AGE_KEY" > "$DEST"
then echo "Còpia feta: $DEST"
else rm -f "$DEST"; echo "ERROR: la còpia ha fallat i s'ha esborrat; mira el missatge de sobre." >&2
fi
docker compose start app
age -r "$AGE_KEY" -o "/var/backups/claudegpt/env-$(date +%F).age" .env
chown -R "${SUDO_USER:-root}" /var/backups/claudegpt   # perquè la puguis descarregar
```

- Si `tar` o `age` fallen (per exemple, perquè Docker no troba la imatge o la clau està mal copiada), no queda cap fitxer a mitges que sembli una còpia bona: surt `ERROR` i l'aplicació torna a arrencar igualment.
- L'última línia dona els fitxers a l'usuari amb què has entrat al VPS; el directori continua sent privat.
- Sense xifrar (només si no pots instal·lar age): treu `| age -r "$AGE_KEY"` i l'extensió `.age` de `DEST`, i canvia la penúltima línia per `cp .env "/var/backups/claudegpt/env-$(date +%F)"`.

Després, descarrega-la al teu ordinador (des de l'ordinador, no des del VPS) i, quan hagis comprovat que la tens, esborra-la del servidor. `usuari` és el mateix usuari amb què entres al VPS al pas 2 (`root` si hi entres directament com a root):

```bash
scp -r usuari@IP-DEL-VPS:/var/backups/claudegpt ./claudegpt-backups
ssh usuari@IP-DEL-VPS 'rm -f /var/backups/claudegpt/*'
```

**Restaurar** (al mateix servidor o a un de nou amb els passos 1–4 fets). Substitueix **tot** el contingut actual dels dos volums pel de la còpia.

1. Al teu ordinador, si la còpia està xifrada, desxifra-la: `age -d -i claudegpt-backup.key -o claudegpt-AAAA-MM-DD.tar.gz claudegpt-AAAA-MM-DD.tar.gz.age` (i igual amb `env-AAAA-MM-DD.age`). Si `age` dona un error, la còpia està incompleta o no és teva: no la facis servir.
2. Puja-la al teu directori del servidor: `scp claudegpt-AAAA-MM-DD.tar.gz env-AAAA-MM-DD usuari@IP-DEL-VPS:`
3. Al servidor, com a root (`sudo -i`). Primer es llegeix tota la còpia: si està malmesa (per exemple, una pujada interrompuda), no s'esborra res.

```bash
cd /opt/claudegpt
B=/home/usuari         # on l'has pujada (/root si entres com a root)
if tar tzf "$B/claudegpt-AAAA-MM-DD.tar.gz" > /dev/null; then
  docker compose stop app
  docker run --rm -i -v claudegpt_app_data:/data -v claudegpt_app_home:/home/app \
    claudegpt-os:latest sh -c 'find /data /home/app -mindepth 1 -delete && tar xzf - -C /' \
    < "$B/claudegpt-AAAA-MM-DD.tar.gz"
  docker compose start app
  install -m 600 "$B/env-AAAA-MM-DD" .env
  docker compose up -d
  rm -f "$B/claudegpt-AAAA-MM-DD.tar.gz" "$B/env-AAAA-MM-DD"
else
  echo "ERROR: la còpia està malmesa; no s'ha tocat res." >&2
fi
```

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
- Si `docker compose up` diu que `caddy-init` ha fallat, o Caddy es queixa de `permission denied` a `/data` o `/config`, mira `docker compose logs caddy-init` i torna a fer `docker compose up -d`.

**`docker compose up` diu «Range of CPUs is from 0.01 to 1.00»**
- El servidor té un sol vCPU i l'aplicació en demana 1,5. Afegeix `APP_CPUS=1` a `.env` i torna a fer `docker compose up -d`.

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
- «Massa intents»: el bloqueig creix amb cada error. Un navegador on ja havies entrat (en els últims 12 mesos i sense esborrar-ne les cookies) només es bloqueja pels seus propis errors: encara que algú provi contrasenyes des d'Internet, hi continues podent entrar. Des d'un navegador o dispositiu nou, espera el temps que indica o aixeca tots els bloquejos des del servidor: `docker compose exec app agentic-os reset-throttle`.
- Si «Massa intents» torna a sortir sense que t'hagis equivocat, algú està provant contrasenyes contra la teva web. La contrasenya i el codi TOTP continuen protegint-te i els navegadors on ja havies entrat no es bloquegen. Un dispositiu nou, en canvi, es tornarà a bloquejar mentre duri l'atac, encara que facis `reset-throttle` (l'atacant ho torna a activar amb pocs intents): per entrar-hi, limita l'accés a les teves IP amb `ALLOWED_IPS` a `.env` (i `docker compose up -d`) o fes servir una VPN. Això també talla l'atac de soca-rel.
- Contrasenya oblidada o mòbil perdut: `docker compose exec -it app agentic-os init`.

**Claude diu que la sessió ha caducat o no està connectat**
- Comprova-ho: `docker compose exec app claude auth status`
- Amb token: genera'n un de nou (`claude setup-token`, pas 6), substitueix-lo a `.env` i fes `docker compose up -d`.
- Amb inici de sessió desat: `docker compose exec -it app claude auth login`.

**ChatGPT diu que no està connectat**
- `docker compose exec app codex login status`; si cal, torna a fer el pas 7 i `docker compose restart app`.
- Els registres de Codex viuen en un espai en memòria de 64 MB (`/run/codex-state`) que l'aplicació buida cada vegada que engega Codex, de manera que mai no li impedeixen tornar a arrencar. Si, després de moltes crides sense reiniciar l'aplicació, ChatGPT comença a fallar i als registres surten errors de SQLite o d'espai ple, `docker compose restart app` el buida del tot.

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
- El cos de les peticions té un màxim d'1 MiB. Caddy el passa a l'aplicació a mesura que arriba, sense acumular-lo en memòria, i l'aplicació respon 408 i tanca la connexió si no ha arribat sencer en 15 segons (Caddy talla als 30): una pujada lenta o que es queda a mitges no pot ocupar cap connexió gaire estona. Els WebSockets no tenen aquest límit.
- Docker i ufw: els ports que publica Docker no passen per les regles d'ufw. Aquí només es publiquen el 80 i el 443, que han de ser públics. No afegeixis `ports:` a l'aplicació; per depurar, fes servir `127.0.0.1:PORT:PORT` i un túnel SSH.

**Inici de sessió i sessions**
- Contrasenya (argon2id) **i** codi TOTP, que no es pot reutilitzar. Bloqueig exponencial després d'intents fallits, que es manté encara que reiniciïs (`agentic-os reset-throttle` l'aixeca).
- Cada navegador on has entrat rep una segona cookie, de dispositiu conegut (un any; no s'esborra en tancar la sessió). Un dispositiu conegut només es bloqueja pels seus propis errors: els intents d'altres des d'Internet no et poden deixar fora d'un navegador que ja fas servir. `agentic-os reset-sessions` i `agentic-os init` obliden tots els dispositius. Durant un atac sostingut, un dispositiu nou només pot entrar si limites l'accés amb `ALLOWED_IPS` o una VPN (vegeu [Resolució de problemes](#resolució-de-problemes)).
- Sessions desades al servidor (només se'n guarda el hash) amb una cookie `__Host-` HttpOnly, Secure i SameSite=Strict. Caduquen després de 72 hores sense activitat i als 30 dies (`AOS_SESSION_IDLE_HOURS`, `AOS_SESSION_MAX_DAYS`).

**Secrets**
- `.env` (permisos 600) i el volum `app_home` contenen credencials que donen accés a les teves subscripcions. Qui sigui root al VPS les pot fer servir: no comparteixis l'accés al servidor.
- Les CLI s'executen en un directori buit i amb una llista tancada de variables d'entorn, sense accés a cap *shell* ni als secrets de l'aplicació. La de Claude no té cap eina. Codex 0.157.1 encara ofereix a ChatGPT una eina que executa codi en un procés fill de Codex (`codex-code-mode`, un entorn aïllat V8 sense accés als fitxers ni a la xarxa, que s'atura amb Codex) i eines per obrir subagents. L'aplicació només deixa córrer un subagent alhora (`agents.max_threads=1`), interromp de seguida qualsevol feina que no pertanyi a una crida en curs, atura la crida si ChatGPT hi fa servir subagents més de 3 vegades i, quan ja no hi ha cap crida en curs, reinicia el procés de Codex que n'hagi obert algun (els subagents aturats no alliberen la memòria). Com a protecció addicional, l'aplicació té un límit de CPU (`APP_CPUS`).
- Codex desa el text de cada crida en els seus registres: viuen en memòria (`/run/codex-state`), fora dels volums i de les còpies de seguretat, i s'esborren cada vegada que l'aplicació engega Codex (i en reiniciar-la).

**Reforços opcionals**
- `ALLOWED_IPS` a `.env`: només aquestes IP podran obrir la web.
- Una VPN (Tailscale o WireGuard) en lloc d'exposar la web a Internet: més segur, però menys còmode.

**Condicions d'ús de les subscripcions**

Fer servir les subscripcions a través de les CLI oficials és una zona que cada proveïdor regula a la seva manera. Llegeix-ne les condicions i decideix tu:

- **Anthropic.** L'article del Help Center «Use the Claude Agent SDK with your Claude plan» (juny del 2026) inclou l'ordre `claude -p` en projectes propis entre els usos que consumeixen els límits del teu pla. Les condicions d'ús prohibeixen compartir les credencials i l'accés automatitzat que no estigui permès explícitament. L'aplicació fa servir la CLI oficial sense modificar i mai extreu el token per cridar l'API directament.
- **OpenAI.** Recomana les claus d'API per a l'automatització. Fer servir la subscripció de ChatGPT a través de Codex en una aplicació com aquesta no està autoritzat explícitament: és sota la teva responsabilitat.
- **Recomanació:** un sol usuari (tu), ús interactiu i moderat, sense compartir l'accés amb ningú. Si tens dubtes o en fas un ús intensiu, fes servir el mode `api`.
