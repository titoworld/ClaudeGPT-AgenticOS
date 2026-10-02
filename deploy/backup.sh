#!/usr/bin/env bash
# Backup of ClaudeGPT OS: the two data volumes and .env, encrypted with age
# (docs/DEPLOYMENT.md, section «Còpies de seguretat»). As root, on the server:
#
#   cd /opt/claudegpt
#   bash deploy/backup.sh age1...          # your age public key
#   bash deploy/backup.sh --sense-xifrar   # discouraged: without encryption
#
# It adds two files to /var/backups/claudegpt (mode 700), named after the date
# and time of the backup:
#   claudegpt-YYYY-MM-DD_HHMMSS.tar.gz.age   the volumes (database, CLI logins)
#   env-YYYY-MM-DD_HHMMSS.age                .env (Claude token, API keys)
# Without encryption the names are the same without ".age".
#
# Guarantees:
# - It never replaces or deletes a backup. The names carry the time, and a
#   finished file gets its name with link(2), which fails if the name exists
#   (two backups in the same second get a numeric suffix: ..._HHMMSS-1...).
# - Everything is written to hidden temporary files in the same directory and
#   fsync'ed before it gets its final name: an error, Ctrl+C or a dropped SSH
#   session never leaves a partial file under a backup name. If the backup is
#   killed (kill -9, a power cut), the next one deletes those temporaries.
# - The key, the image and the volumes are checked before the app stops. The
#   app is stopped only while the volumes are read (so the database is
#   consistent), and a trap starts it again whatever happens.
# - It runs in its own bash: umask 077 and the shell options stay here and never
#   reach the shell it was started from.
#
# The volumes are the ones .env names (APP_DATA_VOLUME, APP_HOME_VOLUME, which
# deploy/restore.sh sets), or claudegpt_app_data and claudegpt_app_home.
#
# It takes the lock of deploy/restore.sh, so the two never run at the same time,
# and it does not run while a restore is unfinished (killed, or unable to roll
# back): .env could name other volumes than the ones the app runs with.
#
# Environment (tests): BACKUP_DIR (default /var/backups/claudegpt), APP_IMAGE
# (default claudegpt-os:latest) and RESTORE_STATE (the journal of restore.sh,
# default /var/lib/claudegpt/restore.state).
set -euo pipefail
umask 077

readonly BACKUP_DIR=${BACKUP_DIR:-/var/backups/claudegpt}
readonly IMAGE=${APP_IMAGE:-claudegpt-os:latest}
readonly STATE=${RESTORE_STATE:-/var/lib/claudegpt/restore.state}
readonly LOCK=$STATE.lock

usage() {
  cat << 'EOF'
Ús: bash deploy/backup.sh CLAU_PÚBLICA_AGE    (la clau comença per age1...)
    bash deploy/backup.sh --sense-xifrar      (desaconsellat: còpia sense xifrar)

Desa una còpia de les dades i del fitxer .env a /var/backups/claudegpt.
Guia: docs/DEPLOYMENT.md, apartat «Còpies de seguretat».
EOF
}

say() { printf '%s\n' "$*" || true; }
warn() { printf '%s\n' "$*" >&2 || true; }
reported=0
die() {
  warn "ERROR: $*"
  reported=1
  exit 1
}

# Runs a command in a session of its own: the terminal's Ctrl+C or hang-up cannot
# interrupt it (the trap uses it to start the app again).
detached() {
  if command -v setsid > /dev/null; then setsid -w "$@"; else "$@"; fi
}

# The value of $1 in .env as docker compose reads it, without expansions: the last
# assignment wins; quotes and a trailing comment are dropped.
env_value() {
  REPLY=$(sed -nE "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=(.*)$/\2/p" .env | tail -n 1)
  REPLY=${REPLY%$'\r'}
  REPLY=${REPLY%%#*}
  REPLY=${REPLY#"${REPLY%%[![:space:]]*}"}
  REPLY=${REPLY%"${REPLY##*[![:space:]]}"}
  case $REPLY in \"*\" | \'*\') REPLY=${REPLY:1:${#REPLY}-2} ;; esac
}

# The volume that .env names in $1, or $2 (${VAR:-default} in docker-compose.yml).
volume_name() {
  env_value "$1"
  REPLY=${REPLY:-$2}
  [[ $REPLY =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]] ||
    die "$1, al fitxer .env, no és un nom de volum vàlid: «$REPLY»."
}

encrypt=1 key=""
[ $# -eq 1 ] || {
  usage >&2
  exit 2
}
case $1 in
  -h | --help | --ajuda)
    usage
    exit 0
    ;;
  --sense-xifrar) encrypt=0 ;;
  -*)
    usage >&2
    exit 2
    ;;
  *) key=$1 ;;
esac

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

tmp_archive="" tmp_env="" stopped=0 published=0
finish() {
  local status=$?
  set +e
  trap '' INT TERM HUP # nothing interrupts the clean-up
  [ -z "$tmp_archive" ] || rm -f -- "$tmp_archive"
  [ -z "$tmp_env" ] || rm -f -- "$tmp_env"
  if [ "$stopped" = 1 ]; then
    if detached docker compose start app > /dev/null; then
      stopped=0
    else
      warn "ERROR: l'aplicació ha quedat aturada. Engega-la amb: docker compose start app"
      reported=1
      [ "$status" != 0 ] || status=1
    fi
  fi
  if [ "$status" != 0 ] && [ "$published" = 0 ]; then
    [ "$reported" = 1 ] || warn "ERROR: la còpia ha fallat (mira el missatge de sobre)."
    warn "No s'ha fet cap còpia nova, i les còpies anteriors continuen intactes."
  fi
  exit "$status"
}
interrupted() {
  trap '' INT TERM HUP
  warn "ERROR: s'ha interromput la còpia."
  reported=1
  exit "$1"
}
trap finish EXIT
trap 'interrupted 130' INT
trap 'interrupted 143' TERM
# The terminal is gone: writing to it would fail, so the clean-up writes nowhere.
trap 'exec > /dev/null 2>&1; interrupted 129' HUP

# ---------------------------------------------------- checks: nothing stops yet
[ -f .env ] || die "no trobo el fitxer .env a $PWD."
install -d -m 700 -- "$(dirname -- "$LOCK")"
exec 9>> "$LOCK"
locked=0
if command -v flock > /dev/null; then
  flock -n 9 ||
    die "hi ha una restauració o una altra còpia en marxa: espera que acabi (bash deploy/restore.sh --estat)."
  locked=1
fi
# A restore that was killed, or that could not roll back, can leave .env naming
# the restored volumes while the app container still has the old ones: this
# backup would save the wrong data, and start the old container.
if [ -f "$STATE" ]; then
  case $(sed -n 's/^status=//p' "$STATE") in
    running | rollback_failed)
      die "hi ha una restauració a mitges. Mira-la amb bash deploy/restore.sh --estat i acaba-la amb --reprèn o --desfés; després fes la còpia."
      ;;
  esac
fi
volume_name APP_DATA_VOLUME claudegpt_app_data
data_volume=$REPLY
volume_name APP_HOME_VOLUME claudegpt_app_home
home_volume=$REPLY

if [ "$encrypt" = 1 ]; then
  command -v age > /dev/null ||
    die "no trobo age. Instal·la'l (apt-get install -y age) o fes servir --sense-xifrar."
  age -r "$key" < /dev/null > /dev/null ||
    die "la clau pública no és vàlida: «$key». Copia-la sencera (age1...) des del teu ordinador."
fi
docker image inspect "$IMAGE" > /dev/null ||
  die "no hi ha la imatge $IMAGE. Construeix-la amb: docker compose build"
for volume in "$data_volume" "$home_volume"; do
  # `docker run` would create a missing volume, empty: that would be an empty backup.
  docker volume inspect "$volume" > /dev/null ||
    die "no existeix el volum $volume (revisa APP_DATA_VOLUME i APP_HOME_VOLUME a .env)."
done
install -d -m 700 -- "$BACKUP_DIR"
# A backup that was killed (kill -9, a power cut) leaves its hidden temporaries
# here (in plain text with --sense-xifrar). Only this script writes those names,
# and the lock says that no other backup is running.
if [ "$locked" = 1 ]; then
  shopt -s nullglob
  stale=("$BACKUP_DIR"/.claudegpt-????-??-??_??????.parcial.??????
    "$BACKUP_DIR"/.env-????-??-??_??????.parcial.??????)
  shopt -u nullglob
  if [ "${#stale[@]}" -gt 0 ]; then
    rm -f -- "${stale[@]}" || die "no he pogut esborrar els fitxers temporals d'una còpia anterior a $BACKUP_DIR."
    say "He esborrat els fitxers temporals d'una còpia que es va interrompre: ${stale[*]##*/}"
  fi
fi

stamp=$(date +%Y-%m-%d_%H%M%S)
if [ "$encrypt" = 1 ]; then
  archive_ext=.tar.gz.age env_ext=.age
else
  archive_ext=.tar.gz env_ext=""
fi
tmp_archive=$(mktemp "$BACKUP_DIR/.claudegpt-$stamp.parcial.XXXXXX")
tmp_env=$(mktemp "$BACKUP_DIR/.env-$stamp.parcial.XXXXXX")

# ------------------------------------------------ the data, with the app stopped
read_volumes() {
  # --log-driver none: the daemon would also write the whole stream to the
  # container's log file.
  docker run --rm --log-driver none --network none -v "$data_volume:/data:ro" \
    -v "$home_volume:/home/app:ro" "$IMAGE" tar czf - -C / data home/app
}

say "Aturo l'aplicació uns segons, mentre llegeixo les dades..."
stopped=1 # before the stop: an interrupted stop must start the app again too
docker compose stop app || die "no s'ha pogut aturar l'aplicació."
if [ "$encrypt" = 1 ]; then
  read_volumes | age -r "$key" > "$tmp_archive" ||
    die "no s'han pogut llegir o xifrar les dades (mira el missatge de sobre)."
else
  read_volumes > "$tmp_archive" || die "no s'han pogut llegir les dades (mira el missatge de sobre)."
fi
# If it does not start, the trap tries again and reports it; the backup is good.
if docker compose start app; then stopped=0; fi

if [ "$encrypt" = 1 ]; then
  age -r "$key" < .env > "$tmp_env" || die "no s'ha pogut xifrar el fitxer .env."
else
  cat .env > "$tmp_env" || die "no s'ha pogut copiar el fitxer .env."
fi

# -------------------------------------------------------------------- publish
sync -- "$tmp_archive" "$tmp_env" || die "no s'han pogut desar les dades al disc."

# Links $1 as $2: status 1 if $2 exists; any other error ends the backup.
new_name() {
  local error
  if [ -e "$2" ] || [ -L "$2" ]; then return 1; fi
  error=$(ln -T -- "$1" "$2" 2>&1) && return 0
  if [ -e "$2" ] || [ -L "$2" ]; then return 1; fi
  die "no s'ha pogut desar $2: $error"
}

suffix="" n=0
while :; do
  archive=$BACKUP_DIR/claudegpt-$stamp$suffix$archive_ext
  env_copy=$BACKUP_DIR/env-$stamp$suffix$env_ext
  if new_name "$tmp_archive" "$archive"; then
    if new_name "$tmp_env" "$env_copy"; then
      published=1
      break
    fi
    # Only the name this backup has just created, never somebody else's file.
    if [ "$archive" -ef "$tmp_archive" ]; then rm -f -- "$archive"; fi
  fi
  n=$((n + 1))
  [ "$n" -lt 100 ] || die "no trobo cap nom lliure per a la còpia a $BACKUP_DIR."
  suffix=-$n
done
sync -- "$BACKUP_DIR" || warn "AVÍS: no s'ha pogut confirmar que el directori s'ha desat al disc."
rm -f -- "$tmp_archive" "$tmp_env"
tmp_archive="" tmp_env=""
# The owner downloads them with the user they log in with (sudo -i keeps it).
chown -- "${SUDO_USER:-root}" "$BACKUP_DIR" "$archive" "$env_copy" ||
  warn "AVÍS: els fitxers no són de ${SUDO_USER:-root}; per descarregar-los, entra com a root."

say "Còpia feta: $stamp$suffix"
say "  $archive"
say "  $env_copy"
say "Descarrega-la al teu ordinador i esborra-la del servidor (docs/DEPLOYMENT.md)."
