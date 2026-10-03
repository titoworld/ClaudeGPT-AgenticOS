#!/usr/bin/env bash
# Restores a backup made by deploy/backup.sh without ever modifying the current
# volumes (docs/DEPLOYMENT.md, section "Backups"). As root, on the
# server, with the backup decrypted on your computer and uploaded:
#
#   cd /opt/claudegpt
#   bash deploy/restore.sh ARCHIVE.tar.gz ENV_FILE   # restore
#   bash deploy/restore.sh --status                  # where it is
#   bash deploy/restore.sh --resume                  # continue or retry it
#   bash deploy/restore.sh --undo                    # back to the state before it
#   bash deploy/restore.sh --finalize                # delete what it kept (asks first)
#
# (Relative paths are taken from the directory it is run in. The Catalan names of
# the options in earlier versions work too: --estat, --reprèn or --repren, --desfés
# or --desfes, --finalitza.)
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
#       --finalize, which checks that the app runs with the restored data and
#       asks for a typed confirmation before deleting them.
#
# What a failure leaves, and what to run then:
#   R0       Nothing touched.                                  Fix it, run again.
#   R1, R2   The new volumes and .env.next are deleted; the    Fix it, run again.
#            app never stopped.
#   R3, R4   Rolled back on its own: the old .env and volumes, --resume retries,
#            the app running with them. The new volumes are    --undo drops them.
#            kept for inspection.
#   Ctrl+C, a dropped SSH session or SIGTERM do what a failure of that step does
#   (after a hang-up the messages go to /var/lib/claudegpt/restore.state.log);
#   once the clean-up has started, further signals are ignored.
#   If the script is killed (kill -9, a power cut), the journal keeps the step:
#   --status shows it, --resume continues from it and --undo goes back to the
#   state before the restore (old .env and volumes, app running) from any step.
#   --resume first checks that the new volumes are still the ones R1 filled (with
#   no container using them, `docker volume prune` deletes them): after a
#   roll-back, or if the script was killed before R3, it fills them again from
#   the upload; if it was killed at R3 or R4, it stops and asks for --undo.
#   --undo deletes the new volumes, except after R5 or when the script was
#   killed at R4: the app has run with them, or may have, so they can hold data
#   written since. Then it keeps them and says how to delete them.
#   While a restore is pending, including R5 before --finalize, another one
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
    sys.exit(f"{path}: there is no owner table")
'
# The healthcheck of docker-compose.yml.
readonly HEALTH_CHECK='import sys, urllib.request as u; sys.exit(0 if u.urlopen("http://127.0.0.1:8000/api/health", timeout=4).status == 200 else 1)'

usage() {
  cat << 'EOF'
Usage: bash deploy/restore.sh ARCHIVE.tar.gz ENV_FILE
       bash deploy/restore.sh --status | --resume | --undo | --finalize

Restores a backup of deploy/backup.sh (already decrypted) into new volumes,
without touching the current data, which --finalize deletes once you confirm.
Guide: docs/DEPLOYMENT.md, section "Backups".
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
  valid_volume "$REPLY" || die "$1 in the .env file is not a valid volume name: \"$REPLY\"."
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
    "# deploy/restore.sh: bash deploy/restore.sh --status explains it" \
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
  case $step in R1 | R2 | R3 | R4 | R5) ;; *) die "the journal $STATE is damaged (step \"$step\")." ;; esac
  case $status in
    running | rolled_back | rollback_failed | done) ;;
    *) die "the journal $STATE is damaged (status \"$status\")." ;;
  esac
  if ! { [[ $stamp =~ ^[0-9]{8}-[0-9]{6}$ ]] && valid_volume "$old_data" &&
    valid_volume "$old_home" && [ "$new_data" = "${DEFAULT_DATA_VOLUME}_r$stamp" ] &&
    [ "$new_home" = "${DEFAULT_HOME_VOLUME}_r$stamp" ] &&
    [ "$new_data" != "$old_data" ] && [ "$new_home" != "$old_home" ]; }; then
    die "the journal $STATE is damaged (volume names)."
  fi
}

# deploy/backup.sh takes the same lock: never a backup in the middle of a restore.
lock() {
  install -d -m 700 -- "$(dirname -- "$STATE")"
  exec 9>> "$STATE.lock"
  if command -v flock > /dev/null && ! flock -n 9; then
    die "another deploy/restore.sh or deploy/backup.sh command is running: wait for it to finish."
  fi
}

step_text() {
  case $1 in
    R1) echo "R1, extract the backup into new volumes and check it" ;;
    R2) echo "R2, prepare the new .env (.env.next)" ;;
    R3) echo "R3, stop the app and switch the .env" ;;
    R4) echo "R4, start the app with the restored data" ;;
    R5) echo "R5, restore done, waiting for --finalize" ;;
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
      warn "ERROR: the restore failed at step $current (see the message above)."
    case $current in
      R1 | R2) discard_new ;;
      R3 | R4) roll_back ;;
    esac
  fi
  exit "$code"
}
interrupted() {
  trap '' INT TERM HUP
  warn "ERROR: the restore was interrupted${current:+ at step $current}."
  [ -n "$current" ] || warn "See the state it was left in with: bash deploy/restore.sh --status"
  reported=1
  exit "$1"
}
on_hangup() {
  trap '' INT TERM HUP
  # The terminal is gone: the rest of the messages go to a log next to the journal.
  local log=/dev/null
  if [ -d "$(dirname -- "$STATE")" ] && [ -w "$(dirname -- "$STATE")" ]; then log=$STATE.log; fi
  exec >> "$log" 2>&1
  warn "$(date '+%F %T'): the connection dropped."
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
    warn "Nothing in the installation was touched: the app keeps running with the data and the .env from before."
    warn "The uploaded files are still at $archive and $env_file: fix the cause and try again."
  else
    status=running
    write_journal || warn "WARNING: could not update the journal $STATE."
    warn "Could not delete the new volumes ($new_data, $new_home). The app carries on with the data from before."
    warn "Run: bash deploy/restore.sh --undo"
  fi
}

roll_back() { # R3 or R4 failed: back to the old .env and volumes, keeping the new ones
  warn "Putting back the .env and the data from before..."
  if restore_env && start_app detached; then
    status=rolled_back
    write_journal || warn "WARNING: could not update the journal $STATE."
    warn "The app is running again with the data and the .env from before. Nothing was deleted:"
    warn "  - the restored volumes are kept, in case you want to inspect them: $new_data, $new_home"
    warn "  - the uploaded files are still at $archive and $env_file"
    warn "To try again: bash deploy/restore.sh --resume"
    warn "To discard the restore: bash deploy/restore.sh --undo"
  else
    status=rollback_failed
    write_journal || warn "WARNING: could not update the journal $STATE."
    warn "ERROR: could not start the app again with the data from before."
    warn "Look at docker compose logs app and run: bash deploy/restore.sh --undo"
  fi
}

# ----------------------------------------------------------------- the checks
check_paths() {
  local path here
  here=$(pwd -P)
  for path in "$archive" "$env_file"; do
    [[ $path != *$'\n'* ]] || die "the file name $path has a line break."
    [ -f "$path" ] || die "$path is not a file."
    case $path in
      "$here/.env" | "$here/.env.prev" | "$here/.env.next" | "$STATE"*)
        die "$path cannot be one of the uploaded files."
        ;;
    esac
  done
  [ "$archive" != "$env_file" ] || die "the backup and the .env file have to be two different files."
}

archive_bytes=0 archive_members=0
check_archive() {
  local listing
  [ -r "$archive" ] || die "cannot find or read the backup $archive."
  ! is_encrypted "$archive" ||
    die "$archive is encrypted: decrypt it on your computer (age -d) and upload the .tar.gz."
  work=${work:-$(mktemp -d)}
  listing=$work/listing
  say "    Reading the whole backup..."
  # The whole listing first (tar reads to the end: gzip's CRC is checked), and only
  # then the searches.
  LC_ALL=C tar -tzvf "$archive" > "$listing" ||
    die "cannot read the whole backup $archive: it is damaged, or the upload was cut short."
  if ! awk '$1 ~ /^-/ && NF == 6 && $6 == "data/agentic_os.sqlite3" { found = 1 }
            END { exit !found }' "$listing" ||
    ! awk '$6 ~ /^home\/app\// { found = 1 } END { exit !found }' "$listing"; then
    die "$archive is not a ClaudeGPT OS backup: data/agentic_os.sqlite3 or home/app/ is missing."
  fi
  read -r archive_bytes archive_members < <(
    awk '{ bytes += $3; members++ } END { printf "%.0f %d\n", bytes, members }' "$listing"
  )
}

check_env_file() {
  [ -s "$env_file" ] || die "the file $env_file does not exist or is empty."
  ! is_encrypted "$env_file" ||
    die "$env_file is encrypted: decrypt it on your computer (age -d) and upload it."
  docker compose --env-file "$env_file" config --quiet ||
    die "$env_file does not work with docker-compose.yml (see the message above)."
}

check_image() {
  docker image inspect "$IMAGE" > /dev/null ||
    die "the image $IMAGE does not exist. Build it with docker compose build and try again."
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
      die "not enough space in $root: about $((need / 1024)) MB are needed and $((available / 1024)) MB are free. Free some (docker system df) and try again."
  else
    warn "WARNING: could not check Docker's free space; carrying on."
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
    die "the restored volumes ($new_data, $new_home) are gone, or are not the ones the restore filled (did a docker volume prune delete them?)."
  fi
}

# The new volumes, with the database checked again (--resume).
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
  write_journal || die "could not write the journal $STATE."
}

step_r1() {
  enter R1
  say "R1: extracting the backup into the new volumes $new_data and $new_home..."
  drop_new_volumes || die "could not delete the new volumes of an earlier attempt."
  local volume
  for volume in "$new_data:app_data" "$new_home:app_home"; do
    # Labelled like the volumes that docker compose creates, so that it takes them
    # as its own (and does not warn about them at every `up`), and with the stamp
    # of this restore (restored_volume).
    docker volume create --label "com.docker.compose.project=$PROJECT" \
      --label "com.docker.compose.volume=${volume#*:}" --label "$LABEL=$stamp" \
      "${volume%%:*}" > /dev/null || die "could not create the volume ${volume%%:*}."
  done
  # As the image's user (uid 10001), who owns /data and /home/app: Docker gives an
  # empty volume the owner of the directory it is mounted on.
  docker run --rm -i --log-driver none --network none --read-only \
    -v "$new_data:/data" -v "$new_home:/home/app" "$IMAGE" \
    tar -xzf - -C / data home/app < "$archive" ||
    die "could not extract the backup into the new volumes (see the message above)."
  database_ok "$new_data" || die "the backup's database is not sound (see the message above)."
}

step_r2() {
  enter R2
  say "R2: preparing the new .env..."
  [ -s "$env_file" ] || die "the file $env_file does not exist or is empty."
  rm -f -- .env.next
  # The uploaded .env without its volume names, which are the backup server's, nor
  # the comment above them (in Catalan if an earlier version wrote it).
  grep -vE '^[[:space:]]*(export[[:space:]]+)?APP_(DATA|HOME)_VOLUME[[:space:]]*=|^# (Volumes restored by|Volums restaurats per) deploy/restore.sh' \
    -- "$env_file" > .env.next || [ "$?" = 1 ]
  if [ -s .env.next ] && [ -n "$(tail -c 1 .env.next)" ]; then echo >> .env.next; fi
  printf '# Volumes restored by deploy/restore.sh (%s)\nAPP_DATA_VOLUME=%s\nAPP_HOME_VOLUME=%s\n' \
    "$stamp" "$new_data" "$new_home" >> .env.next
  chmod 600 .env.next
  sync -- .env.next
}

step_r3() {
  check_new_volumes # before anything stops
  enter R3
  say "R3: stopping the app and switching the .env..."
  docker compose stop app || die "could not stop the app."
  trap '' INT TERM HUP # a link and a rename: always finish them
  [ -e .env.prev ] || ln -T -- .env .env.prev
  mv -fT -- .env.next .env
  sync -- .
  catch_signals
}

step_r4() {
  enter R4
  say "R4: starting the app with the restored data and waiting for it to answer..."
  check_new_volumes # docker compose would create a missing one again, empty
  start_app || die "the app does not start with the restored data (look at docker compose logs app)."
  app_healthy || die "the app does not answer at /api/health."
  app_uses "$new_data" "$new_home" ||
    die "the app does not use the new volumes: check APP_DATA_VOLUME and APP_HOME_VOLUME in docker-compose.yml."
}

step_r5() {
  # The restore has worked: from here on there is nothing to roll back, even if a
  # Ctrl+C stops the journal from being told (it then says R4, and --resume from
  # R4 only checks the app again).
  current=""
  step=R5 status="done"
  write_journal ||
    warn "WARNING: could not record in the journal $STATE that the restore is done. Run bash deploy/restore.sh --resume: it will check it and record it."
  say ""
  say "R5: restore done. The app now runs with the data of the backup."
  say "In case you need to go back, these are kept:"
  say "  - the volumes from before: $old_data and $old_home"
  say "  - the .env from before: $(pwd -P)/.env.prev"
  say "  - the uploaded files: $archive and $env_file"
  say "Check the app (open the web app and look at the conversations). Then:"
  say "  bash deploy/restore.sh --finalize   deletes all of this (it asks you to confirm)"
  say "  bash deploy/restore.sh --undo       goes back to the data from before; the restored data is kept"
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
    die "a restore is pending (step $(step_text "$step")). Look at it with bash deploy/restore.sh --status and finish it with --resume, --undo or --finalize before you start another one."
  fi
  if [ -e .env.next ] || [ -e .env.prev ]; then
    die "there are .env.next or .env.prev files from an earlier restore in $(pwd -P). If you no longer need them, delete them and try again."
  fi
  [ -f .env ] || die "cannot find the .env file in $(pwd -P): do steps 1 to 4 of the guide first."
  archive=$upload_archive env_file=$upload_env
  check_paths
  stamp=$(date +%Y%m%d-%H%M%S)
  volume_name APP_DATA_VOLUME "$DEFAULT_DATA_VOLUME"
  old_data=$REPLY
  volume_name APP_HOME_VOLUME "$DEFAULT_HOME_VOLUME"
  old_home=$REPLY
  new_data=${DEFAULT_DATA_VOLUME}_r$stamp
  new_home=${DEFAULT_HOME_VOLUME}_r$stamp

  say "R0: checking the backup and the server, without touching anything..."
  check_archive
  check_env_file
  check_image
  local volume
  for volume in "$new_data" "$new_home"; do
    if docker volume inspect "$volume" > /dev/null 2>&1; then
      die "a volume $volume already exists: try again in a second."
    fi
  done
  check_space
  run_from R1
}

show_status() {
  if [ ! -e "$STATE" ]; then
    say "No restore in progress."
    return 0
  fi
  read_journal
  local busy=0
  exec 9>> "$STATE.lock"
  if command -v flock > /dev/null && ! flock -n 9; then busy=1; fi
  say "Restore $stamp"
  say "  Step: $(step_text "$step")"
  case $status in
    running)
      if [ "$busy" = 1 ]; then
        say "  Status: running right now"
      else
        say "  Status: interrupted (the script could not finish this step)"
      fi
      ;;
    rolled_back) say "  Status: failed and rolled back; the app runs with the data from before" ;;
    rollback_failed) say "  Status: failed and could not fully roll back" ;;
    done) say "  Status: done; the app runs with the data of the backup" ;;
  esac
  say "  Uploaded backup: $archive"
  say "  Uploaded .env file: $env_file"
  say "  Volumes from before: $old_data, $old_home"
  say "  New volumes: $new_data, $new_home"
  if [ -f .env ]; then
    env_value APP_DATA_VOLUME
    say "  The current .env uses: ${REPLY:-$DEFAULT_DATA_VOLUME}"
  fi
  say ""
  case $status in
    done)
      say "Check the app, and then:"
      say "  bash deploy/restore.sh --finalize   deletes the volumes from before, .env.prev and the uploaded files"
      say "  bash deploy/restore.sh --undo       goes back to the data from before; the restored volumes are kept, with whatever was written to them"
      ;;
    rolled_back)
      say "  bash deploy/restore.sh --resume   tries again with the new volumes"
      say "  bash deploy/restore.sh --undo     discards the restore and deletes the new volumes"
      ;;
    *)
      say "  bash deploy/restore.sh --resume   continues it"
      if [ "$step" = R4 ] && [ "$status" = running ]; then
        say "  bash deploy/restore.sh --undo     goes back to the state from before (the .env and volumes from before, the app running); the new volumes are kept, in case the app has written to them"
      else
        say "  bash deploy/restore.sh --undo     goes back to the state from before (the .env and volumes from before, the app running)"
      fi
      ;;
  esac
  if [ -s "$STATE.log" ]; then
    say ""
    say "If the connection dropped during a restore, the script's messages are in $STATE.log"
  fi
}

resume() {
  lock
  read_journal || die "there is no restore to resume."
  if [ "$status" = "done" ]; then
    say "The restore $stamp is already done: check the app and run --finalize (or --undo)."
    return 0
  fi
  [ -f .env ] || die "cannot find the .env file in $(pwd -P)."
  local from
  case $step in
    R1 | R2) from=$step ;;
    *)
      if [ -e .env.next ]; then # .env was not switched yet
        from=R3
        if [ -e .env.prev ] && ! [ .env.prev -ef .env ]; then
          die "there is a .env.next, and a .env.prev that differs from .env: cannot tell which one is right. Check them by hand."
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
      die "the restored volumes ($new_data, $new_home) are gone, are not the ones the restore filled, or their database is not sound (see the message above): stopping here. Go back to the data from before with bash deploy/restore.sh --undo and start the restore again."
    fi
    warn "WARNING: the restored volumes ($new_data, $new_home) are gone, are not the ones the restore filled (did a docker volume prune delete them?), or their database is not sound. Filling them again from the uploaded backup."
    from=R1
  fi
  say "Resuming the restore $stamp from step $from."
  if [ "$from" = R1 ]; then
    drop_new_volumes || die "could not delete the half-made new volumes ($new_data, $new_home)."
    check_archive
  fi
  if [ "$from" = R1 ] || [ "$from" = R2 ]; then check_env_file; fi
  if [ "$from" = R1 ]; then check_space; fi
  run_from "$from"
}

undo() {
  lock
  if ! read_journal; then
    say "No restore in progress: there is nothing to undo."
    return 0
  fi
  # After R5 the app has run with the new volumes, and a restore killed at R4 can
  # have left it running with them: they can hold data written since the
  # restore, so they are kept. Otherwise they hold what the upload does.
  local keep=0 volume kept=()
  if [ "$step" = R5 ] || { [ "$step" = R4 ] && [ "$status" = running ]; }; then keep=1; fi
  say "Undoing the restore $stamp (step $(step_text "$step"))..."
  restore_env || die "could not put back the .env from before ($(pwd -P)/.env.prev)."
  if [ "$step" = R1 ] || [ "$step" = R2 ]; then # the app never stopped: just make sure
    docker compose up -d --wait --wait-timeout "$WAIT_SECONDS" app
  else
    start_app
  fi || die "the app does not start with the data from before: look at docker compose logs app and run --undo again."
  app_uses "$old_data" "$old_home" ||
    die "the app does not use the volumes from before ($old_data, $old_home): check APP_DATA_VOLUME and APP_HOME_VOLUME in $(pwd -P)/.env and run --undo again."
  if [ "$keep" = 1 ]; then
    for volume in "$new_data" "$new_home"; do
      if docker volume inspect "$volume" > /dev/null 2>&1; then kept+=("$volume"); fi
    done
  else
    drop_new_volumes ||
      die "could not delete the new volumes ($new_data, $new_home): run --undo again."
  fi
  rm -f -- "$STATE"
  say "Done: the app runs with the data and the .env from before the restore."
  if [ "${#kept[@]}" -gt 0 ]; then
    say "The restored volumes are kept, with everything written to them since the restore: ${kept[*]}"
    say "Once you are sure you don't need them, delete them with: docker volume rm ${kept[*]}"
  fi
  say "The uploaded files are still at $archive and $env_file: delete them when you no longer need them."
}

finalize() {
  lock
  read_journal || die "there is no restore to finalize."
  [ "$status" = "done" ] ||
    die "the restore is not finished (step $(step_text "$step")): see bash deploy/restore.sh --status."
  volume_name APP_DATA_VOLUME "$DEFAULT_DATA_VOLUME"
  local data=$REPLY
  volume_name APP_HOME_VOLUME "$DEFAULT_HOME_VOLUME"
  if [ "$data" != "$new_data" ] || [ "$REPLY" != "$new_home" ]; then
    die "the .env no longer uses the restored volumes ($new_data, $new_home): deleting nothing."
  fi
  if ! app_healthy || ! app_uses "$new_data" "$new_home"; then
    die "the app does not work with the restored data: deleting nothing. Look at docker compose logs app, or go back with --undo."
  fi
  # The volumes in use have to be the ones R1 filled, with a sound database, and
  # not empty ones that docker compose created again after they were deleted.
  if ! restored_volume "$new_data" || ! restored_volume "$new_home" ||
    ! database_ok "$new_data"; then
    die "the app does not run with the data that was restored, or the database is not sound: deleting nothing. Go back to the data from before with bash deploy/restore.sh --undo."
  fi

  say "This will be deleted, with no way to get it back:"
  local volume
  for volume in "$old_data" "$old_home"; do
    if docker volume inspect "$volume" > /dev/null 2>&1; then say "  - the volume $volume"; fi
  done
  [ ! -e .env.prev ] || say "  - $(pwd -P)/.env.prev"
  [ ! -e "$archive" ] || say "  - $archive"
  [ ! -e "$env_file" ] || say "  - $env_file"
  printf '%s' 'Type "delete" to confirm: '
  local answer=""
  read -r answer || true
  [ "$answer" = delete ] || die "not confirmed: nothing was deleted."

  for volume in "$old_data" "$old_home"; do
    if docker volume inspect "$volume" > /dev/null 2>&1; then
      docker volume rm "$volume" > /dev/null ||
        die "could not delete the volume $volume (is a container using it?). Try again with --finalize."
    fi
  done
  rm -f -- .env.prev "$archive" "$env_file"
  rm -f -- "$STATE"
  say "Done: the restore $stamp is complete, and what was kept has been deleted."
}

upload_archive="" upload_env=""
# The Catalan names of the options in earlier versions still work: --estat, --reprèn
# (--repren), --desfés (--desfes), --finalitza and --ajuda.
case ${1:-} in
  --status | --resume | --undo | --finalize) [ $# -eq 1 ] || usage_error ;;
  --estat | --reprèn | --repren | --desfés | --desfes | --finalitza) [ $# -eq 1 ] || usage_error ;;
  -h | --help | --ajuda)
    usage
    exit 0
    ;;
  -* | "") usage_error ;;
  *)
    [ $# -eq 2 ] || usage_error
    # From the directory it is run in, before moving to the repository.
    upload_archive=$(realpath -e -- "$1" 2> /dev/null) || die "cannot find the backup $1."
    upload_env=$(realpath -e -- "$2" 2> /dev/null) || die "cannot find the file $2."
    ;;
esac

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# docker compose has to take the volume names from .env, as this script does.
unset APP_DATA_VOLUME APP_HOME_VOLUME

case $1 in
  --status | --estat) show_status ;;
  --resume | --reprèn | --repren) resume ;;
  --undo | --desfés | --desfes) undo ;;
  --finalize | --finalitza) finalize ;;
  *) restore ;;
esac
