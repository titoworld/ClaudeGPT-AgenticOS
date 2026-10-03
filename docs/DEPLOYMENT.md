# Deploying on a VPS

This guide takes you step by step from an empty server to ClaudeGPT OS running at `https://your-domain`, with a certificate, a password and a TOTP code. Allow about 45 minutes the first time.

You don't need to be an expert: copy the commands exactly as they are and change only what is marked (the domain, the email...).

## What you will have

```
Internet ──► Caddy (80/443: automatic HTTPS, HTTP/3)
                │  Docker internal network (not reachable from the Internet)
                ▼
             App (FastAPI + web interface)
                ├── Claude Code CLI  ─► your Claude subscription
                └── Codex CLI        ─► your ChatGPT subscription
```

- Only Caddy can be reached from outside. The app has no open port.
- Everything runs in Docker containers without root (the app as user 10001 and Caddy as user 10002), with a read-only file system. The only exception is `caddy-init`, which at every start hands Caddy's volumes over to Caddy's user: it runs as root for a few seconds, without a network, and stops.
- The data lives in four Docker volumes (see [Backups](#backups)).

## What you need

- **A VPS** with 2 vCPUs and 2–4 GB of RAM, 25 GB of disk, **Debian 12/13 or Ubuntu 24.04** (amd64 or arm64). With 2 GB of RAM, the preparation script adds 2 GB of swap.
- **A domain or subdomain** where you can create DNS records, for example `ia.example.com`.
- **An SSH key** on your computer (if you don't have one: `ssh-keygen -t ed25519`).
- **The subscriptions** you want to use: Claude Pro/Max and ChatGPT Plus/Pro. You can also use API keys, or mix the two.
- **An authenticator app** on your phone: Aegis, Google Authenticator, 1Password, Bitwarden...

## 1. DNS

In your domain provider's control panel, create:

| Type | Name | Value |
| --- | --- | --- |
| `A` | `ia` (or the subdomain you want) | The VPS's IPv4 address |
| `AAAA` | `ia` | The VPS's IPv6 address (only if it has one) |

Check it from your computer (it can take a few minutes):

```bash
nslookup ia.example.com
```

It must answer with the VPS's IP **before** you start the app: otherwise the certificate cannot be obtained and, after several failed attempts, Let's Encrypt makes you wait.

> If you use Cloudflare, leave the record as "DNS only" (grey cloud). With Cloudflare's proxy on, Caddy does not see the visitors' real IP.

If your VPS provider has its own firewall in its control panel (Hetzner, OVH, AWS...), open **22/tcp, 80/tcp, 443/tcp and 443/udp** there too.

## 2. Prepare the server

Log in to the VPS and become root:

```bash
ssh user@VPS-IP
sudo -i
```

From here on, **every command runs as root**.

Install git and download the project to `/opt/claudegpt`:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/titoworld/ClaudeGPT-AgenticOS.git /opt/claudegpt
cd /opt/claudegpt
```

> If the repository is private, GitHub will ask you for credentials: use an access *token* with read-only permission, or a *deploy key*.

Run the preparation script:

```bash
bash deploy/harden.sh
```

What it does (you can safely run it again: it always leaves the same result):

1. Updates the system and turns on **automatic security updates** (if an update needs it, the server reboots at 04:30 and the app starts again by itself).
2. Turns on the **firewall** (ufw): only SSH (rate limited), 80/tcp, 443/tcp and 443/udp.
3. **SSH with keys only**: turns off passwords. Before that, it checks that your user has an authorized key and asks you to confirm.
4. Creates swap if the server has little RAM.
5. Installs **Docker** from Docker's official repository.

Option: `WITH_FAIL2BAN=1 bash deploy/harden.sh` adds fail2ban (it bans for an hour the IPs that fail to log in over SSH 5 times).

> **Don't lock yourself out.** When the script asks you about SSH, keep **another SSH session open**. When it finishes, open a new terminal and check that you can still log in with `ssh user@VPS-IP`. If you can't, the session you have open lets you undo it: `rm /etc/ssh/sshd_config.d/01-claudegpt.conf && systemctl restart ssh`.
>
> If SSH answers "Too many authentication failures", your SSH agent offers too many keys before the right one: tell it which one to use, `ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 user@VPS-IP`.

## 3. Configure

```bash
cd /opt/claudegpt
cp .env.example .env
chmod 600 .env
nano .env
```

Fill in at least:

- `DOMAIN`: your domain, without `https://` (for example `ia.example.com`).
- `ACME_EMAIL`: your email, for the certificates' ACME account. Let's Encrypt no longer sends expiry warnings (since June 2025): Caddy renews the certificates by itself and, if a renewal fails, you will only see it in `docker compose logs caddy`. If you want a warning, use an external service that monitors the certificate.

The rest already has correct values. Save with `Ctrl+O` and `Enter`, and exit with `Ctrl+X`.

## 4. Start

```bash
docker compose up -d --build
```

The first time takes about 5–10 minutes: it downloads some base images, builds the interface and includes the official CLIs (Claude Code 2.1.283 and Codex 0.157.1). The final image takes up about 900 MB.

Check that everything is running:

```bash
docker compose ps
```

Both services (`app` and `caddy`) must be `running` and `healthy`. (`caddy-init` is not listed: it has already done its job.) To watch Caddy obtain the certificate:

```bash
docker compose logs -f caddy
```

When you see `certificate obtained successfully`, exit with `Ctrl+C`.

## 5. Create the owner (password and TOTP)

```bash
docker compose exec -it app agentic-os init
```

1. Choose a password of at least 12 characters (a phrase is better: "three-blue-cats-in-the-rain").
2. Scan the QR code with the authenticator app. If the terminal is too small or the app does not scan it, make the terminal bigger, or type into the app the key shown under the QR code.
3. Type the 6-digit code that the app shows you, to confirm.

If you ever lose your phone or forget the password, run this command again on the server: it replaces the owner and ends every open session.

## 6. Connect Claude (Pro/Max subscription)

**Recommended option: a one-year token**

```bash
docker compose exec -it app claude setup-token
```

1. Copy the link it shows and open it in your computer's browser.
2. Log in with your Claude account and authorize access.
3. Paste into the terminal the code that the website gives you.
4. The command shows a token that starts with `sk-ant-oat01-`. Open `.env` (`nano .env`), remove the `#` from the line `CLAUDE_CODE_OAUTH_TOKEN=` and paste the token there.
5. Apply the change: `docker compose up -d`

The token lasts a year and can only be used to make requests to the model. Put its expiry date in your calendar.

**Alternative: a login saved on the server**

```bash
docker compose exec -it app claude auth login
```

Follow the same steps (link, authorize, paste the code). The session is saved in the `app_home` volume and renews itself; there is no need to touch `.env`.

## 7. Connect ChatGPT (Plus/Pro subscription)

```bash
docker compose exec -it app codex login --device-auth
```

1. Open the link it shows (from your phone or your computer) and log in with your ChatGPT account.
2. Type the code that appears in the terminal.
3. Check it: `docker compose exec app codex login status`
4. Restart the app so that the Codex process picks up the new session: `docker compose restart app`

If it tells you that logging in with a device code is not allowed, look for the option in the security settings of your ChatGPT account (in business or team accounts, the administrator has to turn it on).

## 8. Alternative: API keys

If you prefer to pay per use (or to mix: Claude with a subscription and ChatGPT with a key, for example), edit `.env`:

```bash
AOS_CLAUDE_MODE=api
ANTHROPIC_API_KEY=sk-ant-api03-...
AOS_CHATGPT_MODE=api
OPENAI_API_KEY=sk-proj-...
```

And apply it with `docker compose up -d`.

> **Important:** if Claude is in `cli` mode, leave `ANTHROPIC_API_KEY` commented out. The Claude CLI gives this key priority over the subscription, and would bill everything through the API.

To try the interface without spending anything, there is the `fake` mode (`AOS_CLAUDE_MODE=fake`, `AOS_CHATGPT_MODE=fake`).

## 9. Check everything

```bash
docker compose exec app agentic-os doctor
```

It checks the settings, the database, the owner, the exchange rate, the interface, the CLIs and the status of each provider, with the usage limits of the ChatGPT subscription (Claude's are not shown: the Claude CLI only reports them when it answers). Each check starts with `[ OK ]`, `[ -- ]` (not needed), `[WARN]` or `[ERROR]`; at the end it says whether there is any critical problem.

## 10. First login

Open `https://your-domain` in the browser, and type the password and the TOTP code. That's it!

- A session lasts up to 72 hours without activity, and 30 days at most.
- To end every session and forget the known devices (for example, if you have lost a device): `docker compose exec app agentic-os reset-sessions`

## Updating

Once a month, **even if `git pull` brings nothing new**:

```bash
cd /opt/claudegpt
git pull
docker compose pull caddy
docker compose build --pull
docker compose up -d
docker compose restart caddy
docker image prune -f
```

- `--pull` downloads the latest base images (Debian, Python, Node), with the month's security patches; `docker compose pull caddy` does the same for Caddy's image. Without it, Docker reuses the old copies it already has.
- `docker compose restart caddy` applies the changes to `deploy/Caddyfile` that `git pull` brings: Docker does not notice them by itself, and Caddy would carry on with the old configuration.
- Do it when no answer is in progress: the app restarts (a few seconds) and the turns being generated are cut short. The interface reconnects by itself.
- The versions of the CLIs and of Caddy are pinned in the project and are updated with `git pull`. The app talks to each CLI in a very particular way, Codex above all, and a new version can break it.
- If you want to try another version before the project updates it, set `CLAUDE_CLI_VERSION` or `CODEX_CLI_VERSION` in `.env` and run `docker compose up -d --build` again. Check it with `agentic-os doctor`. To go back, delete the line and rebuild.

**The server.** Automatic updates only include Debian/Ubuntu's security updates. Docker (and with it `containerd` and `runc`, which isolate the containers) comes from Docker's repository and does not update by itself, on purpose: a new version of Docker is best installed while you are watching. Do it with the monthly update:

```bash
apt-get update && apt-get upgrade
docker compose ps
```

The containers keep running, or start again by themselves; `docker compose ps` confirms it. If the file `/var/run/reboot-required` exists, reboot the server (`reboot`) or wait for the automatic reboot at 04:30.

**Only once, when you update an installation from before these changes** (September 2026; if it was not needed, it does no harm):

- Delete Codex's old log, which kept the whole text of every call to ChatGPT (these files now live in memory, in `/run/codex-state`, and are deleted every time the app starts Codex): `docker compose exec app sh -c 'rm -f /home/app/.codex/logs_2.sqlite*'`. Old backups contain it too: delete them or keep them encrypted.
- If you have backups in `/opt/claudegpt/backups`, download them and delete them from the server (`rm -rf /opt/claudegpt/backups`): they are now saved outside the repository (see [Backups](#backups)).
- Run `bash deploy/harden.sh` again: it removes the limit of 3 SSH attempts, which could lock out anyone with several keys in their agent.
- Caddy no longer runs as root: the `caddy-init` service hands its volumes over to the new user by itself.
- Backups are now made with `bash deploy/backup.sh` and restored with `bash deploy/restore.sh` (see [Backups](#backups)): don't use the old command blocks, which could delete the previous backup of the same day or leave the app stopped. Backups made with the old blocks are restored with `deploy/restore.sh` too.
- If you made a backup with the old commands, check it: if `tar` failed, it left an incomplete file that looked fine. On your computer, `age -d -i claudegpt-backup.key claudegpt-YYYY-MM-DD.tar.gz.age | tar tzf - > /dev/null && echo OK` (unencrypted, `tar tzf claudegpt-YYYY-MM-DD.tar.gz > /dev/null && echo OK`).

## Backups

| Volume | What it holds | If you lose it... |
| --- | --- | --- |
| `claudegpt_app_data` | SQLite database: owner (password hash and TOTP secret), sessions, conversations, statistics and preferences. Also the files attached to conversations, in `/data/attachments` | You lose the conversations and their attachments, and you have to run `agentic-os init` again |
| `claudegpt_app_home` | Claude's (`~/.claude`) and Codex's (`~/.codex`) logins. Codex's logs, which contain the prompts, are not there: they live in memory | You have to log in to the CLIs again |
| `claudegpt_caddy_data` | Let's Encrypt certificates and account | Caddy requests them again by itself |
| `claudegpt_caddy_config` | Caddy's internal configuration | Nothing; it is regenerated |

After a restore, the first two have a new name, such as `claudegpt_app_data_r20260928-101500`: `deploy/restore.sh` always restores into new volumes and writes their names to `.env` (`APP_DATA_VOLUME` and `APP_HOME_VOLUME`).

Also, **keep the `.env` file**: it has the Claude token and the API keys. The backup script saves it next to the data.

**Attachments.** The images, PDFs and text files you attach to your questions are saved in the data volume, in `/data/attachments` (directories 700 and files 600, like the database), so they go into the backups and are restored with them. Each file is saved only once, named after its hash (`sha256`), even if you upload it several times; the thumbnails go in `/data/attachments/thumbnails`. They take up space on the disk and in every backup:

- An attachment you never send (you remove it from the composer or close the tab) is deleted by itself within a day at most.
- Deleting a conversation deletes the attachments that only it used.
- To see how much space they take up: `docker compose exec app du -sh /data/attachments`. If the disk fills up, uploading a file gives an error that says so, and the app keeps working.

Attachments contain whatever you uploaded (documents, photos): treat the backups with the same care as the conversations.

> The backups contain secrets (the TOTP secret, the subscription tokens and the API keys). They are saved in `/var/backups/claudegpt`, a directory with permissions 700 (only your user and root) that is **outside the repository** (so a `git add` can never upload them). Encrypt them, download them and delete them from the server.

**Encryption (recommended, once).** On your computer, install [age](https://github.com/FiloSottile/age) and create a key: `age-keygen -o claudegpt-backup.key`. It shows the public key (`age1...`): that is the one you will use on the server. The private key (the file) never leaves your computer; keep a copy of it in a safe place, because without it you won't be able to restore. On the server: `apt-get install -y age`.

> **Do it inside `tmux`.** If the SSH connection drops halfway through a backup or a restore, the scripts leave everything in a safe state, but they stop. Inside `tmux` they carry on to the end: open it with `tmux new -s backup` before you start and, if the connection drops, go back to it with `tmux attach -t backup`. (If you don't have it: `apt-get install -y tmux`. `screen` works too.)

**Making a backup** on the server, as root (`sudo -i`, as in step 2):

```bash
cd /opt/claudegpt
bash deploy/backup.sh age1...        # your public key
```

- Before touching anything, it checks the key, the image and the volumes. Then it stops the app for a few seconds, so that the database is consistent, and starts it again even if something fails, you press Ctrl+C or the connection drops.
- It creates two new files with the date and time in their names: `claudegpt-YYYY-MM-DD_HHMMSS.tar.gz.age` (the data) and `env-YYYY-MM-DD_HHMMSS.age` (the `.env`); if you make two in the same second, the second one gets a suffix (`_HHMMSS-1`). It never overwrites or deletes an earlier backup: if something fails, it says `ERROR` and no half-written file is left.
- The files belong to the user you logged in to the VPS with, so that you can download them; the directory stays private.
- Unencrypted (only if you can't install age): `bash deploy/backup.sh --unencrypted`. The files have the same names, without `.age`.
- If the script dies suddenly (a `kill -9` or a power cut), it cannot clean up: the app may be left stopped (`docker compose start app` starts it again), and hidden temporary files with `.partial.` in their names are left in `/var/backups/claudegpt`; the next backup deletes them.
- If a restore is unfinished (see below), the backup stops with `ERROR` without touching anything: finish the restore first with `--resume` or `--undo`.

**Downloading it** from your computer (not from the VPS), into the folder where you keep your backups. `user` is the same user you log in to the VPS with in step 2 (`root` if you log in directly as root), and `S` is what the script printed after `Backup done:`, with the suffix if it has one:

```bash
S=YYYY-MM-DD_HHMMSS
V=user@VPS-IP
scp "${V}:/var/backups/claudegpt/claudegpt-$S.tar.gz.age" "${V}:/var/backups/claudegpt/env-$S.age" . \
  && age -d -i claudegpt-backup.key "claudegpt-$S.tar.gz.age" | tar tzf - > /dev/null \
  && ssh "$V" "rm /var/backups/claudegpt/claudegpt-$S.tar.gz.age /var/backups/claudegpt/env-$S.age" \
  && echo "Backup downloaded and checked: it is no longer on the server."
```

It deletes only these two files from the server, and only if they were downloaded correctly and the backup can be decrypted and read to the end with your key. If your key is in another folder, replace `claudegpt-backup.key` with its path. If the backup is unencrypted, remove `.age` from the names and, instead of the `age` line, check it with `tar tzf "claudegpt-$S.tar.gz" > /dev/null`.

**Restoring** (on the same server, or on a new one with steps 1–4 done). The backup is restored into new volumes: the current data is not touched, and you can get it back until you confirm that everything went well.

1. On your computer, decrypt the backup and the `.env`: `age -d -i claudegpt-backup.key -o claudegpt-YYYY-MM-DD_HHMMSS.tar.gz claudegpt-YYYY-MM-DD_HHMMSS.tar.gz.age` and `age -d -i claudegpt-backup.key -o env-YYYY-MM-DD_HHMMSS env-YYYY-MM-DD_HHMMSS.age`. If `age` gives an error, the backup is incomplete or not yours: don't use it. If you restore on a server with another domain, change `DOMAIN` in the decrypted `env-...` file.
2. Upload both files to your home directory on the server: `scp claudegpt-YYYY-MM-DD_HHMMSS.tar.gz env-YYYY-MM-DD_HHMMSS user@VPS-IP:`
3. On the server, as root (`sudo -i`) and inside `tmux`. If you log in directly as root, the files are in `/root/` instead of `/home/user/`:

```bash
cd /opt/claudegpt
bash deploy/restore.sh /home/user/claudegpt-YYYY-MM-DD_HHMMSS.tar.gz /home/user/env-YYYY-MM-DD_HHMMSS
```

The script works in steps and records where it is in `/var/lib/claudegpt/restore.state`. If a step fails, it says `ERROR` and explains the state it leaves things in:

| Step | What it does | If it fails or you interrupt it (Ctrl+C, a dropped connection) |
| --- | --- | --- |
| R0 | Reads the whole backup and checks that it is from ClaudeGPT OS, that the `.env` works, that the image is there and that there is enough space | Nothing has been touched. Fix the cause and try again |
| R1 | Creates two new volumes, extracts the backup into them and checks its database. The app keeps running | The new volumes are deleted and the app does not stop. Fix the cause and try again |
| R2 | Prepares the new `.env` (`.env.next`), with the names of the new volumes | As in R1 |
| R3 | Checks that the new volumes are still there, stops the app and switches the `.env` in one go; the previous one is kept as `.env.prev` | It rolls back by itself: the `.env` and the volumes from before, with the app running. The new volumes are kept: `--resume` tries again and `--undo` discards them |
| R4 | Starts the app with the restored data and waits for it to answer | As in R3 |
| R5 | Done. The volumes from before, `.env.prev` and the uploaded files are kept | Check the app and run `--finalize` (or `--undo`, to go back to the data from before) |

When it finishes, open the web app and check that the conversations are there. Then clean up with `--finalize`: first it checks that the app works with the restored data and that the database is sound (if not, it deletes nothing), and then it asks you to type `delete` to confirm:

| Command | What for |
| --- | --- |
| `bash deploy/restore.sh --status` | See which step it is at. If the connection dropped, the messages that the script could no longer show you are in `/var/lib/claudegpt/restore.state.log` |
| `bash deploy/restore.sh --resume` | Continue an interrupted restore, or try it again after an error at R3 or R4 |
| `bash deploy/restore.sh --undo` | Go back to the state from before, from any step: the `.env` and the volumes from before, with the app running. If the app has already run with the restored data (at R5, or if the script died at R4), the restored volumes are kept, with everything written to them since: the script tells you their names and how to delete them |
| `bash deploy/restore.sh --finalize` | Once you have checked the restore: deletes the volumes from before, `.env.prev` and the uploaded files |

- If the script dies halfway (a `kill -9` or a power cut), it cannot roll back by itself: `--status` tells you where it stopped, and `--resume` or `--undo` finish the job.
- Before going on, `--resume` checks that the restored volumes are still there and that their database is sound: while no container uses them (for example, after an error at R3 or R4), a `docker volume prune` deletes them. If the restore rolled back by itself, or the script died before R3, it fills them again from the uploaded backup; if it died at R3 or R4, it does not go on, and asks you to run `--undo` and start again.
- While a restore is pending, even at R5 before `--finalize`, another one cannot start.
- While the restore script is running, no backup can be made, and vice versa: the second one to arrive stops with `ERROR` without touching anything. Nor can a backup be made while a restore that was interrupted, or that could not roll back, has not been finished with `--resume` or `--undo`.
- The restore only recreates the app's container. If the backup's `.env` changes `DOMAIN`, `ACME_EMAIL` or `ALLOWED_IPS`, apply it to Caddy too with `docker compose up -d` when it finishes.
- The options' earlier Catalan names still work: `--estat`, `--reprèn`, `--desfés`, `--finalitza` (also without accents) and, for backups, `--sense-xifrar`.

## Troubleshooting

First of all, always:

```bash
docker compose ps
docker compose exec app agentic-os doctor
docker compose logs --tail 100 app
docker compose logs --tail 100 caddy
```

**The browser says that the certificate is not valid, or that the connection was closed**
- Always open the web app through the domain, never through the IP: for security, Caddy closes any connection that is not for your domain.
- Check the DNS (`nslookup your-domain`), and that ports 80 and 443 are open in the provider's firewall too.
- Look at Caddy's errors: `docker compose logs caddy | grep -i error`. If `rateLimited` shows up, Let's Encrypt is making you wait: fix the cause and wait an hour.
- If `docker compose up` says that `caddy-init` failed, or Caddy complains of `permission denied` on `/data` or `/config`, look at `docker compose logs caddy-init` and run `docker compose up -d` again.

**`docker compose up` says "Range of CPUs is from 0.01 to 1.00"**
- The server has a single vCPU and the app asks for 1.5. Add `APP_CPUS=1` to `.env` and run `docker compose up -d` again.

**Error 502 (Bad Gateway)**
- The app is not answering. Look at `docker compose logs --tail 100 app`.
- If it says "The configuration (AOS_* variables) is not valid", the lines below it say which variable fails and why. Fix it in `.env` and apply it with `docker compose up -d`. `agentic-os doctor` makes the same check.
- If it ran out of memory (`docker compose ps` shows it restarting; `dmesg | grep -i oom`), raise `mem_limit` in `docker-compose.yml`, or upgrade the VPS to 4 GB.
- During an update it is normal for a few seconds.

**The interface says it is reconnecting and never connects (WebSocket)**
- `AOS_PUBLIC_ORIGIN` must match the browser's address exactly: `https://`, no trailing slash and the same name (with or without `www`). If it does not match, the server refuses the connection for security. Fix it in `.env` and apply it with `docker compose up -d`. (The interface also mentions `AOS_EXTRA_ORIGINS`, other allowed addresses: you don't need it here, because Caddy only serves `DOMAIN`.)
- Some antivirus programs or company proxies block WebSockets: try from another network.

**I can't log in**
- "The password or code is incorrect" and you are sure of the password: the time on your phone or on the server is wrong. On the server: `timedatectl` (it must say `System clock synchronized: yes`). Every failed attempt counts towards the lockout.
- "Too many attempts": the lockout grows with every error. A browser where you had already logged in (in the last 12 months, and without clearing its cookies) is only locked out by its own errors: even if someone is trying passwords from the Internet, you can still log in from it. From a new browser or device, wait for the time it shows, or lift every lockout from the server: `docker compose exec app agentic-os reset-throttle`.
- If "Too many attempts" comes back without you making any mistake, someone is trying passwords against your site. The password and the TOTP code still protect you, and the browsers where you had already logged in are not locked out. A new device, though, will be locked out again while the attack lasts, even if you run `reset-throttle` (the attacker sets the lockout off again with a few attempts): to log in from it, restrict access to your IPs with `ALLOWED_IPS` in `.env` (and `docker compose up -d`) or use a VPN. That also cuts the attack off at the root.
- Forgotten password or lost phone: `docker compose exec -it app agentic-os init`.

**Claude says that the session has expired or that it is not connected**
- Check it: `docker compose exec app claude auth status`
- With a token: generate a new one (`claude setup-token`, step 6), replace it in `.env` and run `docker compose up -d`.
- With a login saved on the server: `docker compose exec -it app claude auth login`.

**ChatGPT says that it is not connected**
- `docker compose exec app codex login status`; if needed, do step 7 again and `docker compose restart app`.
- Codex's logs live in a 64 MB in-memory space (`/run/codex-state`) that the app empties every time it starts Codex, so they never stop it from starting again. If, after many calls without restarting the app, ChatGPT starts failing and the logs show SQLite or disk-full errors, `docker compose restart app` empties it completely.

**Subscription usage limits**
- Subscriptions have usage windows (for example, of 5 hours and of 7 days). The dashboard shows the percentage used and when it renews: ChatGPT's always, and Claude's from Claude's first answer since the app started (the Claude CLI only reports it when it answers). `agentic-os doctor` only shows ChatGPT's.
- When the limit is reached, that model fails until it renews. Meanwhile, use the other model in Solo mode, or switch to `api` mode for a while.
- Council mode makes several calls per question: use it when it is worth it.

**Disk full**
- `docker system df` to see what takes up the space; `docker image prune -f` and `docker builder prune -f` free old images and build cache.

## Security

**What is exposed to the Internet**
- Only Caddy (80 and 443) and SSH. The app has no published port and lives on an internal network.
- Caddy serves HTTPS with HSTS, redirects HTTP to HTTPS and closes the connections that are not for your domain. The access logs keep no cookies, and no log (neither the app's nor Caddy's) keeps the query of the URLs: neither the names of the files you upload nor what you search for.
- Request bodies have a maximum of 1 MiB (4 KiB for the login). Caddy passes a body on to the app as it arrives, without holding it in memory, and the app answers 408 and closes the connection if it has not arrived whole within 15 seconds (Caddy cuts off at 30 s): a slow upload, or one that stalls halfway, cannot hold a connection for long. WebSockets do not have this limit. Uploading an attachment, which can only be done while logged in, has a limit of its own: 20 MB and 120 seconds (Caddy cuts off at 150 s).
- Attachments: the server decides their type from their content, refuses SVG and never serves an uploaded file as a web page (PDFs and text files are downloaded). It reads PDFs in a separate process, with no access to the app's secrets and with limits on time (60 seconds) and memory, so that a malicious PDF cannot jam it. If even so the app's container runs out of memory (two large PDFs at once during a debate), the kernel kills that process first: the upload fails with a message, and the CLIs and the server carry on.
- Docker and ufw: the ports that Docker publishes bypass ufw's rules. Here only 80 and 443 are published, and they have to be public. Don't add `ports:` to the app; to debug, use `127.0.0.1:PORT:PORT` and an SSH tunnel.

**Login and sessions**
- Password (argon2id) **and** TOTP code, which cannot be reused. Exponential lockout after failed attempts, which survives a restart (`agentic-os reset-throttle` lifts it).
- Every browser where you have logged in gets a second cookie, a known-device cookie (a year; it is not deleted when you log out). A known device is only locked out by its own errors: other people's attempts from the Internet cannot lock you out of a browser that you already use. `agentic-os reset-sessions` and `agentic-os init` forget every device. During a sustained attack, a new device can only log in if you restrict access with `ALLOWED_IPS` or a VPN (see [Troubleshooting](#troubleshooting)).
- Sessions are stored on the server (only their hash is kept), with a `__Host-` cookie that is HttpOnly, Secure and SameSite=Strict. They expire after 72 hours without activity and after 30 days (`AOS_SESSION_IDLE_HOURS`, from 1 to 8,760 hours; `AOS_SESSION_MAX_DAYS`, from 1 to 3,650 days). An open tab that you are not using does not count as activity.

**Secrets**
- `.env` (permissions 600) and the `app_home` volume contain credentials that give access to your subscriptions. Whoever is root on the VPS can use them: don't share access to the server.
- The CLIs run in an empty directory with a closed list of environment variables, without access to any *shell* or to the app's secrets. Claude's has no tools at all. Codex 0.157.1 still offers ChatGPT a tool that runs code in a child process of Codex (`codex-code-mode`, an isolated V8 environment without access to files or the network, which stops with Codex) and tools to open subagents. The app only lets one subagent run at a time (`agents.max_threads=1`), interrupts at once any work that does not belong to a call in progress, stops the call if ChatGPT uses subagents in it more than 3 times and, once no call is in progress, restarts any Codex process that has opened one (stopped subagents do not free their memory). As an extra protection, the app has a CPU limit (`APP_CPUS`).
- Codex saves the text of every call in its logs: they live in memory (`/run/codex-state`), outside the volumes and the backups, and are deleted every time the app starts Codex (and when the app restarts).

**Optional hardening**
- `ALLOWED_IPS` in `.env`: only these IPs will be able to open the web app.
- A VPN (Tailscale or WireGuard) instead of exposing the web app to the Internet: more secure, but less convenient.

**Terms of use of the subscriptions**

Using the subscriptions through the official CLIs is an area that each provider regulates in its own way. Read their terms and decide for yourself:

- **Anthropic.** The Help Center article "Use the Claude Agent SDK with your Claude plan" (June 2026) lists the `claude -p` command in your own projects among the uses that draw on your plan's limits. The terms of use forbid sharing credentials, and automated access that is not explicitly permitted. The app uses the official CLI unmodified, and never extracts the token to call the API directly.
- **OpenAI.** It recommends API keys for automation. Using the ChatGPT subscription through Codex in an app like this one is not explicitly authorized: it is your own responsibility.
- **Recommendation:** a single user (you), interactive and moderate use, without sharing access with anyone. If you have doubts, or use it heavily, use `api` mode.
