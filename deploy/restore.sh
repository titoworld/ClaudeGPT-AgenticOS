#!/usr/bin/env bash
# Restores a backup made by deploy/backup.sh without ever modifying the current
# volumes (docs/DESPLEGAMENT.md, section «Còpies de seguretat»). As root, on the
# server, with the backup decrypted on your computer and uploaded:
#
#   cd /opt/claudegpt
#   bash deploy/restore.sh ARCHIVE.tar.gz ENV_FILE   # restore
#   bash deploy/restore.sh --estat                   # where it is
#   bash deploy/restore.sh --reprèn                  # continue or retry it
#   bash deploy/restore.sh --desfés                  # back to the state before it
#   bash deploy/restore.sh --finalitza               # delete what it kept (asks first)
#
# (--repren and --desfes, without accents, work too. Relative paths are taken
# from the directory it is run in.)
#
# Steps. A journal (/var/lib/claudegpt/restore.state) records each step before it
# starts, with the volume names and the paths, so that a restore that was
# killed can be resumed or undone:
#   R0  Checks; nothing is touched. The whole archive is read (gzip CRC and tar
#       structure) and holds data/agentic_os.sqlite3 and home/app/; the env file
#       works with docker-compose.yml; the image exists; the new volume names
#       are free; Docker's disk has room for the uncompressed data plus a margin.
#   R1  Creates two NEW volumes, claudegpt_app_data_r<stamp> and
#       claudegpt_app_home_r<stamp> (labelled claudegpt.restore=<stamp>), extracts
#       the archive into them with the app image (uid 10001) and checks the
#       database in a throwaway container (PRAGMA integrity_check, owner table).
#       The app keeps running meanwhile.
#   R2  Writes .env.next: the uploaded env file with APP_DATA_VOLUME and
#       APP_HOME_VOLUME pointing to the new volumes.
#   R3  Checks that the new volumes are still the ones R1 filled, stops the app,
#       keeps the current .env as .env.prev (a hard link) and renames .env.next
#       to .env: data and secrets change in one atomic step.
#   R4  Checks the new volumes again (docker compose would create a missing one,
#       empty), starts the app (docker compose up --wait: the healthcheck) and
#       checks /api/health and that it runs with the new volumes.
#   R5  Done. The old volumes, .env.prev and the uploaded files are kept until
#       --finalitza, which checks that the app runs with the restored data and
#       asks for a typed confirmation before deleting them.
#
# What a failure leaves, and what to run then:
#   R0       Nothing touched.                                  Fix it, run again.
#   R1, R2   The new volumes and .env.next are deleted; the    Fix it, run again.
#            app never stopped.
#   R3, R4   Rolled back on its own: the old .env and volumes, --reprèn retries,
#            the app running with them. The new volumes are    --desfés drops them.
#            kept for inspection.
#   Ctrl+C, a dropped SSH session or SIGTERM do what a failure of that step does
#   (after a hang-up the messages go to /var/lib/claudegpt/restore.state.log);
#   once the clean-up has started, further signals are ignored.
#   If the script is killed (kill -9, a power cut), the journal keeps the step:
#   --estat shows it, --reprèn continues from it and --desfés goes back to the
#   state before the restore (old .env and volumes, app running) from any step.
#   --reprèn first checks that the new volumes are still the ones R1 filled (with
#   no container using them, `docker volume prune` deletes them): after a
#   roll-back, or if the script was killed before R3, it fills them again from
#   the upload; if it was killed at R3 or R4, it stops and asks for --desfés.
#   --desfés deletes the new volumes, except after R5 or when the script was
#   killed at R4: the app has run with them, or may have, so they can hold data
#   written since. Then it keeps them and says how to delete them.
#   While a restore is pending, including R5 before --finalitza, another one
#   cannot start, and deploy/backup.sh does not run while one is unfinished.
#
# Environment (tests): RESTORE_STATE (the journal; default
# /var/lib/claudegpt/restore.state) and APP_IMAGE (default claudegpt-os:latest).
set -euo pipefail
umask 077

readonly IMAGE=${APP_IMAGE:-claudegpt-os:latest}
readonly STATE=${RESTORE_STATE:-/var/lib/claudegpt/restore.state}
readonly PROJECT=claudegpt # the compose project (name: in docker-compose.yml)
readonly WAIT_SECONDS=300
# The names of a fresh install (the defaults of docker-compose.yml).
readonly DEFAULT_DATA_VOLUME=claudegpt_app_data
readonly DEFAULT_HOME_VOLUME=claudegpt_app_home
# The label of the volumes that R1 creates; its value is the restore's stamp.
readonly LABEL=claudegpt.restore

# Run in a throwaway container of the app image, on the new data volume.
readonly DB_CHECK='
import pathlib, sqlite3, sys
path = sys.argv[1]
try:
    db = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=rw", uri=True)
    try:
        result = db.execute("PRAGMA integrity_check").fetchall()
        owner = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", ("table", "owner")
        ).fetchone()
    finally:
        db.close()
except sqlite3.Error as exc:
    sys.exit(f"{path}: {exc}")
if result != [("ok",)]:
    sys.exit("PRAGMA integrity_check: " + "; ".join(str(row[0]) for row in result[:5]))
if owner is None:
    sys.exit(f"{path}: no hi ha la taula owner")
'
# The healthcheck of docker-compose.yml.
readonly HEALTH_CHECK='import sys, urllib.request as u; sys.exit(0 if u.urlopen("http://127.0.0.1:8000/api/health", timeout=4).status == 200 else 1)'

usage() {
  cat << 'EOF'
Ús: bash deploy/restore.sh CÒPIA.tar.gz FITXER_ENV
    bash deploy/restore.sh --estat | --reprèn | --desfés | --finalitza

Restaura una còpia de deploy/backup.sh (ja desxifrada) en volums nous, sense
tocar les dades actuals; les esborra --finalitza, quan ho confirmes.
Guia: docs/DESPLEGAMENT.md, apartat «Còpies de seguretat».
EOF
}
usage_error() {
  usage >&2
  exit 2
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
# interrupt it (the roll-back uses it).
detached() {
  if command -v setsid > /dev/null; then setsid -w "$@"; else "$@"; fi
}

valid_volume() { [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]]; }

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
  valid_volume "$REPLY" || die "$1, al fitxer .env, no és un nom de volum vàlid: «$REPLY»."
}

is_encrypted() {
  local start
  start=$(head -c 34 -- "$1" | tr -d '\0')
  [[ $start == age-encryption.org/* || $start == "-----BEGIN AGE ENCRYPTED FILE-----" ]]
}

# ------------------------------------------------------------------ the journal
step="" status="" stamp="" archive="" env_file=""
old_data="" old_home="" new_data="" new_home=""

# One KEY=value per line, replaced atomically (a temporary file and a rename).
write_journal() {
  local tmp
  tmp=$(mktemp "$STATE.XXXXXX") || return 1
  if printf '%s\n' \
    "# deploy/restore.sh: bash deploy/restore.sh --estat ho explica" \
    "step=$step" "status=$status" "stamp=$stamp" \
    "archive=$archive" "env_file=$env_file" \
    "old_data_volume=$old_data" "old_home_volume=$old_home" \
    "new_data_volume=$new_data" "new_home_volume=$new_home" > "$tmp" &&
    sync -- "$tmp" && mv -f -- "$tmp" "$STATE"; then
    sync -- "$(dirname -- "$STATE")" || true
    return 0
  fi
  rm -f -- "$tmp"
  return 1
}

read_journal() { # status 1 if there is none
  [ -e "$STATE" ] || return 1
  local key value
  while IFS='=' read -r key value; do
    case $key in
      step) step=$value ;;
      status) status=$value ;;
      stamp) stamp=$value ;;
      archive) archive=$value ;;
      env_file) env_file=$value ;;
      old_data_volume) old_data=$value ;;
      old_home_volume) old_home=$value ;;
      new_data_volume) new_data=$value ;;
      new_home_volume) new_home=$value ;;
    esac
  done < "$STATE"
  case $step in R1 | R2 | R3 | R4 | R5) ;; *) die "el diari $STATE està malmès (pas «$step»)." ;; esac
  case $status in
    running | rolled_back | rollback_failed | done) ;;
    *) die "el diari $STATE està malmès (estat «$status»)." ;;
  esac
  if ! { [[ $stamp =~ ^[0-9]{8}-[0-9]{6}$ ]] && valid_volume "$old_data" &&
    valid_volume "$old_home" && [ "$new_data" = "${DEFAULT_DATA_VOLUME}_r$stamp" ] &&
    [ "$new_home" = "${DEFAULT_HOME_VOLUME}_r$stamp" ] &&
    [ "$new_data" != "$old_data" ] && [ "$new_home" != "$old_home" ]; }; then
    die "el diari $STATE està malmès (noms dels volums)."
  fi
}

# deploy/backup.sh takes the same lock: never a backup in the middle of a restore.
lock() {
  install -d -m 700 -- "$(dirname -- "$STATE")"
  exec 9>> "$STATE.lock"
  if command -v flock > /dev/null && ! flock -n 9; then
    die "hi ha una altra ordre de deploy/restore.sh o deploy/backup.sh en marxa: espera que acabi."
  fi
}

step_text() {
  case $1 in
    R1) echo "R1, extreure la còpia en volums nous i verificar-la" ;;
    R2) echo "R2, preparar el .env nou (.env.next)" ;;
    R3) echo "R3, aturar l'aplicació i canviar el .env" ;;
    R4) echo "R4, engegar l'aplicació amb les dades restaurades" ;;
    R5) echo "R5, restauració feta, pendent de --finalitza" ;;
  esac
}

# ------------------------------------------------ what failures leave behind
current="" # the step in progress, which the EXIT trap has to clean up after
work=""    # a temporary directory

on_exit() {
  local code=$?
  # Nothing interrupts the clean-up, not even a second Ctrl+C or hang-up: the
  # roll-back has to run to the end (the commands it starts ignore them too).
  trap '' INT TERM HUP
  set +e
  [ -z "$work" ] || rm -rf -- "$work"
  if [ "$code" != 0 ] && [ -n "$current" ]; then
    [ "$reported" = 1 ] ||
      warn "ERROR: la restauració ha fallat al pas $current (mira el missatge de sobre)."
    case $current in
      R1 | R2) discard_new ;;
      R3 | R4) roll_back ;;
    esac
  fi
  exit "$code"
}
interrupted() {
  trap '' INT TERM HUP
  warn "ERROR: s'ha interromput la restauració${current:+ al pas $current}."
  [ -n "$current" ] || warn "Mira en quin estat ha quedat amb: bash deploy/restore.sh --estat"
  reported=1
  exit "$1"
}
on_hangup() {
  trap '' INT TERM HUP
  # The terminal is gone: the rest of the messages go to a log next to the journal.
  local log=/dev/null
  if [ -d "$(dirname -- "$STATE")" ] && [ -w "$(dirname -- "$STATE")" ]; then log=$STATE.log; fi
  exec >> "$log" 2>&1
  warn "$(date '+%F %T'): s'ha tallat la connexió."
  interrupted 129
}
catch_signals() {
  trap 'interrupted 130' INT
  trap 'interrupted 143' TERM
  trap on_hangup HUP
}
trap on_exit EXIT
catch_signals

# Deletes the new volumes, if they exist, and never the old ones.
drop_new_volumes() {
  local volume attempt failed=0
  for volume in "$new_data" "$new_home"; do
    if [ "$volume" = "$old_data" ] || [ "$volume" = "$old_home" ]; then continue; fi
    docker volume inspect "$volume" > /dev/null 2>&1 || continue
    # An interrupted `docker run --rm` can hold the volume for a moment.
    for attempt in 1 2 3 4 5; do
      if docker volume rm "$volume" > /dev/null 2>&1; then continue 2; fi
      sleep "$attempt"
    done
    docker volume rm "$volume" > /dev/null || failed=1
  done
  return "$failed"
}

# Puts back the .env from before the restore, whatever step R3 reached.
restore_env() {
  if [ -e .env.next ]; then # the switch did not happen: .env is the old one
    rm -f -- .env.next || return 1
  fi
  if [ -e .env.prev ]; then
    if [ .env.prev -ef .env ]; then # linked, but not switched yet
      rm -f -- .env.prev || return 1
    else
      mv -fT -- .env.prev .env || return 1
    fi
  fi
  sync -- . || true
}

discard_new() { # R1 or R2 failed: nothing in use was touched
  rm -f -- .env.next
  if drop_new_volumes; then
    rm -f -- "$STATE"
    warn "No s'ha tocat res de la instal·lació: l'aplicació continua funcionant amb les dades i el .env d'abans."
    warn "Els fitxers pujats continuen a $archive i $env_file: corregeix la causa i torna-ho a provar."
  else
    status=running
    write_journal || warn "AVÍS: no he pogut actualitzar el diari $STATE."
    warn "No he pogut esborrar els volums nous ($new_data, $new_home). L'aplicació continua amb les dades d'abans."
    warn "Executa: bash deploy/restore.sh --desfés"
  fi
}

roll_back() { # R3 or R4 failed: back to the old .env and volumes, keeping the new ones
  warn "Torno a posar el .env i les dades d'abans..."
  if restore_env && start_app detached; then
    status=rolled_back
    write_journal || warn "AVÍS: no he pogut actualitzar el diari $STATE."
    warn "L'aplicació torna a funcionar amb les dades i el .env d'abans. No s'ha esborrat res:"
    warn "  - els volums restaurats es conserven per si els vols revisar: $new_data, $new_home"
    warn "  - els fitxers pujats continuen a $archive i $env_file"
    warn "Per tornar-ho a provar: bash deploy/restore.sh --reprèn"
    warn "Per descartar la restauració: bash deploy/restore.sh --desfés"
  else
    status=rollback_failed
    write_journal || warn "AVÍS: no he pogut actualitzar el diari $STATE."
    warn "ERROR: no he pogut tornar a engegar l'aplicació amb les dades d'abans."
    warn "Mira docker compose logs app i executa: bash deploy/restore.sh --desfés"
  fi
}

# ----------------------------------------------------------------- the checks
check_paths() {
  local path here
  here=$(pwd -P)
  for path in "$archive" "$env_file"; do
    [[ $path != *$'\n'* ]] || die "el nom del fitxer $path té un salt de línia."
    [ -f "$path" ] || die "$path no és un fitxer."
    case $path in
      "$here/.env" | "$here/.env.prev" | "$here/.env.next" | "$STATE"*)
        die "$path no pot ser un dels fitxers pujats."
        ;;
    esac
  done
  [ "$archive" != "$env_file" ] || die "la còpia i el fitxer .env han de ser dos fitxers diferents."
}

archive_bytes=0 archive_members=0
check_archive() {
  local listing
  [ -r "$archive" ] || die "no trobo o no puc llegir la còpia $archive."
  ! is_encrypted "$archive" ||
    die "$archive està xifrada: desxifra-la al teu ordinador (age -d) i puja el .tar.gz."
  work=${work:-$(mktemp -d)}
  listing=$work/listing
  say "    Llegeixo tota la còpia..."
  # The whole listing first (tar reads to the end: gzip's CRC is checked), and only
  # then the searches.
  LC_ALL=C tar -tzvf "$archive" > "$listing" ||
    die "no es pot llegir tota la còpia $archive: està malmesa o la pujada es va tallar."
  if ! awk '$1 ~ /^-/ && NF == 6 && $6 == "data/agentic_os.sqlite3" { found = 1 }
            END { exit !found }' "$listing" ||
    ! awk '$6 ~ /^home\/app\// { found = 1 } END { exit !found }' "$listing"; then
    die "$archive no és una còpia de ClaudeGPT OS: hi falta data/agentic_os.sqlite3 o home/app/."
  fi
  read -r archive_bytes archive_members < <(
    awk '{ bytes += $3; members++ } END { printf "%.0f %d\n", bytes, members }' "$listing"
  )
}

check_env_file() {
  [ -s "$env_file" ] || die "el fitxer $env_file no existeix o és buit."
  ! is_encrypted "$env_file" ||
    die "$env_file està xifrat: desxifra'l al teu ordinador (age -d) i puja'l."
  docker compose --env-file "$env_file" config --quiet ||
    die "$env_file no serveix per a docker-compose.yml (mira el missatge de sobre)."
}

check_image() {
  docker image inspect "$IMAGE" > /dev/null ||
    die "no hi ha la imatge $IMAGE. Construeix-la amb docker compose build i torna-ho a provar."
}

check_space() {
  local root available need
  # The old volumes are kept, so the new ones need room for all the data: the sizes
  # in the archive, a block per file and a margin (KiB).
  need=$(((archive_bytes + archive_members * 4096) / 1024))
  need=$((need + need / 10 + 100 * 1024))
  if root=$(docker info --format '{{.DockerRootDir}}') &&
    available=$(df -Pk -- "$root" | awk 'NR == 2 { print $4 }') &&
    [[ $available =~ ^[0-9]+$ ]]; then
    [ "$available" -ge "$need" ] ||
      die "no hi ha prou espai a $root: calen uns $((need / 1024)) MB i n'hi ha $((available / 1024)). Allibera'n (docker system df) i torna-ho a provar."
  else
    warn "AVÍS: no he pogut comprovar l'espai lliure de Docker; continuo."
  fi
}

# The database of the data volume $1, in a throwaway container: intact, and with
# the owner table of ClaudeGPT OS.
database_ok() {
  docker run --rm --log-driver none --network none --read-only --tmpfs /tmp -v "$1:/data" \
    "$IMAGE" python3 -c "$DB_CHECK" /data/agentic_os.sqlite3
}

# Whether the volume $1 is one that R1 of this restore created and filled. While
# no container uses them (after a roll-back, say), `docker volume prune` deletes
# the new volumes, and docker compose would create them again: empty, and without
# this label.
restored_volume() {
  local label
  label=$(docker volume inspect --format "{{index .Labels \"$LABEL\"}}" "$1" 2> /dev/null) &&
    [ "$label" = "$stamp" ]
}

check_new_volumes() {
  if ! restored_volume "$new_data" || ! restored_volume "$new_home"; then
    die "els volums restaurats ($new_data, $new_home) ja no hi són, o no són els que va omplir la restauració (els ha esborrat un docker volume prune?)."
  fi
}

# The new volumes, with the database checked again (--reprèn).
new_volumes_ok() {
  restored_volume "$new_data" && restored_volume "$new_home" && database_ok "$new_data"
}

app_healthy() { docker compose exec -T app python -c "$HEALTH_CHECK" > /dev/null; }

# Starts the app with the volumes .env names now, and waits for its healthcheck. The
# container is recreated: compose would reuse a stopped one with its old volumes if
# the service definition looked unchanged. $@: an optional prefix (detached).
start_app() {
  "$@" docker compose up -d --wait --wait-timeout "$WAIT_SECONDS" --force-recreate app
}

app_uses() { # whether the app container runs with the volumes $1 and $2
  local id mounts
  if ! id=$(docker compose ps -q app) || [ -z "$id" ]; then return 1; fi
  mounts=$(docker inspect --format '{{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}' "$id") ||
    return 1
  [[ " $mounts " == *" $1:/data "* && " $mounts " == *" $2:/home/app "* ]]
}

# ------------------------------------------------------------------ the steps
enter() { # the journal records a step before any change
  current=$1 step=$1 status=running
  write_journal || die "no he pogut escriure el diari $STATE."
}

step_r1() {
  enter R1
  say "R1: extrec la còpia als volums nous $new_data i $new_home..."
  drop_new_volumes || die "no he pogut esborrar els volums nous d'un intent anterior."
  local volume
  for volume in "$new_data:app_data" "$new_home:app_home"; do
    # Labelled like the volumes that docker compose creates, so that it takes them
    # as its own (and does not warn about them at every `up`), and with the stamp
    # of this restore (restored_volume).
    docker volume create --label "com.docker.compose.project=$PROJECT" \
      --label "com.docker.compose.volume=${volume#*:}" --label "$LABEL=$stamp" \
      "${volume%%:*}" > /dev/null || die "no he pogut crear el volum ${volume%%:*}."
  done
  # As the image's user (uid 10001), who owns /data and /home/app: Docker gives an
  # empty volume the owner of the directory it is mounted on.
  docker run --rm -i --log-driver none --network none --read-only \
    -v "$new_data:/data" -v "$new_home:/home/app" "$IMAGE" \
    tar -xzf - -C / data home/app < "$archive" ||
    die "no s'ha pogut extreure la còpia als volums nous (mira el missatge de sobre)."
  database_ok "$new_data" || die "la base de dades de la còpia no està bé (mira el missatge de sobre)."
}

step_r2() {
  enter R2
  say "R2: preparo el .env nou..."
  [ -s "$env_file" ] || die "el fitxer $env_file no existeix o és buit."
  rm -f -- .env.next
  # The uploaded .env without its volume names, which are the backup server's.
  grep -vE '^[[:space:]]*(export[[:space:]]+)?APP_(DATA|HOME)_VOLUME[[:space:]]*=|^# Volums restaurats per deploy/restore.sh' \
    -- "$env_file" > .env.next || [ "$?" = 1 ]
  if [ -s .env.next ] && [ -n "$(tail -c 1 .env.next)" ]; then echo >> .env.next; fi
  printf '# Volums restaurats per deploy/restore.sh (%s)\nAPP_DATA_VOLUME=%s\nAPP_HOME_VOLUME=%s\n' \
    "$stamp" "$new_data" "$new_home" >> .env.next
  chmod 600 .env.next
  sync -- .env.next
}

step_r3() {
  check_new_volumes # before anything stops
  enter R3
  say "R3: aturo l'aplicació i canvio el .env..."
  docker compose stop app || die "no s'ha pogut aturar l'aplicació."
  trap '' INT TERM HUP # a link and a rename: always finish them
  [ -e .env.prev ] || ln -T -- .env .env.prev
  mv -fT -- .env.next .env
  sync -- .
  catch_signals
}

step_r4() {
  enter R4
  say "R4: engego l'aplicació amb les dades restaurades i espero que respongui..."
  check_new_volumes # docker compose would create a missing one again, empty
  start_app || die "l'aplicació no arrenca amb les dades restaurades (mira docker compose logs app)."
  app_healthy || die "l'aplicació no respon a /api/health."
  app_uses "$new_data" "$new_home" ||
    die "l'aplicació no fa servir els volums nous: revisa APP_DATA_VOLUME i APP_HOME_VOLUME a docker-compose.yml."
}

step_r5() {
  # The restore has worked: from here on there is nothing to roll back, even if a
  # Ctrl+C stops the journal from being told (it then says R4, and --reprèn from
  # R4 only checks the app again).
  current=""
  step=R5 status="done"
  write_journal ||
    warn "AVÍS: no he pogut apuntar al diari $STATE que la restauració està feta. Executa bash deploy/restore.sh --reprèn: ho comprovarà i ho apuntarà."
  say ""
  say "R5: restauració feta. L'aplicació ja funciona amb les dades de la còpia."
  say "Per si cal tornar enrere, es conserven:"
  say "  - els volums d'abans: $old_data i $old_home"
  say "  - el .env d'abans: $(pwd -P)/.env.prev"
  say "  - els fitxers pujats: $archive i $env_file"
  say "Comprova l'aplicació (entra a la web i mira-hi les converses). Després:"
  say "  bash deploy/restore.sh --finalitza   esborra tot això (et demana confirmació)"
  say "  bash deploy/restore.sh --desfés      torna a les dades d'abans; les restaurades es conserven"
}

# Plain commands, never in an && or || list: set -e has to stay on inside the steps.
run_from() {
  case $1 in
    R1)
      step_r1
      step_r2
      step_r3
      step_r4
      ;;
    R2)
      step_r2
      step_r3
      step_r4
      ;;
    R3)
      step_r3
      step_r4
      ;;
    R4) step_r4 ;;
  esac
  step_r5
}

# --------------------------------------------------------------- the commands
restore() {
  lock
  if read_journal; then
    die "hi ha una restauració pendent (pas $(step_text "$step")). Mira-la amb bash deploy/restore.sh --estat i acaba-la amb --reprèn, --desfés o --finalitza abans de començar-ne una altra."
  fi
  if [ -e .env.next ] || [ -e .env.prev ]; then
    die "hi ha fitxers .env.next o .env.prev d'una restauració anterior a $(pwd -P). Si ja no els necessites, esborra'ls i torna-ho a provar."
  fi
  [ -f .env ] || die "no trobo el fitxer .env a $(pwd -P): fes primer els passos 1 a 4 de la guia."
  archive=$upload_archive env_file=$upload_env
  check_paths
  stamp=$(date +%Y%m%d-%H%M%S)
  volume_name APP_DATA_VOLUME "$DEFAULT_DATA_VOLUME"
  old_data=$REPLY
  volume_name APP_HOME_VOLUME "$DEFAULT_HOME_VOLUME"
  old_home=$REPLY
  new_data=${DEFAULT_DATA_VOLUME}_r$stamp
  new_home=${DEFAULT_HOME_VOLUME}_r$stamp

  say "R0: comprovo la còpia i el servidor, sense tocar res..."
  check_archive
  check_env_file
  check_image
  local volume
  for volume in "$new_data" "$new_home"; do
    if docker volume inspect "$volume" > /dev/null 2>&1; then
      die "ja existeix un volum $volume: torna-ho a provar d'aquí a un segon."
    fi
  done
  check_space
  run_from R1
}

show_status() {
  if [ ! -e "$STATE" ]; then
    say "No hi ha cap restauració en curs."
    return 0
  fi
  read_journal
  local busy=0
  exec 9>> "$STATE.lock"
  if command -v flock > /dev/null && ! flock -n 9; then busy=1; fi
  say "Restauració $stamp"
  say "  Pas: $(step_text "$step")"
  case $status in
    running)
      if [ "$busy" = 1 ]; then
        say "  Estat: en marxa ara mateix"
      else
        say "  Estat: interrompuda (l'script no va poder acabar aquest pas)"
      fi
      ;;
    rolled_back) say "  Estat: ha fallat i ha tornat enrere; l'aplicació funciona amb les dades d'abans" ;;
    rollback_failed) say "  Estat: ha fallat i no ha pogut tornar enrere del tot" ;;
    done) say "  Estat: feta; l'aplicació funciona amb les dades de la còpia" ;;
  esac
  say "  Còpia pujada: $archive"
  say "  Fitxer .env pujat: $env_file"
  say "  Volums d'abans: $old_data, $old_home"
  say "  Volums nous: $new_data, $new_home"
  if [ -f .env ]; then
    env_value APP_DATA_VOLUME
    say "  El .env actual fa servir: ${REPLY:-$DEFAULT_DATA_VOLUME}"
  fi
  say ""
  case $status in
    done)
      say "Comprova l'aplicació i després:"
      say "  bash deploy/restore.sh --finalitza   esborra els volums d'abans, .env.prev i els fitxers pujats"
      say "  bash deploy/restore.sh --desfés      torna a les dades d'abans; es conserven els volums restaurats, amb el que s'hi hagi escrit"
      ;;
    rolled_back)
      say "  bash deploy/restore.sh --reprèn   torna-ho a provar amb els volums nous"
      say "  bash deploy/restore.sh --desfés   descarta la restauració i esborra els volums nous"
      ;;
    *)
      say "  bash deploy/restore.sh --reprèn   continua-la"
      if [ "$step" = R4 ] && [ "$status" = running ]; then
        say "  bash deploy/restore.sh --desfés   torna a l'estat d'abans (.env i volums d'abans, aplicació en marxa); es conserven els volums nous, per si l'aplicació hi ha escrit"
      else
        say "  bash deploy/restore.sh --desfés   torna a l'estat d'abans (.env i volums d'abans, aplicació en marxa)"
      fi
      ;;
  esac
  if [ -s "$STATE.log" ]; then
    say ""
    say "Si la connexió s'ha tallat durant una restauració, els missatges de l'script són a $STATE.log"
  fi
}

resume() {
  lock
  read_journal || die "no hi ha cap restauració per reprendre."
  if [ "$status" = "done" ]; then
    say "La restauració $stamp ja està feta: comprova l'aplicació i fes --finalitza (o --desfés)."
    return 0
  fi
  [ -f .env ] || die "no trobo el fitxer .env a $(pwd -P)."
  local from
  case $step in
    R1 | R2) from=$step ;;
    *)
      if [ -e .env.next ]; then # .env was not switched yet
        from=R3
        if [ -e .env.prev ] && ! [ .env.prev -ef .env ]; then
          die "hi ha .env.next i un .env.prev diferent de .env: no sé quin és el bo. Revisa'ls a mà."
        fi
      elif [ -e .env.prev ] && ! [ .env.prev -ef .env ]; then # switched
        from=R4
      else # rolled back: the old .env is in place again
        from=R2
      fi
      ;;
  esac
  # First the image: without it the database check below fails too.
  check_image
  # The new volumes have to be the ones R1 filled (restored_volume).
  if [ "$from" != R1 ] && ! new_volumes_ok; then
    if [ "$from" != R2 ]; then # .env.next or .env name them already: stop here
      die "els volums restaurats ($new_data, $new_home) ja no hi són, no són els que va omplir la restauració o la seva base de dades no està bé (mira el missatge de sobre): no continuo. Torna a les dades d'abans amb bash deploy/restore.sh --desfés i torna a començar la restauració."
    fi
    warn "AVÍS: els volums restaurats ($new_data, $new_home) ja no hi són, no són els que va omplir la restauració (els ha esborrat un docker volume prune?) o la seva base de dades no està bé. Els torno a omplir a partir de la còpia pujada."
    from=R1
  fi
  say "Reprenc la restauració $stamp des del pas $from."
  if [ "$from" = R1 ]; then
    drop_new_volumes || die "no he pogut esborrar els volums nous a mig fer ($new_data, $new_home)."
    check_archive
  fi
  if [ "$from" = R1 ] || [ "$from" = R2 ]; then check_env_file; fi
  if [ "$from" = R1 ]; then check_space; fi
  run_from "$from"
}

undo() {
  lock
  if ! read_journal; then
    say "No hi ha cap restauració en curs: no hi ha res a desfer."
    return 0
  fi
  # After R5 the app has run with the new volumes, and a restore killed at R4 can
  # have left it running with them: they can hold data written since the
  # restore, so they are kept. Otherwise they hold what the upload does.
  local keep=0 volume kept=()
  if [ "$step" = R5 ] || { [ "$step" = R4 ] && [ "$status" = running ]; }; then keep=1; fi
  say "Desfaig la restauració $stamp (pas $(step_text "$step"))..."
  restore_env || die "no he pogut tornar a posar el .env d'abans ($(pwd -P)/.env.prev)."
  if [ "$step" = R1 ] || [ "$step" = R2 ]; then # the app never stopped: just make sure
    docker compose up -d --wait --wait-timeout "$WAIT_SECONDS" app
  else
    start_app
  fi || die "l'aplicació no arrenca amb les dades d'abans: mira docker compose logs app i torna a executar --desfés."
  app_uses "$old_data" "$old_home" ||
    die "l'aplicació no fa servir els volums d'abans ($old_data, $old_home): revisa APP_DATA_VOLUME i APP_HOME_VOLUME a $(pwd -P)/.env i torna a executar --desfés."
  if [ "$keep" = 1 ]; then
    for volume in "$new_data" "$new_home"; do
      if docker volume inspect "$volume" > /dev/null 2>&1; then kept+=("$volume"); fi
    done
  else
    drop_new_volumes ||
      die "no he pogut esborrar els volums nous ($new_data, $new_home): torna a executar --desfés."
  fi
  rm -f -- "$STATE"
  say "Fet: l'aplicació funciona amb les dades i el .env d'abans de la restauració."
  if [ "${#kept[@]}" -gt 0 ]; then
    say "Els volums restaurats es conserven, amb tot el que s'hi hagi escrit des de la restauració: ${kept[*]}"
    say "Quan estiguis segur que no els necessites, esborra'ls amb: docker volume rm ${kept[*]}"
  fi
  say "Els fitxers pujats continuen a $archive i $env_file: esborra'ls quan no els necessitis."
}

finalize() {
  lock
  read_journal || die "no hi ha cap restauració per finalitzar."
  [ "$status" = "done" ] ||
    die "la restauració no està acabada (pas $(step_text "$step")): mira bash deploy/restore.sh --estat."
  volume_name APP_DATA_VOLUME "$DEFAULT_DATA_VOLUME"
  local data=$REPLY
  volume_name APP_HOME_VOLUME "$DEFAULT_HOME_VOLUME"
  if [ "$data" != "$new_data" ] || [ "$REPLY" != "$new_home" ]; then
    die "el .env ja no fa servir els volums restaurats ($new_data, $new_home): no esborro res."
  fi
  if ! app_healthy || ! app_uses "$new_data" "$new_home"; then
    die "l'aplicació no funciona amb les dades restaurades: no esborro res. Mira docker compose logs app, o torna enrere amb --desfés."
  fi
  # The volumes in use have to be the ones R1 filled, with a sound database, and
  # not empty ones that docker compose created again after they were deleted.
  if ! restored_volume "$new_data" || ! restored_volume "$new_home" ||
    ! database_ok "$new_data"; then
    die "l'aplicació no funciona amb les dades que es van restaurar, o la base de dades no està bé: no esborro res. Torna a les dades d'abans amb bash deploy/restore.sh --desfés."
  fi

  say "S'esborrarà, sense possibilitat de recuperar-ho:"
  local volume
  for volume in "$old_data" "$old_home"; do
    if docker volume inspect "$volume" > /dev/null 2>&1; then say "  - el volum $volume"; fi
  done
  [ ! -e .env.prev ] || say "  - $(pwd -P)/.env.prev"
  [ ! -e "$archive" ] || say "  - $archive"
  [ ! -e "$env_file" ] || say "  - $env_file"
  printf '%s' "Escriu «esborra» per confirmar-ho: "
  local answer=""
  read -r answer || true
  [ "$answer" = esborra ] || die "no ho has confirmat: no s'ha esborrat res."

  for volume in "$old_data" "$old_home"; do
    if docker volume inspect "$volume" > /dev/null 2>&1; then
      docker volume rm "$volume" > /dev/null ||
        die "no he pogut esborrar el volum $volume (el fa servir algun contenidor?). Torna-ho a provar amb --finalitza."
    fi
  done
  rm -f -- .env.prev "$archive" "$env_file"
  rm -f -- "$STATE"
  say "Fet: la restauració $stamp s'ha completat i s'ha esborrat el que es conservava."
}

upload_archive="" upload_env=""
case ${1:-} in
  --estat | --reprèn | --repren | --desfés | --desfes | --finalitza) [ $# -eq 1 ] || usage_error ;;
  -h | --help | --ajuda)
    usage
    exit 0
    ;;
  -* | "") usage_error ;;
  *)
    [ $# -eq 2 ] || usage_error
    # From the directory it is run in, before moving to the repository.
    upload_archive=$(realpath -e -- "$1" 2> /dev/null) || die "no trobo la còpia $1."
    upload_env=$(realpath -e -- "$2" 2> /dev/null) || die "no trobo el fitxer $2."
    ;;
esac

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# docker compose has to take the volume names from .env, as this script does.
unset APP_DATA_VOLUME APP_HOME_VOLUME

case $1 in
  --estat) show_status ;;
  --reprèn | --repren) resume ;;
  --desfés | --desfes) undo ;;
  --finalitza) finalize ;;
  *) restore ;;
esac
