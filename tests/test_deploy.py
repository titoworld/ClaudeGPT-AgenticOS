"""Security invariants of the deployment files (docker-compose.yml, Dockerfile,
deploy/, .gitignore, docs/), and the behaviour of the backup and restore scripts.

They read the files as text or YAML, so they need neither Docker nor Caddy; the
deploy job of the CI checks the same files with the real tools (compose config,
caddy validate, shellcheck, image build). deploy/backup.sh, deploy/restore.sh and
the backup commands of docs/DEPLOYMENT.md run with bash against stand-ins for
docker, age, scp and ssh (STUBS): no Docker, no root, and nothing outside a
temporary directory."""

from __future__ import annotations

import contextlib
import fcntl
import glob
import io
import json
import os
import pwd
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from portability import group_alive

from agentic_os.server.middleware import (
    BODY_TIMEOUT_SECONDS,
    LARGE_BODY_PATHS,
    MAX_BODY_BYTES,
    UPLOAD_PATH,
    UPLOAD_TIMEOUT_SECONDS,
)

yaml = pytest.importorskip("yaml")  # PyYAML comes with uvicorn[standard]

ROOT = Path(__file__).resolve().parents[1]
BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BACKUP_DIR = "/var/backups/claudegpt"
RESTORE_DIR = "/home/user"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def services() -> dict[str, Any]:
    compose: dict[str, Any] = yaml.safe_load(read("docker-compose.yml"))
    return dict(compose["services"])


def size_bytes(value: str) -> int:
    """A Caddy size such as ``1MiB`` or ``2MB``."""
    match = re.fullmatch(r"(\d+)\s*([KMG]i?B)", value)
    assert match, value
    unit = match.group(2)
    base = 1024 if "i" in unit else 1000
    return int(match.group(1)) * int(base ** ("KMG".index(unit[0]) + 1))


def caddy_code(text: str) -> list[str]:
    """Lines of a Caddyfile without comments or indentation."""
    return [re.sub(r"(^|\s)#.*$", "", line).strip() for line in text.splitlines()]


def caddy_blocks(text: str, header: str) -> list[str]:
    """Bodies of the Caddyfile blocks whose opening line matches ``header``."""
    lines = caddy_code(text)
    found: list[str] = []
    for start, line in enumerate(lines):
        if re.fullmatch(header + r"\s*\{", line):
            depth, body = 1, []
            for inner in lines[start + 1 :]:
                depth += inner.count("{") - inner.count("}")
                if depth <= 0:
                    break
                body.append(inner)
            found.append("\n".join(body))
    return found


def section(markdown: str, title: str) -> str:
    """Text of a ``## title`` section of a Markdown document."""
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", markdown, re.M | re.S)
    assert match, title
    return match.group(1)


def test_caddy_runs_without_root_and_its_volumes_are_handed_over_first() -> None:
    all_services = services()
    caddy = all_services["caddy"]
    uid, _, gid = str(caddy["user"]).partition(":")
    assert int(uid) != 0 and int(gid) != 0
    # The image's caddy binary has the file capability cap_net_bind_service: the
    # kernel refuses to execute it unless it is in the bounding set.
    assert caddy["cap_drop"] == ["ALL"]
    assert caddy["cap_add"] == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in caddy["security_opt"]
    assert caddy["depends_on"]["caddy-init"]["condition"] == "service_completed_successfully"

    init = all_services["caddy-init"]
    assert init["image"] == caddy["image"]
    assert init["command"][:2] == ["chown", "-R"]
    assert f"{uid}:{gid}" in init["command"]
    assert {"/data", "/config"} <= set(init["command"])
    mounts = {volume.split(":")[1]: volume.split(":")[0] for volume in caddy["volumes"]}
    init_mounts = {volume.split(":")[1]: volume.split(":")[0] for volume in init["volumes"]}
    assert init_mounts == {"/data": mounts["/data"], "/config": mounts["/config"]}
    assert init["network_mode"] == "none"
    assert init["cap_drop"] == ["ALL"]
    assert set(init["cap_add"]) <= {"CHOWN", "DAC_READ_SEARCH"}
    assert init["restart"] == "no"


def test_app_container_has_room_for_connections_and_a_cpu_limit() -> None:
    app = services()["app"]
    nofile = app["ulimits"]["nofile"]
    assert nofile["soft"] >= 65536 and nofile["hard"] >= nofile["soft"]
    default = re.fullmatch(r"\$\{\w+:-([\d.]+)\}", str(app["cpus"]))
    cpus = float(default.group(1) if default else app["cpus"])
    assert 0 < cpus < 2  # leaves CPU for Caddy and SSH on the documented 2 vCPU VPS


def test_codex_state_lives_on_a_private_tmpfs() -> None:
    env = dict(re.findall(r"^\s+(AOS_CODEX_STATE_DIR)=(\S+)", read("Dockerfile"), re.M))
    state_dir = env["AOS_CODEX_STATE_DIR"]
    assert not state_dir.startswith(("/home/app", "/data"))  # never on a backed-up volume
    mounts = [m for m in services()["app"]["tmpfs"] if m.split(":")[0] == state_dir]
    assert len(mounts) == 1
    options = set(mounts[0].split(":", 1)[1].split(","))
    assert {"uid=10001", "gid=10001", "mode=0700"} <= options
    assert any(option.startswith("size=") for option in options)
    assert "exec" not in options


def read_timeout(limits: str) -> int:
    """Seconds of the ``read_timeout`` of a ``request_body`` block."""
    [seconds] = re.findall(r"^read_timeout (\d+)s$", limits, re.M)
    return int(seconds)


def test_caddy_caps_request_bodies_but_never_websockets() -> None:
    caddyfile = read("deploy/Caddyfile")
    # Every method that carries a body (but the upload of an attachment, see below).
    [body] = caddy_blocks(caddyfile, r"@body")
    outside = re.sub(r"^not \{$.*?^\}$", "", body, flags=re.M | re.S).split()
    assert outside[0] == "method" and set(outside[1:]) == BODY_METHODS

    [limits] = caddy_blocks(caddyfile, r"request_body @body")
    assert size_bytes(re.findall(r"max_size (\S+)", limits)[0]) <= MAX_BODY_BYTES
    assert read_timeout(limits) > BODY_TIMEOUT_SECONDS  # the app answers 408 first
    # An unmatched request_body would also catch a WebSocket (a GET, or a CONNECT
    # whose body is the socket over HTTP/2 and HTTP/3): capped and timed out, it breaks.
    assert caddy_blocks(caddyfile, r"request_body") == []
    proxies = caddy_blocks(caddyfile, r"reverse_proxy(\s+\S+)*")
    assert proxies
    for block in proxies:
        assert "request_body" not in block


def test_caddy_lets_an_attachment_upload_through_like_the_app() -> None:
    # PUT /api/attachments is the file itself: the 1 MiB cap refused every file over
    # 1 MiB with a 413 of Caddy's, and cut a slow upload at 30 s.
    caddyfile = read("deploy/Caddyfile")
    [upload] = caddy_blocks(caddyfile, r"@upload")
    assert sorted(upload.splitlines()) == ["method PUT", f"path {UPLOAD_PATH}"]
    # The only request @body leaves out, so the two limits never apply together.
    [body] = caddy_blocks(caddyfile, r"@body")
    [excluded] = caddy_blocks(body, r"not")
    assert sorted(excluded.splitlines()) == sorted(upload.splitlines())

    [limits] = caddy_blocks(caddyfile, r"request_body @upload")
    assert size_bytes(re.findall(r"max_size (\S+)", limits)[0]) == LARGE_BODY_PATHS[UPLOAD_PATH]
    assert read_timeout(limits) > UPLOAD_TIMEOUT_SECONDS  # the app answers 408 first


def test_the_docs_give_caddys_timeouts() -> None:
    caddyfile = read("deploy/Caddyfile")
    timeouts = {
        read_timeout(limits)
        for matcher in ("@body", "@upload")
        for limits in caddy_blocks(caddyfile, rf"request_body {matcher}")
    }
    assert len(timeouts) == 2
    for document in ("docs/ARCHITECTURE.md", "docs/DEPLOYMENT.md"):
        # "(Caddy cuts off at 30 s)", whatever the language of the document.
        said = {int(n) for n in re.findall(r"\(Caddy\b[^)]*?(\d+)[^)]*\)", read(document))}
        assert said == timeouts, document


def test_caddy_logs_keep_no_query_string() -> None:
    # An upload names its file in the query (PUT /api/attachments?name=...), and a search
    # sends its text (?q=...): neither the access log nor Caddy's own log, where a 502
    # names its request, may keep them.
    caddyfile = read("deploy/Caddyfile")
    [own] = caddy_blocks(caddyfile, r"log default")
    [access] = caddy_blocks(caddyfile, r"log")
    for log in (own, access):
        [fields] = caddy_blocks(log, r"fields")
        [(pattern, replacement)] = re.findall(r'^request>uri regexp "(.+)" "(.*)"$', fields, re.M)
        for uri in ("/api/attachments?name=informe%20m%C3%A8dic.pdf", "/api/conversations?q=x"):
            assert "?" not in re.sub(pattern, replacement, uri)
        assert re.sub(pattern, replacement, "/api/attachments") == "/api/attachments"


def test_caddy_streams_request_bodies_instead_of_buffering_them() -> None:
    # request_buffers kept up to its size per request in Caddy's 256 MiB container:
    # 150 stalled 1 MiB uploads got Caddy OOM-killed. The app bounds a slow body
    # itself (408 after BODY_TIMEOUT_SECONDS, then it closes the connection).
    code = "\n".join(caddy_code(read("deploy/Caddyfile")))
    assert not re.search(r"\b(request_buffers|buffer_requests)\b", code)


@pytest.mark.parametrize(
    ("document", "stale"),
    [
        # Codex still offers ChatGPT a code tool and sub-agent tools (ADR 0002, Nota).
        ("docs/ARCHITECTURE.md", "sense eines"),
        # agents.max_threads caps sub-agents running at once, not per call.
        ("docs/ARCHITECTURE.md", "un per crida"),
        ("docs/ARCHITECTURE.md", "one per call"),
        ("docs/DEPLOYMENT.md", "one per call"),
        ("docs/adr/0002-subscriptions-via-official-clis.md", "un subagent per crida"),
        ("docs/adr/0002-subscriptions-via-official-clis.md", "one subagent per call"),
        # Caddy streams request bodies (test_caddy_streams_request_bodies_...).
        ("docs/ARCHITECTURE.md", "llegeix sencer"),
        ("docs/ARCHITECTURE.md", "reads it whole"),
        ("docs/DEPLOYMENT.md", "reads it whole"),
        # The Codex logs are deleted before every start: a full tmpfs cannot stop it.
        ("docs/DEPLOYMENT.md", "failed to initialize sqlite state runtime"),
    ],
)
def test_docs_drop_stale_claims(document: str, stale: str) -> None:
    assert stale not in read(document)


def test_login_troubleshooting_covers_new_devices_during_an_attack() -> None:
    text = section(read("docs/DEPLOYMENT.md"), "Troubleshooting")
    login = text[text.index("**I can't log in**") :].split("\n\n")[0]
    assert "agentic-os reset-throttle" in login
    advice = next(line for line in login.splitlines() if "new device" in line and "attack" in line)
    assert "ALLOWED_IPS" in advice and "VPN" in advice


def heading_anchor(heading: str) -> str:
    """GitHub's anchor for a Markdown heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


@pytest.mark.parametrize("document", ["docs/DEPLOYMENT.md", "docs/ARCHITECTURE.md"])
def test_links_to_sections_resolve(document: str) -> None:
    text = read(document)
    anchors = {heading_anchor(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, target


@pytest.mark.parametrize("script", ["deploy/harden.sh", "deploy/backup.sh", "deploy/restore.sh"])
def test_deploy_scripts_point_to_existing_sections(script: str) -> None:
    headings = set(re.findall(r"^## (.+)$", read("docs/DEPLOYMENT.md"), re.M))
    # section "Updating", or section \"Updating\" inside a string of the script
    named = re.findall(r'section \\?"([^"\\]+)\\?"', read(script))
    assert named
    for name in named:
        assert name in headings, name


def test_ssh_hardening_keeps_the_default_max_auth_tries() -> None:
    # Each key an SSH agent offers counts as a try: a low value locks out owners
    # whose agent holds several keys. Passwords are off, so there is nothing to guess.
    assert "MaxAuthTries" not in read("deploy/harden.sh")


def test_update_steps_refresh_base_images_and_the_host() -> None:
    assert services()["app"]["build"]["pull"] is True
    update = section(read("docs/DEPLOYMENT.md"), "Updating")
    assert "docker compose build --pull" in update
    assert "apt-get upgrade" in update  # Docker Engine, containerd and runc


def test_update_steps_apply_a_new_caddyfile() -> None:
    # deploy/Caddyfile is bind-mounted as a single file: `git pull` replaces it with
    # a new inode that the running container never sees, and `docker compose up -d`
    # keeps a container whose service definition did not change.
    assert "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro" in services()["caddy"]["volumes"]
    [steps] = bash_blocks(section(read("docs/DEPLOYMENT.md"), "Updating"))[:1]
    commands = steps.splitlines()
    assert commands.index("docker compose restart caddy") > commands.index("git pull")


def test_backups_are_written_outside_the_repository() -> None:
    backups = section(read("docs/DEPLOYMENT.md"), "Backups")
    assert "/var/backups/claudegpt" in backups
    # Neither inside the clone (/opt/claudegpt) nor relative to it.
    assert not re.search(r"\$PWD/backups|/opt/claudegpt/backups|(?<![\w/])backups/", backups)


def bash_blocks(markdown: str) -> list[str]:
    return re.findall(r"^```bash\n(.*?)^```", markdown, re.M | re.S)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "path", ["backups/claudegpt-2026-09-27.tar.gz", "env-2026-09-27", "backups/env-2026-09-27"]
)
def test_leftover_backups_in_the_clone_are_ignored_by_git(path: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path], cwd=ROOT, check=False
    )
    assert result.returncode == 0, f"{path} is not ignored"


# --------------------------------------------------------- backups and restores
#
# The "Backups" section of docs/DEPLOYMENT.md runs deploy/backup.sh
# and deploy/restore.sh (audit items 2 and 16, and N8, N9 and N23). Where they can,
# the tests run the guide's own commands, so they also cover what the owner
# pastes; the rest call the scripts directly.

LINUX_SCRIPTS = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None or shutil.which("tar") is None,
    reason="the scripts target Linux servers (bash, GNU coreutils, util-linux and tar)",
)

CURRENT_ENV = "DOMAIN=ia.example.com\nACME_EMAIL=tu@example.com\nAOS_CLAUDE_MODE=cli\n"
BACKUP_ENV = "DOMAIN=backup.example.com\nACME_EMAIL=tu@example.com\nAOS_CLAUDE_MODE=api\n"
OLD_VOLUMES = ("claudegpt_app_data", "claudegpt_app_home")
# A well-formed age recipient (bech32 alphabet): the stand-in of age rejects others.
AGE_KEY = "age1" + ("qpzry9x8gf2tvdw0s3jn54khce6mua7l" * 2)[:58]
CURRENT_USER = pwd.getpwuid(os.getuid()).pw_name

# How a stand-in blocks a call until the test interrupts it: it creates the marker $1
# that the test waits for, and sleeps 60 s. A signal the call does not ignore ends it
# as it ends a program that does not handle it, even one sent the moment the marker
# appears: with `touch; exec sleep 60`, bash could take that signal between the two
# and lose it in the exec, and the call then blocked for the whole 60 s.
BLOCK = r"""block() {
  exec "${PYTHON:-python3}" -c '
import signal, sys, time
for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
    if signal.getsignal(sig) is not signal.SIG_IGN:
        signal.signal(sig, signal.SIG_DFL)
open(sys.argv[1], "w").close()
time.sleep(60)
' "$1"
}
"""

# Stand-in for the Docker CLI with just the calls of the scripts and of the guide.
DOCKER_STUB = (
    r"""#!/usr/bin/env bash
# It never talks to a daemon:
# - named volumes are directories under $SANDBOX/volumes (created on first use,
#   as Docker does, and then without labels), and their labels are files under
#   $SANDBOX/volume-labels;
# - the app container is $SANDBOX/app.state (running, stopped or unhealthy) and
#   $SANDBOX/app.mounts (the data and home volumes it was created with);
# - `docker run` runs tar and python3 for real, on copies of the mounted volumes
#   in a temporary root (never the host's /data or /home/app), and copies the
#   writable ones back; any other command is only logged.
# Every call goes to $LOG. $FAIL lists the points that fail (tar, extract, check,
# config, stop, start, up, volume-create) and $HANG the ones whose first call
# blocks until the test interrupts it. $PRUNE lists volumes that a
# `docker volume prune` in another terminal deletes while the app is stopping.
set -u
"""
    + BLOCK
    + r"""S=${SANDBOX:?}
CALL="docker $*"
printf '%s\n' "$CALL" >> "$LOG"
log() { printf '%s\n' "$*" >> "$LOG"; }
unsupported() { echo "docker stub: unsupported: $CALL" >&2; exit 99; }
failing() { [[ " ${FAIL:-} " == *" $1 "* ]]; }
hang() {
  if [[ " ${HANG:-} " == *" $1 "* ]] && [ ! -e "$S/hung.$1" ]; then block "$S/hung.$1"; fi
}
volume_dir() { printf '%s/volumes/%s' "$S" "$1"; }
labels_file() { printf '%s/volume-labels/%s' "$S" "$1"; }
new_volume() {  # $1, empty, with the labels $2...
  local name=$1
  shift
  mkdir -p "$(volume_dir "$name")" "$S/volume-labels"
  printf '%s\n' "$@" > "$(labels_file "$name")"
}
drop_volume() { rm -rf -- "$(volume_dir "$1")" "$(labels_file "$1")"; }
image_exists() { [ "$1" = claudegpt-os:latest ] && [ ! -e "$S/image-missing" ]; }
app_state() { cat "$S/app.state" 2> /dev/null || echo none; }

env_file=.env
env_value() {  # $1 as docker compose reads it: the environment first, then the env file
  local value=${!1:-}
  if [ -z "$value" ] && [ -f "$env_file" ]; then
    value=$(sed -nE "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=[[:space:]]*//p" \
      "$env_file" | tail -n 1)
    value=${value%%#*}
    value=${value%"${value##*[![:space:]]}"}
    value=${value#[\"\']}
    value=${value%[\"\']}
  fi
  printf '%s' "$value"
}

compose() {
  while [ "${1:-}" = --env-file ]; do env_file=$2; shift 2; done
  local command=${1:-}
  shift || true
  case $command in
    config)
      [ "$*" = --quiet ] || unsupported
      if failing config; then echo "stub: invalid project" >&2; exit 1; fi
      [ -f "$env_file" ] || { echo "env file $env_file not found" >&2; exit 1; }
      local var
      for var in DOMAIN ACME_EMAIL; do
        if [ -z "$(env_value "$var")" ]; then
          echo "required variable $var is missing a value" >&2
          exit 1
        fi
      done ;;
    stop)
      [ "$*" = app ] || unsupported
      hang stop
      if failing stop; then echo "stub: cannot stop the app" >&2; exit 1; fi
      local pruned
      for pruned in ${PRUNE:-}; do
        drop_volume "$pruned"
        log "  (daemon) pruned volume $pruned"
      done
      [ "$(app_state)" = none ] || echo stopped > "$S/app.state" ;;
    start)
      [ "$*" = app ] || unsupported
      if failing start; then echo "stub: cannot start the app" >&2; exit 1; fi
      [ "$(app_state)" != none ] || { echo "stub: no container to start" >&2; exit 1; }
      echo running > "$S/app.state" ;;
    up)
      local arg data home volume
      for arg in "$@"; do
        case $arg in -d | --wait | --wait-timeout | [0-9]* | --force-recreate | app) ;;
          *) unsupported ;;
        esac
      done
      data=$(env_value APP_DATA_VOLUME)
      data=${data:-claudegpt_app_data}
      home=$(env_value APP_HOME_VOLUME)
      home=${home:-claudegpt_app_home}
      for volume in "$data" "$home"; do
        if [ ! -d "$(volume_dir "$volume")" ]; then  # empty, and not external
          new_volume "$volume" com.docker.compose.project=claudegpt
          log "  (compose) created volume $volume"
        fi
      done
      echo "$data $home" > "$S/app.mounts"
      log "  (compose) app created with $data $home"
      hang up
      if failing up && [[ $data == *_r* ]]; then
        echo unhealthy > "$S/app.state"
        echo "container claudegpt-app-1 is unhealthy" >&2
        exit 1
      fi
      echo running > "$S/app.state" ;;
    ps)
      [ "$*" = "-q app" ] || unsupported
      case $(app_state) in running | unhealthy) echo claudegpt-app-1 ;; esac ;;
    exec)
      [ "${1:-}" = -T ] && [ "${2:-}" = app ] || unsupported
      if [ "$(app_state)" != running ] || failing health; then
        echo "stub: the app does not answer" >&2
        exit 1
      fi ;;
    *) unsupported ;;
  esac
}

volume() {
  local command=${1:-} name labels=() format=""
  shift || true
  case $command in
    create)
      name=""
      while [ $# -gt 0 ]; do
        case $1 in --label) labels+=("$2"); shift 2 ;; -*) unsupported ;; *) name=$1; shift ;; esac
      done
      [[ $name =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]] || { echo "invalid name: $name" >&2; exit 1; }
      if failing volume-create; then echo "stub: cannot create $name" >&2; exit 1; fi
      new_volume "$name" "${labels[@]}"
      echo "$name" ;;
    inspect)
      if [ "${1:-}" = --format ]; then format=$2; shift 2; fi
      for name in "$@"; do
        if [ ! -d "$(volume_dir "$name")" ]; then
          echo "Error response from daemon: get $name: no such volume" >&2
          exit 1
        fi
      done
      case $format in
        "") echo '[]' ;;
        '{{index .Labels "claudegpt.restore"}}')  # an empty line without that label
          [ $# -eq 1 ] || unsupported
          name=$(sed -n 's/^claudegpt\.restore=//p' "$(labels_file "$1")" 2> /dev/null)
          printf '%s\n' "$name" ;;
        *) unsupported ;;
      esac ;;
    rm)
      for name in "$@"; do
        if [ ! -d "$(volume_dir "$name")" ]; then
          echo "Error response from daemon: get $name: no such volume" >&2
          exit 1
        fi
        # A container keeps its volumes, running or stopped, until it is removed.
        if [ -f "$S/app.mounts" ] && [[ " $(cat "$S/app.mounts") " == *" $name "* ]]; then
          echo "Error response from daemon: remove $name: volume is in use" >&2
          exit 1
        fi
        drop_volume "$name"
      done ;;
    *) unsupported ;;
  esac
}

inspect() {  # docker inspect --format TEMPLATE claudegpt-app-1: the app's mounts
  [ "${1:-}" = --format ] && [ "${3:-}" = claudegpt-app-1 ] || unsupported
  local data home
  read -r data home < "$S/app.mounts" || exit 1
  printf '%s:/data %s:/home/app :/tmp :/run/codex-state \n' "$data" "$home"
}

run() {
  local interactive=0 specs=()
  while [ $# -gt 0 ]; do
    case $1 in
      --rm | --read-only) shift ;;
      -i) interactive=1; shift ;;
      --network | --tmpfs | --log-driver) shift 2 ;;
      -v) specs+=("$2"); shift 2 ;;
      -*) unsupported ;;
      *) break ;;
    esac
  done
  local image=${1:?}
  shift
  if ! image_exists "$image"; then
    echo "Unable to find image '$image' locally" >&2
    echo "docker: Error response from daemon: pull access denied for ${image%%:*}" >&2
    exit 125
  fi
  [ "$interactive" = 1 ] || exec < /dev/null
  local root spec name target mode names=() targets=() modes=()
  root=$(mktemp -d "$S/container.XXXXXX")
  for spec in "${specs[@]}"; do
    name=${spec%%:*} target=${spec#*:} mode=rw
    case $target in *:ro) target=${target%:ro} mode=ro ;; esac
    if [ ! -d "$(volume_dir "$name")" ]; then
      new_volume "$name"
      log "  (daemon) created volume $name"
    fi
    mkdir -p "$root${target%/*}"
    cp -a "$(volume_dir "$name")" "$root$target"
    names+=("$name") targets+=("$target") modes+=("$mode")
  done
  # The command's paths, moved into the temporary root; nothing else on the host.
  local args=() arg i
  for arg in "$@"; do
    if [ "$arg" = / ]; then
      arg=$root
    else
      for i in "${!targets[@]}"; do
        case $arg in "${targets[$i]}" | "${targets[$i]}"/*) arg=$root$arg ;; esac
      done
    fi
    if [[ $arg == /* && $arg != "$root"* ]]; then
      echo "docker stub: refusing the host path $arg" >&2
      exit 99
    fi
    args+=("$arg")
  done
  local status=0
  case ${1:-} in
    tar)
      case ${2:-} in
        c* | -c*)
          if [[ " ${HANG:-} " == *" tar "* ]]; then printf 'partial archive'; fi
          hang tar
          if failing tar; then
            printf 'partial archive'
            echo "tar: data/agentic_os.sqlite3: Cannot open: Permission denied" >&2
            status=2
          else
            "${args[@]}" || status=$?
          fi ;;
        x* | -x*)
          hang extract
          "${args[@]}" || status=$?
          if failing extract; then  # e.g. the disk filled up half-way
            if [ -f "$root/data/agentic_os.sqlite3" ]; then : > "$root/data/agentic_os.sqlite3"; fi
            echo "tar: data/agentic_os.sqlite3: Wrote only 4096 of 10240 bytes" >&2
            status=2
          fi ;;
        *) unsupported ;;
      esac ;;
    python3)
      hang check
      if failing check; then
        echo "stub: integrity check failed" >&2
        status=1
      else
        "${PYTHON:-python3}" "${args[@]:1}" || status=$?
      fi ;;
    *)  # anything else would run on the host: pretend
      log "  (stub) not run: $*"
      [ "$interactive" = 0 ] || cat > /dev/null
      if failing extract; then status=2; fi ;;
  esac
  for i in "${!targets[@]}"; do
    if [ "${modes[$i]}" = rw ]; then
      rm -rf -- "$(volume_dir "${names[$i]}")"
      cp -a "$root${targets[$i]}" "$(volume_dir "${names[$i]}")"
    fi
  done
  rm -rf -- "$root"
  log "  (daemon) container exited with status $status"
  exit "$status"
}

case ${1:-} in
  compose) shift; compose "$@" ;;
  run) shift; run "$@" ;;
  volume) shift; volume "$@" ;;
  inspect) shift; inspect "$@" ;;
  image)
    [ "${2:-}" = inspect ] || unsupported
    if image_exists "${3:-}"; then
      echo '[]'
    else
      echo "Error response from daemon: No such image: ${3:-}" >&2
      exit 1
    fi ;;
  info)
    [ "${2:-}" = --format ] || unsupported
    mkdir -p "$S/docker-root"
    echo "$S/docker-root" ;;
  *) unsupported ;;
esac
"""
)

AGE_STUB = r"""#!/usr/bin/env bash
# Stand-in for age: checks the shape of the recipient, as age does, and "encrypts"
# by prefixing the input, streaming it. $FAIL=age makes it fail half-way through a
# non-empty input. With -d (the owner's computer) it takes the prefix off a file.
set -u
printf 'age %s\n' "$*" >> "$LOG"
recipient="" output="" input="" identity="" decrypt=0
while [ $# -gt 0 ]; do
  case $1 in
    -r) recipient=$2; shift 2 ;;
    -o) output=$2; shift 2 ;;
    -d) decrypt=1; shift ;;
    -i) identity=$2; shift 2 ;;
    -*) echo "age stub: unsupported option $1" >&2; exit 99 ;;
    *) input=$1; shift ;;
  esac
done
if [ "$decrypt" = 1 ]; then
  [ -f "$identity" ] || { echo "age: error: reading $identity: no such file" >&2; exit 1; }
  [ -f "$input" ] || { echo "age stub: decrypts files only" >&2; exit 99; }
  [ -z "$output" ] || exec > "$output"
  if [ "$(head -c 10 -- "$input")" != encrypted: ]; then
    echo "age: error: failed to decrypt the file" >&2
    exit 1
  fi
  exec tail -c +11 -- "$input"
fi
if ! [[ $recipient =~ ^age1[02-9ac-hj-np-z]{58}$ ]]; then
  echo "age: error: malformed recipient \"$recipient\"" >&2
  exit 1
fi
[ -z "$input" ] || exec < "$input"
[ -z "$output" ] || exec > "$output"
if [[ " ${FAIL:-} " == *" age "* ]]; then
  data=$(mktemp "$SANDBOX/age.XXXXXX")
  cat > "$data"
  if [ -s "$data" ]; then
    printf 'enc'
    rm -f "$data"
    echo "age: error: failed to write the output" >&2
    exit 1
  fi
  rm -f "$data"
fi
printf 'encrypted:'
exec cat
"""

# The real date, at $FAKE_DATE when it is set.
DATE_STUB = r"""#!/usr/bin/env bash
export PATH=$REAL_PATH
if [ -n "${FAKE_DATE:-}" ]; then exec date -d "$FAKE_DATE" "$@"; fi
exec date "$@"
"""

# The real df, or a nearly full disk when $DF_AVAIL_KB is set.
DF_STUB = r"""#!/usr/bin/env bash
if [ -n "${DF_AVAIL_KB:-}" ]; then
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
  printf 'stub 1048576 1047552 %s 100%% /\n' "$DF_AVAIL_KB"
  exit 0
fi
export PATH=$REAL_PATH
exec df "$@"
"""

# The real NAME, except that its first call on .env.next blocks when $HANG names it.
BLOCKING_STUB = (
    "#!/usr/bin/env bash\n"
    + BLOCK
    + r"""for arg; do
  if [[ $arg == *.env.next && " ${HANG:-} " == *" NAME "* && ! -e $SANDBOX/hung.NAME ]]; then
    block "$SANDBOX/hung.NAME"
  fi
done
export PATH=$REAL_PATH
exec NAME "$@"
"""
)

STUBS = {
    "docker": DOCKER_STUB,
    "age": AGE_STUB,
    "date": DATE_STUB,
    "df": DF_STUB,
    "sync": BLOCKING_STUB.replace("NAME", "sync"),
    "mv": BLOCKING_STUB.replace("NAME", "mv"),
}

# The real rm, except that when it deletes the restore's temporary directory (the
# first thing its clean-up does) it waits there until the test has sent a second
# signal ($SANDBOX/go.rm). A signal that is not ignored ends that wait.
CLEANUP_RM_STUB = r"""#!/usr/bin/env bash
for arg; do
  case $arg in
    "$TMPDIR"/tmp.*)
      touch "$SANDBOX/hung.rm"
      for _ in $(seq 1 1500); do
        [ ! -e "$SANDBOX/go.rm" ] || break
        sleep 0.02
      done ;;
  esac
done
export PATH=$REAL_PATH
exec rm "$@"
"""

# The real sync, except that it blocks once on the journal's directory just after
# the journal has been told R5 (the restore has worked).
R5_SYNC_STUB = (
    "#!/usr/bin/env bash\n"
    + BLOCK
    + r"""for arg; do
  if [ "$arg" = "$(dirname -- "$RESTORE_STATE")" ] && [ ! -e "$SANDBOX/hung.r5" ] &&
    grep -qx step=R5 "$RESTORE_STATE" 2> /dev/null; then
    block "$SANDBOX/hung.r5"
  fi
done
export PATH=$REAL_PATH
exec sync "$@"
"""
)

# Stand-ins for the owner's computer: scp "downloads" from $SERVER, the stand-in of
# /var/backups/claudegpt on the VPS; ssh only records the remote command.
SCP_STUB = r"""#!/usr/bin/env bash
set -u
printf 'scp %s\n' "$*" >> "$LOG"
if [[ " ${FAIL:-} " == *" scp "* ]]; then echo "scp: Connection closed" >&2; exit 1; fi
args=() recursive=0
for arg; do case $arg in -r) recursive=1 ;; -*) ;; *) args+=("$arg") ;; esac; done
target=${args[-1]}
unset 'args[-1]'
status=0
for source in "${args[@]}"; do
  path=${source#*:}
  if [ "$path" = /var/backups/claudegpt ] && [ "$recursive" = 1 ]; then
    cp -r "$SERVER" "$target"
    continue
  fi
  pattern=${path#/var/backups/claudegpt/}
  if [ "$pattern" = "$path" ] || [[ $pattern == */* ]]; then
    echo "scp stub: unexpected path $path" >&2
    exit 99
  fi
  found=0
  for file in "$SERVER"/$pattern; do
    [ -f "$file" ] || continue
    cp "$file" "$target"
    found=1
  done
  if [ "$found" = 0 ]; then echo "scp: $path: No such file or directory" >&2; status=1; fi
done
exit "$status"
"""

SSH_STUB = r"""#!/usr/bin/env bash
printf 'ssh %s\n' "$*" >> "$LOG"
"""


def make_database(path: Path, marker: str, *, owner: bool = True) -> None:
    """A small SQLite database: ``marker`` tells whose data it is."""
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if owner:
            db.execute("CREATE TABLE owner (id INTEGER PRIMARY KEY, password_hash TEXT)")
        db.execute("INSERT INTO settings VALUES ('marker', ?)", (marker,))
        db.commit()


def make_archive(path: Path, marker: str, fault: str = "") -> None:
    """A tar.gz with the layout of deploy/backup.sh (``data`` and ``home/app``),
    made with tar like the real one. ``fault`` breaks it on purpose."""
    source = path.parent / f".source-{path.name}"
    data, home = source / "data", source / "home" / "app"
    data.mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text(marker)
    if fault == "corrupt":
        (data / "agentic_os.sqlite3").write_bytes(b"not a database " * 512)
    elif fault != "not-a-backup":
        make_database(data / "agentic_os.sqlite3", marker, owner=fault != "no-owner")
    subprocess.run(["tar", "czf", str(path), "-C", str(source), "data", "home/app"], check=True)
    shutil.rmtree(source)
    if fault == "damaged":  # an interrupted upload
        path.write_bytes(path.read_bytes()[:-20])
    elif fault == "encrypted":  # uploaded without decrypting it first
        path.write_bytes(b"age-encryption.org/v1\n-> X25519 stub\n" + path.read_bytes())


def wait_until(condition: Callable[[], bool], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


class Sandbox:
    """A fake /opt/claudegpt (the scripts and .env), the app with its two volumes and
    a database, the backup directory and the stand-ins, all inside ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.project = root / "project"
        self.bin = root / "bin"
        self.backups = root / "backups"
        self.uploads = root / "uploads"
        self.state = root / "state" / "restore.state"
        self.log = root / "commands.log"
        for directory in (self.project / "deploy", self.bin, self.uploads, root / "tmp"):
            directory.mkdir(parents=True)
        for script in (ROOT / "deploy").glob("*.sh"):
            shutil.copy2(script, self.project / "deploy" / script.name)
        (self.project / ".env").write_text(CURRENT_ENV)
        (self.project / ".env").chmod(0o600)
        for name, source in STUBS.items():
            (self.bin / name).write_text(source)
            (self.bin / name).chmod(0o755)
        self.log.touch()
        self.fill(OLD_VOLUMES, "CURRENT")
        (root / "app.state").write_text("running\n")
        (root / "app.mounts").write_text(" ".join(OLD_VOLUMES) + "\n")

    def volume(self, name: str) -> Path:
        return self.root / "volumes" / name

    def fill(self, volumes: tuple[str, str], marker: str) -> None:
        data, home = (self.volume(name) for name in volumes)
        data.mkdir(parents=True)
        (home / ".claude").mkdir(parents=True)
        make_database(data / "agentic_os.sqlite3", marker)
        (home / ".claude" / ".credentials.json").write_text(marker)

    def marker(self, volume: str) -> str:
        """Whose data a data volume holds (the ``marker`` of its database)."""
        uri = (self.volume(volume) / "agentic_os.sqlite3").as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'marker'").fetchone()
        return str(row[0])

    def volumes(self) -> list[str]:
        return sorted(path.name for path in (self.root / "volumes").iterdir())

    def prune(self, *names: str) -> None:
        """What `docker volume prune -a` does to volumes no container uses."""
        for name in names:
            shutil.rmtree(self.volume(name))
            (self.root / "volume-labels" / name).unlink(missing_ok=True)

    def app(self) -> tuple[str, tuple[str, ...]]:
        """The app container: its state and the volumes it runs with."""
        state = (self.root / "app.state").read_text().strip()
        return state, tuple((self.root / "app.mounts").read_text().split())

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if not line.startswith(" ")]

    def journal(self) -> dict[str, str]:
        lines = self.state.read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and line[0] != "#")

    def backup_files(self) -> dict[str, bytes]:
        """Every file of the backup directory, hidden ones (temporaries) included."""
        if not self.backups.exists():
            return {}
        return {path.name: path.read_bytes() for path in sorted(self.backups.iterdir())}

    def upload(self, marker: str = "BACKUP", fault: str = "") -> tuple[Path, Path]:
        """A decrypted backup and its .env, uploaded to the owner's directory."""
        archive = self.uploads / "claudegpt-2026-09-27_223000.tar.gz"
        env_file = self.uploads / "env-2026-09-27_223000"
        make_archive(archive, marker, fault)
        env_file.write_text(BACKUP_ENV)
        return archive, env_file

    def env(self, **extra: str) -> dict[str, str]:
        path = os.environ.get("PATH", "/usr/bin:/bin")
        return {
            "PATH": f"{self.bin}:{path}",
            "REAL_PATH": path,
            "HOME": str(self.root),
            "LANG": "C.UTF-8",
            "SANDBOX": str(self.root),
            "TMPDIR": str(self.root / "tmp"),
            "LOG": str(self.log),
            "PYTHON": sys.executable,
            "SUDO_USER": CURRENT_USER,
            # Never the real /var/backups or /var/lib.
            "BACKUP_DIR": str(self.backups),
            "RESTORE_STATE": str(self.state),
            **extra,
        }

    def bash(self, script: str, *, stdin: str = "", **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            cwd=self.root,
            env=self.env(**env),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def command(self, name: str, *args: str) -> str:
        return shlex.join(["bash", str(self.project / "deploy" / f"{name}.sh"), *args])

    def script(
        self, name: str, *args: str, stdin: str = "", **env: str
    ) -> subprocess.CompletedProcess[str]:
        return self.bash(self.command(name, *args), stdin=stdin, **env)

    def interrupt(self, script: str, point: str, sig: int, *, again: str = "", **env: str) -> str:
        """Runs ``script`` until the stand-in that blocks at ``point`` is reached and
        sends ``sig`` to its whole process group, as a terminal does on Ctrl+C or a
        hang-up (SIGKILL: kill -9 or a power cut). With ``again``, it sends ``sig`` a
        second time when the stand-in that waits at ``again`` is reached, and then
        lets that one go on. Returns what it printed."""
        output = self.root / f"interrupted-{point}.out"
        with output.open("wb") as sink:
            process = subprocess.Popen(
                ["bash", "--noprofile", "--norc", "-c", script],
                cwd=self.root,
                env=self.env(HANG=point, **env),
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        try:
            hung = self.root / f"hung.{point}"
            wait_until(lambda: hung.exists() or process.poll() is not None)
            assert process.poll() is None, output.read_text()
            os.killpg(process.pid, sig)
            if again:
                waiting = self.root / f"hung.{again}"
                wait_until(lambda: waiting.exists() or process.poll() is not None)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, sig)
                (self.root / f"go.{again}").touch()
            process.wait(timeout=60)
            # A script whose parent shell died goes on cleaning up on its own.
            wait_until(lambda: not group_alive(process.pid))
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=60)
        return output.read_text()

    def assert_before_restore(
        self, archive: Path, env_file: Path, kept: tuple[str, ...] = ()
    ) -> None:
        """The state before any restore: the old .env and volumes, the app running
        with them, the uploaded files still there and no journal. ``kept``: restored
        volumes that are left too, because the app has run with them."""
        assert (self.project / ".env").read_text() == CURRENT_ENV
        assert not (self.project / ".env.prev").exists()
        assert not (self.project / ".env.next").exists()
        assert self.app() == ("running", OLD_VOLUMES)
        assert self.volumes() == sorted((*OLD_VOLUMES, *kept))
        assert self.marker(OLD_VOLUMES[0]) == "CURRENT"
        assert archive.exists() and env_file.exists()
        assert not self.state.exists()

    def assert_restored(self, archive: Path, env_file: Path) -> tuple[str, str]:
        """A finished restore, before --finalize: the app runs with the new volumes
        and the uploaded .env; the old volumes, .env.prev and the uploads are kept."""
        journal = self.journal()
        assert (journal["step"], journal["status"]) == ("R5", "done")
        new = (journal["new_data_volume"], journal["new_home_volume"])
        assert self.app() == ("running", new)
        env = (self.project / ".env").read_text()
        assert env.startswith(BACKUP_ENV)
        assert [line for line in env.splitlines() if line.startswith("APP_")] == [
            f"APP_DATA_VOLUME={new[0]}",
            f"APP_HOME_VOLUME={new[1]}",
        ]
        assert stat.S_IMODE((self.project / ".env").stat().st_mode) == 0o600
        assert (self.project / ".env.prev").read_text() == CURRENT_ENV
        assert not (self.project / ".env.next").exists()
        assert self.marker(new[0]) == "BACKUP"
        assert (self.volume(new[1]) / ".claude" / ".credentials.json").read_text() == "BACKUP"
        assert self.marker(OLD_VOLUMES[0]) == "CURRENT"
        assert archive.exists() and env_file.exists()
        return new


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


def guide_block(marker: str) -> str:
    """The bash block of "Backups" that contains ``marker``."""
    backups = section(read("docs/DEPLOYMENT.md"), "Backups")
    [block] = [block for block in bash_blocks(backups) if marker in block]
    return block


def guide_backup(sandbox: Sandbox, key: str = AGE_KEY) -> str:
    """The guide's backup commands, run in the sandbox with ``key``."""
    block = guide_block("age1...")
    assert "/opt/claudegpt" in block
    script = (
        block.replace("/opt/claudegpt", str(sandbox.project))
        .replace(BACKUP_DIR, str(sandbox.backups))
        .replace("age1...", key)
    )
    assert "/var/" not in script
    return script


def guide_restore(sandbox: Sandbox) -> tuple[str, Path, Path]:
    """The guide's restore commands, run in the sandbox, and the uploaded files
    they expect."""
    block = guide_block(RESTORE_DIR)
    assert "/opt/claudegpt" in block
    [archive] = set(re.findall(r"claudegpt-[\w-]+\.tar\.gz\b", block))
    [env_file] = set(re.findall(r"\benv-[\w-]+", block))
    script = block.replace("/opt/claudegpt", str(sandbox.project)).replace(
        RESTORE_DIR, str(sandbox.uploads)
    )
    assert "/var/" not in script and RESTORE_DIR not in script
    return script, sandbox.uploads / archive, sandbox.uploads / env_file


# ------------------------------------------------------------ the guide itself


def test_backup_steps_run_the_scripts_and_leave_the_owners_shell_alone() -> None:
    blocks = bash_blocks(section(read("docs/DEPLOYMENT.md"), "Backups"))
    assert any("bash deploy/backup.sh" in block for block in blocks)
    assert any("bash deploy/restore.sh" in block for block in blocks)
    for block in blocks:
        # Pasted into the owner's root shell: nothing that outlives the block
        # (umask, shell options: N8) and no step that would need a trap (N9).
        assert not re.search(r"^\s*(umask|set\s|shopt\s)", block, re.M), block
        assert "docker compose stop" not in block and "docker run" not in block, block


def test_the_guide_explains_every_restore_step_and_command() -> None:
    backups = section(read("docs/DEPLOYMENT.md"), "Backups")
    restore = read("deploy/restore.sh")
    for step in ("R0", "R1", "R2", "R3", "R4", "R5"):
        assert step in backups and step in restore, step
    for option in ("--status", "--resume", "--undo", "--finalize"):
        assert f"bash deploy/restore.sh {option}" in backups, option
        assert option in restore, option
    assert "--unencrypted" in backups and "--unencrypted" in read("deploy/backup.sh")
    # A dropped SSH session should not stop a long backup or restore; if it does,
    # the restore's messages go to a log next to its journal.
    assert "tmux" in backups
    assert "/var/lib/claudegpt/restore.state.log" in backups
    # --undo keeps the restored volumes once the app has run with them.
    [undo] = [line for line in backups.splitlines() if "| `bash deploy/restore.sh --undo`" in line]
    assert "are kept" in undo


def test_ci_shellchecks_every_deploy_script() -> None:
    workflow = yaml.safe_load(read(".github/workflows/ci.yml"))
    [command] = [
        str(step["run"])
        for step in workflow["jobs"]["deploy"]["steps"]
        if "shellcheck" in str(step.get("run", ""))
    ]
    for script in sorted((ROOT / "deploy").glob("*.sh")):
        assert f"deploy/{script.name}" in command, script.name
    # The runner's own shellcheck is older than a local one and warns about other
    # things: a pinned version gives the same verdict everywhere.
    assert re.search(r"--from shellcheck-py==[\d.]+ shellcheck ", command), command


def test_the_data_volumes_are_named_from_env_with_the_existing_names_as_defaults() -> None:
    compose = yaml.safe_load(read("docker-compose.yml"))
    # Existing installs have the volumes of project "claudegpt": claudegpt_app_data
    # and claudegpt_app_home. The defaults keep exactly those names.
    assert compose["name"] == "claudegpt"
    volumes = compose["volumes"]
    assert volumes["app_data"] == {"name": "${APP_DATA_VOLUME:-claudegpt_app_data}"}
    assert volumes["app_home"] == {"name": "${APP_HOME_VOLUME:-claudegpt_app_home}"}
    assert not volumes["caddy_data"] and not volumes["caddy_config"]  # implicit names
    app = services()["app"]
    assert "app_data:/data" in app["volumes"][0] and "app_home:/home/app" in app["volumes"][1]


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")
def test_compose_resolves_the_volume_names_from_env(tmp_path: Path) -> None:
    """`docker compose config` needs no daemon: the names it resolves are the ones
    `docker compose up` would use."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("APP_", "COMPOSE_"))}
    if subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False
    ).returncode:
        pytest.skip("needs the docker compose plugin")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path)
    example = read(".env.example")

    def names(env: str) -> tuple[str, str]:
        (tmp_path / ".env").write_text(env)
        rendered = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        volumes = json.loads(rendered)["volumes"]
        return volumes["app_data"]["name"], volumes["app_home"]["name"]

    assert names(example) == OLD_VOLUMES
    restored = ("claudegpt_app_data_r20260928-101500", "claudegpt_app_home_r20260928-101500")
    switched = f"{example}APP_DATA_VOLUME={restored[0]}\nAPP_HOME_VOLUME={restored[1]}\n"
    assert names(switched) == restored


def test_env_example_documents_the_volume_names_but_leaves_them_unset() -> None:
    example = read(".env.example")
    for name in ("APP_DATA_VOLUME", "APP_HOME_VOLUME"):
        assert re.search(rf"^# {name}=", example, re.M), name
        assert not re.search(rf"^\s*{name}\s*=", example, re.M), name
    assert "deploy/restore.sh" in example


def remote_deletions(log: Path, server: Path) -> set[str]:
    """Carries out on ``server`` the `rm` commands that the stand-in of ssh got."""
    deleted: set[str] = set()
    for line in log.read_text().splitlines():
        if not line.startswith("ssh "):
            continue
        command = shlex.split(line.split(" ", 2)[2])
        assert command[0] == "rm", line
        for pattern in (word for word in command[1:] if not word.startswith("-")):
            assert pattern.startswith(BACKUP_DIR + "/"), line
            for match in glob.glob(str(server / pattern.removeprefix(BACKUP_DIR + "/"))):
                Path(match).unlink()
                deleted.add(Path(match).name)
    return deleted


@LINUX_SCRIPTS
@pytest.mark.parametrize("fault", ["", "scp", "damaged"])
def test_download_deletes_only_the_backup_it_copied(tmp_path: Path, fault: str) -> None:
    """N23: a failed scp used to delete every backup on the server. The server copy
    goes only once the download is complete and decrypts and reads to the end."""
    server, laptop, stubs = tmp_path / "server", tmp_path / "laptop", tmp_path / "bin"
    for directory in (server, laptop, stubs):
        directory.mkdir()
    # The backup to download, an older one and one made in the same second.
    stamp = "2026-09-28_101500"
    for when in (stamp, "2026-09-27_090000", f"{stamp}-1"):
        archive = tmp_path / f"{when}.tar.gz"
        make_archive(archive, when)
        data = archive.read_bytes()
        if fault == "damaged" and when == stamp:  # e.g. the disk filled up
            data = data[:-20]
        (server / f"claudegpt-{when}.tar.gz.age").write_bytes(b"encrypted:" + data)
        (server / f"env-{when}.age").write_text(f"encrypted:env {when}")
    on_server = {path.name for path in server.iterdir()}
    (laptop / "claudegpt-backup.key").write_text("AGE-SECRET-KEY-1STUB\n")
    for name, source in (("scp", SCP_STUB), ("ssh", SSH_STUB), ("age", AGE_STUB)):
        (stubs / name).write_text(source)
        (stubs / name).chmod(0o755)
    log = tmp_path / "commands.log"
    log.touch()
    block = guide_block("scp").replace("YYYY-MM-DD_HHMMSS", stamp)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", block],
        cwd=laptop,
        env={
            "PATH": f"{stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(laptop),
            "LOG": str(log),
            "SERVER": str(server),
            "FAIL": fault,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    deleted = remote_deletions(log, server)
    downloaded = {path.name for path in laptop.rglob("*") if path.is_file()}
    assert deleted <= downloaded, (deleted, downloaded)  # never an uncopied backup
    if fault:
        assert result.returncode != 0
        assert deleted == set()
    else:
        assert result.returncode == 0, result.stderr
        assert deleted == {f"claudegpt-{stamp}.tar.gz.age", f"env-{stamp}.age"}
    assert {path.name for path in server.iterdir()} == on_server - deleted


# ------------------------------------------------------------------ deploy/backup.sh


@LINUX_SCRIPTS
def test_backup_writes_two_new_encrypted_files_and_restarts_the_app(sandbox: Sandbox) -> None:
    result = sandbox.script("backup", AGE_KEY, FAKE_DATE="2026-09-28 10:15:00")

    assert result.returncode == 0, result.stderr
    archive = sandbox.backups / "claudegpt-2026-09-28_101500.tar.gz.age"
    env_copy = sandbox.backups / "env-2026-09-28_101500.age"
    assert list(sandbox.backup_files()) == [archive.name, env_copy.name]  # no temporaries
    assert env_copy.read_text() == "encrypted:" + CURRENT_ENV
    encrypted = archive.read_bytes()
    assert encrypted.startswith(b"encrypted:")
    with tarfile.open(fileobj=io.BytesIO(encrypted.removeprefix(b"encrypted:"))) as backup:
        names = backup.getnames()
    assert "data/agentic_os.sqlite3" in names
    assert "home/app/.claude/.credentials.json" in names
    assert stat.S_IMODE(sandbox.backups.stat().st_mode) == 0o700
    for path in (archive, env_copy):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.stat().st_uid == os.getuid()  # chown to SUDO_USER, to download them
    assert sandbox.app() == ("running", OLD_VOLUMES)
    calls = sandbox.calls()
    run = next(i for i, call in enumerate(calls) if call.startswith("docker run "))
    assert calls.index("docker compose stop app") < run < calls.index("docker compose start app")
    # The daemon would also write the whole stream to the container's log file.
    assert "--log-driver none" in calls[run]
    assert archive.name in result.stdout and env_copy.name in result.stdout


@LINUX_SCRIPTS
@pytest.mark.parametrize("fault", ["tar", "age", "key", "image"])
def test_a_failed_backup_keeps_the_earlier_backups_of_the_day(sandbox: Sandbox, fault: str) -> None:
    """Audit item 16: the second backup of a day used to truncate the first one
    before knowing whether it would work, and deleted what was left on failure."""
    first = sandbox.bash(guide_backup(sandbox), FAKE_DATE="2026-09-28 10:00:00")
    assert first.returncode == 0, first.stderr
    before = sandbox.backup_files()
    assert before
    sandbox.log.write_text("")

    env = {"FAKE_DATE": "2026-09-28 10:05:00"}
    key = AGE_KEY[:-1] if fault == "key" else AGE_KEY  # a key pasted incompletely
    if fault in ("tar", "age"):
        env["FAIL"] = fault
    elif fault == "image":
        (sandbox.root / "image-missing").touch()
    result = sandbox.bash(guide_backup(sandbox, key), **env)

    assert result.returncode != 0
    assert "ERROR" in result.stderr
    # Byte for byte, no new file and no temporary left behind.
    assert sandbox.backup_files() == before, result.stderr
    assert sandbox.app()[0] == "running"
    if fault in ("key", "image"):  # checked before the app stops
        assert "docker compose stop app" not in sandbox.calls()


@LINUX_SCRIPTS
def test_two_backups_on_the_same_day_are_two_files(sandbox: Sandbox) -> None:
    for when in ("2026-09-28 10:00:00", "2026-09-28 18:30:00"):
        result = sandbox.bash(guide_backup(sandbox), FAKE_DATE=when)
        assert result.returncode == 0, result.stderr
    assert len(list(sandbox.backups.glob("claudegpt-*.tar.gz.age"))) == 2
    assert len(list(sandbox.backups.glob("env-*.age"))) == 2


@LINUX_SCRIPTS
def test_backups_in_the_same_second_never_overwrite_each_other(sandbox: Sandbox) -> None:
    sandbox.backups.mkdir(mode=0o700)
    taken = sandbox.backups / "claudegpt-2026-09-28_101500.tar.gz.age"
    taken.write_text("an earlier backup")
    for _ in range(2):
        result = sandbox.script("backup", AGE_KEY, FAKE_DATE="2026-09-28 10:15:00")
        assert result.returncode == 0, result.stderr
    assert taken.read_text() == "an earlier backup"
    assert list(sandbox.backup_files()) == [
        "claudegpt-2026-09-28_101500-1.tar.gz.age",
        "claudegpt-2026-09-28_101500-2.tar.gz.age",
        "claudegpt-2026-09-28_101500.tar.gz.age",
        "env-2026-09-28_101500-1.age",
        "env-2026-09-28_101500-2.age",
    ]


@LINUX_SCRIPTS
def test_backup_leaves_the_owners_shell_alone(sandbox: Sandbox) -> None:
    """N8: `umask 077` used to stay in the owner's root shell, so a later `git pull`
    there wrote deploy/Caddyfile as 0600, which Caddy (uid 10002) cannot read."""
    script = (
        "umask 022\nset +o pipefail\n"
        + guide_backup(sandbox)
        + "\numask\nshopt -qo pipefail && echo pipefail-on || echo pipefail-off\n"
        + ': > "$HOME/Caddyfile"\nstat -c %a "$HOME/Caddyfile"\n'
    )
    result = sandbox.bash(script, FAKE_DATE="2026-09-28 10:00:00")
    assert result.stdout.splitlines()[-3:] == ["0022", "pipefail-off", "644"], result.stderr


@LINUX_SCRIPTS
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGHUP, signal.SIGTERM])
def test_an_interrupted_backup_restarts_the_app_and_leaves_no_partial_file(
    sandbox: Sandbox, sig: signal.Signals
) -> None:
    """N9: Ctrl+C or a dropped SSH session in the middle of a backup used to leave
    the app stopped (restart: unless-stopped does not start a stopped container)."""
    first = sandbox.bash(guide_backup(sandbox), FAKE_DATE="2026-09-28 10:00:00")
    assert first.returncode == 0, first.stderr
    before = sandbox.backup_files()

    output = sandbox.interrupt(guide_backup(sandbox), "tar", sig, FAKE_DATE="2026-09-28 10:05:00")

    assert sandbox.app()[0] == "running", output
    assert sandbox.backup_files() == before, output


@LINUX_SCRIPTS
def test_the_next_backup_deletes_what_a_killed_one_left(sandbox: Sandbox) -> None:
    """kill -9 or a power cut in the middle of a backup: no trap runs, so its hidden
    temporary files (in plain text with --unencrypted) used to stay forever."""
    sandbox.interrupt(
        sandbox.command("backup", "--unencrypted"),
        "tar",
        signal.SIGKILL,
        FAKE_DATE="2026-09-28 10:00:00",
    )
    left = [name for name in sandbox.backup_files() if name.startswith(".")]
    assert left and all(".partial." in name for name in left), left
    # The temporaries of a backup.sh from before the scripts were in English.
    for name in (
        ".claudegpt-2026-09-27_080000.parcial.Ab12Cd",
        ".env-2026-09-27_080000.parcial.Ab12Cd",
    ):
        (sandbox.backups / name).write_text("left by an earlier version")
    for name in (".note", ".env-2026-09-28_090000.backup"):  # not temporaries of backup.sh
        (sandbox.backups / name).write_text("the owner's")

    result = sandbox.script("backup", "--unencrypted", FAKE_DATE="2026-09-28 10:05:00")

    assert result.returncode == 0, result.stderr
    assert list(sandbox.backup_files()) == [
        ".env-2026-09-28_090000.backup",
        ".note",
        "claudegpt-2026-09-28_100500.tar.gz",
        "env-2026-09-28_100500",
    ]
    assert sandbox.app()[0] == "running"


@LINUX_SCRIPTS
@pytest.mark.parametrize("restore", ["killed", "rolled-back"])
def test_backup_waits_for_an_unfinished_restore(sandbox: Sandbox, restore: str) -> None:
    """After a restore killed at R4, .env names the restored volumes while the app
    container still has the old ones: a backup used to save the restored copy and
    start the old container."""
    archive, env_file = sandbox.upload()
    if restore == "killed":
        command = sandbox.command("restore", str(archive), str(env_file))
        sandbox.interrupt(command, "up", signal.SIGKILL)
    else:
        assert sandbox.script("restore", str(archive), str(env_file), FAIL="up").returncode
    sandbox.log.write_text("")

    result = sandbox.script("backup", "--unencrypted", FAKE_DATE="2026-09-28 11:00:00")

    if restore == "killed":
        assert result.returncode != 0 and "ERROR" in result.stderr
        assert "bash deploy/restore.sh --status" in result.stderr
        assert "docker compose stop app" not in sandbox.calls()
        assert sandbox.backup_files() == {}
    else:  # rolled back: the app runs with the old data again, and that is saved
        assert result.returncode == 0, result.stderr
        [run] = [call for call in sandbox.calls() if call.startswith("docker run ")]
        assert f"-v {OLD_VOLUMES[0]}:/data:ro -v {OLD_VOLUMES[1]}:/home/app:ro" in run


@LINUX_SCRIPTS
def test_backup_reads_the_volumes_that_env_names(sandbox: Sandbox) -> None:
    """After a restore the app's volumes are the ones .env names."""
    restored = ("claudegpt_app_data_r20260928-101500", "claudegpt_app_home_r20260928-101500")
    sandbox.fill(restored, "RESTORED")
    env = CURRENT_ENV + f"APP_DATA_VOLUME={restored[0]}\nAPP_HOME_VOLUME='{restored[1]}'\n"
    (sandbox.project / ".env").write_text(env)

    result = sandbox.script("backup", "--unencrypted", FAKE_DATE="2026-09-28 11:00:00")

    assert result.returncode == 0, result.stderr
    [run] = [call for call in sandbox.calls() if call.startswith("docker run ")]
    assert f"-v {restored[0]}:/data:ro -v {restored[1]}:/home/app:ro" in run
    archive = sandbox.backups / "claudegpt-2026-09-28_110000.tar.gz"
    with tarfile.open(archive) as backup:
        member = backup.extractfile("home/app/.claude/.credentials.json")
        assert member is not None and member.read() == b"RESTORED"
    assert (sandbox.backups / "env-2026-09-28_110000").read_text() == env


@LINUX_SCRIPTS
def test_backup_refuses_a_volume_that_does_not_exist(sandbox: Sandbox) -> None:
    (sandbox.project / ".env").write_text(CURRENT_ENV + "APP_DATA_VOLUME=claudegpt_app_dada\n")
    result = sandbox.script("backup", AGE_KEY)
    assert result.returncode != 0 and "ERROR" in result.stderr
    assert "claudegpt_app_dada" in result.stderr
    # Checked before stopping the app; `docker run` would have created it empty.
    assert "docker compose stop app" not in sandbox.calls()
    assert "created volume" not in sandbox.log.read_text()
    assert sandbox.backup_files() == {}


@LINUX_SCRIPTS
def test_a_plain_backup_restores_with_restore_sh(sandbox: Sandbox) -> None:
    result = sandbox.script("backup", "--unencrypted", FAKE_DATE="2026-09-28 10:00:00")
    assert result.returncode == 0, result.stderr
    archive = sandbox.backups / "claudegpt-2026-09-28_100000.tar.gz"
    env_copy = sandbox.backups / "env-2026-09-28_100000"
    assert env_copy.read_text() == CURRENT_ENV
    # The app goes on and its data changes; then the owner restores the backup.
    make_database(sandbox.volume(OLD_VOLUMES[0]) / "later.sqlite3", "LATER")
    result = sandbox.script("restore", str(archive), str(env_copy), FAKE_DATE="2026-09-28 12:00:00")
    assert result.returncode == 0, result.stderr
    data, home = sandbox.journal()["new_data_volume"], sandbox.journal()["new_home_volume"]
    assert sandbox.app() == ("running", (data, home))
    assert sandbox.marker(data) == "CURRENT"
    assert not (sandbox.volume(data) / "later.sqlite3").exists()
    assert (sandbox.volume(home) / ".claude" / ".credentials.json").read_text() == "CURRENT"


# ----------------------------------------------------------------- deploy/restore.sh


@LINUX_SCRIPTS
def test_a_restore_switches_to_new_volumes_and_keeps_the_old_ones(sandbox: Sandbox) -> None:
    script, archive, env_file = guide_restore(sandbox)
    make_archive(archive, "BACKUP")
    # A backup made after an earlier restore names that server's volumes: replaced.
    env_file.write_text(BACKUP_ENV + "APP_DATA_VOLUME=claudegpt_app_data_r20260101-000000\n")

    result = sandbox.bash(script, FAKE_DATE="2026-09-28 10:15:00")

    assert result.returncode == 0, result.stderr
    new = sandbox.assert_restored(archive, env_file)
    assert new == ("claudegpt_app_data_r20260928-101500", "claudegpt_app_home_r20260928-101500")
    calls = sandbox.calls()
    # Extracted and checked in new volumes while the app kept running.
    runs = [i for i, call in enumerate(calls) if call.startswith("docker run ")]
    assert len(runs) == 2 and runs[-1] < calls.index("docker compose stop app")
    assert all("--log-driver none" in calls[i] for i in runs)
    # Recreated, never a restart of the stopped container with its old volumes.
    [start] = [call for call in calls if call.startswith("docker compose up")]
    assert "--force-recreate" in start
    creates = [call for call in calls if call.startswith("docker volume create")]
    assert len(creates) == 2
    for call, key in zip(creates, ("app_data", "app_home"), strict=True):
        # Labelled like compose's own volumes, so it does not warn about them.
        assert "--label com.docker.compose.project=claudegpt" in call
        assert f"--label com.docker.compose.volume={key}" in call

    refused = sandbox.script("restore", "--finalize", stdin="no\n")
    assert refused.returncode != 0 and "ERROR" in refused.stderr
    sandbox.assert_restored(archive, env_file)

    done = sandbox.script("restore", "--finalize", stdin="delete\n")
    assert done.returncode == 0, done.stderr
    assert sandbox.volumes() == sorted(new)
    assert sandbox.app() == ("running", new)
    assert not (sandbox.project / ".env.prev").exists()
    assert not archive.exists() and not env_file.exists()
    assert not sandbox.state.exists()
    assert "No restore in progress" in sandbox.script("restore", "--status").stdout


@LINUX_SCRIPTS
@pytest.mark.parametrize("fault", ["image", "extract", "corrupt", "no-owner", "stop", "up"])
def test_a_failed_restore_leaves_the_current_data_running(sandbox: Sandbox, fault: str) -> None:
    """Audit item 2: after a failed extraction the old block went on, started the
    app on empty or half-restored volumes, replaced .env and deleted the uploads."""
    script, archive, env_file = guide_restore(sandbox)
    make_archive(archive, "BACKUP", fault if fault in ("corrupt", "no-owner") else "")
    env_file.write_text(BACKUP_ENV)
    if fault == "image":
        (sandbox.root / "image-missing").touch()

    result = sandbox.bash(script, FAIL=fault)

    assert result.returncode != 0
    assert "ERROR" in result.stderr
    assert (sandbox.project / ".env").read_text() == CURRENT_ENV
    assert not (sandbox.project / ".env.prev").exists()
    assert not (sandbox.project / ".env.next").exists()
    assert sandbox.app() == ("running", OLD_VOLUMES)
    assert sandbox.marker(OLD_VOLUMES[0]) == "CURRENT"
    assert archive.exists() and env_file.exists()
    if fault in ("stop", "up"):
        # R3, R4: rolled back on its own; the restored volumes are kept to inspect.
        journal = sandbox.journal()
        assert (journal["step"], journal["status"]) == (
            "R3" if fault == "stop" else "R4",
            "rolled_back",
        )
        assert sandbox.marker(journal["new_data_volume"]) == "BACKUP"
    else:
        # R0, R1: the app never stopped and nothing is left behind.
        assert "docker compose stop app" not in sandbox.calls()
        assert sandbox.volumes() == sorted(OLD_VOLUMES)
        assert not sandbox.state.exists()


@LINUX_SCRIPTS
@pytest.mark.parametrize(
    "fault",
    [
        "damaged",
        "encrypted",
        "not-a-backup",
        "empty-env",
        "bad-env",
        "no-space",
        "taken",
        "leftover",
    ],
)
def test_restore_checks_everything_before_touching_anything(sandbox: Sandbox, fault: str) -> None:
    archive, env_file = sandbox.upload(fault=fault)
    env = {"FAKE_DATE": "2026-09-28 10:15:00"}
    if fault == "empty-env":
        env_file.write_text("")
    elif fault == "bad-env":
        env_file.write_text("ACME_EMAIL=tu@example.com\n")  # no DOMAIN
    elif fault == "no-space":
        env["DF_AVAIL_KB"] = "1024"
    elif fault == "taken":
        sandbox.volume("claudegpt_app_data_r20260928-101500").mkdir(parents=True)
    elif fault == "leftover":  # an earlier restore that was never finished by hand
        (sandbox.project / ".env.prev").write_text("DOMAIN=vell.example.com\n")

    result = sandbox.script("restore", str(archive), str(env_file), **env)

    assert result.returncode != 0
    assert "ERROR" in result.stderr
    touching = ("docker volume create", "docker run", "docker compose stop", "docker compose up")
    assert not [call for call in sandbox.calls() if call.startswith(touching)]
    assert (sandbox.project / ".env").read_text() == CURRENT_ENV
    assert sandbox.app() == ("running", OLD_VOLUMES)
    assert archive.exists() and env_file.exists()
    assert not sandbox.state.exists()


# Where a restore can be killed (kill -9, a power cut): the stand-in that blocks
# there and the step the journal must show.
KILL_POINTS = {
    "R1": ("extract", "R1"),
    "R2": ("sync", "R2"),
    "R3-stop": ("stop", "R3"),
    "R3-switch": ("mv", "R3"),
    "R4": ("up", "R4"),
}


@LINUX_SCRIPTS
@pytest.mark.parametrize("action", ["--undo", "--resume"])
@pytest.mark.parametrize("point", [*KILL_POINTS, "rolled-back"])
def test_an_interrupted_restore_can_be_undone_or_resumed(
    sandbox: Sandbox, point: str, action: str
) -> None:
    archive, env_file = sandbox.upload()
    if point == "rolled-back":
        failed = sandbox.script("restore", str(archive), str(env_file), FAIL="up")
        assert failed.returncode != 0
        expected = ("R4", "rolled_back")
    else:
        hang, step = KILL_POINTS[point]
        command = sandbox.command("restore", str(archive), str(env_file))
        sandbox.interrupt(command, hang, signal.SIGKILL)
        expected = (step, "running")
    journal = sandbox.journal()
    assert (journal["step"], journal["status"]) == expected

    status = sandbox.script("restore", "--status")
    assert status.returncode == 0 and expected[0] in status.stdout
    # No other restore can start before this one is resumed or undone.
    again = sandbox.script("restore", str(archive), str(env_file))
    assert again.returncode != 0 and "ERROR" in again.stderr

    result = sandbox.script("restore", action)

    assert result.returncode == 0, result.stdout + result.stderr
    if action == "--undo":
        # Killed at R4, the app may have run (and written) with the restored volumes.
        new = (journal["new_data_volume"], journal["new_home_volume"])
        sandbox.assert_before_restore(archive, env_file, new if point == "R4" else ())
    else:
        sandbox.assert_restored(archive, env_file)
        assert sandbox.script("restore", "--finalize", stdin="delete\n").returncode == 0
        assert not sandbox.state.exists()


@LINUX_SCRIPTS
@pytest.mark.parametrize(
    ("point", "sig"),
    [
        ("extract", signal.SIGINT),
        ("stop", signal.SIGINT),
        ("up", signal.SIGINT),
        ("up", signal.SIGHUP),
    ],
)
def test_an_interrupted_restore_goes_back_to_the_current_data(
    sandbox: Sandbox, point: str, sig: signal.Signals
) -> None:
    archive, env_file = sandbox.upload()

    output = sandbox.interrupt(sandbox.command("restore", str(archive), str(env_file)), point, sig)

    if sig == signal.SIGHUP:  # the terminal is gone: the messages go to a log
        output += Path(f"{sandbox.state}.log").read_text()
    assert "ERROR" in output
    assert (sandbox.project / ".env").read_text() == CURRENT_ENV
    assert not (sandbox.project / ".env.prev").exists()
    assert sandbox.app() == ("running", OLD_VOLUMES)
    assert archive.exists() and env_file.exists()
    if point == "extract":  # R1: nothing left behind, the app never stopped
        assert sandbox.volumes() == sorted(OLD_VOLUMES)
        assert not sandbox.state.exists()
        assert "docker compose stop app" not in sandbox.calls()
    else:  # R3, R4: rolled back, the restored volumes kept for --resume
        journal = sandbox.journal()
        step = "R3" if point == "stop" else "R4"
        assert (journal["step"], journal["status"]) == (step, "rolled_back")
        assert sandbox.marker(journal["new_data_volume"]) == "BACKUP"


@LINUX_SCRIPTS
def test_a_finished_restore_can_still_be_undone(sandbox: Sandbox) -> None:
    archive, env_file = sandbox.upload()
    result = sandbox.script("restore", str(archive), str(env_file))
    assert result.returncode == 0, result.stderr
    new = sandbox.assert_restored(archive, env_file)

    undone = sandbox.script("restore", "--undo")

    assert undone.returncode == 0, undone.stderr
    # Back to the data from before; the restored volumes are the app's data since
    # R5, so they stay, and the owner is told how to delete them.
    sandbox.assert_before_restore(archive, env_file, kept=new)
    assert f"docker volume rm {new[0]} {new[1]}" in undone.stdout
    assert sandbox.marker(new[0]) == "BACKUP"


@LINUX_SCRIPTS
def test_undoing_a_finished_restore_keeps_what_was_written_since(sandbox: Sandbox) -> None:
    """--undo after R5 used to delete the restored volumes without asking, and
    with them everything the app had written since the restore."""
    archive, env_file = sandbox.upload()
    assert sandbox.script("restore", str(archive), str(env_file)).returncode == 0
    database = sandbox.volume(sandbox.journal()["new_data_volume"]) / "agentic_os.sqlite3"
    with contextlib.closing(sqlite3.connect(database)) as db:  # the owner goes on working
        db.execute("INSERT INTO settings VALUES ('conversation', 'written since')")
        db.commit()

    undone = sandbox.script("restore", "--undo")

    assert undone.returncode == 0, undone.stderr
    assert sandbox.app() == ("running", OLD_VOLUMES)
    with contextlib.closing(sqlite3.connect(database)) as db:
        row = db.execute("SELECT value FROM settings WHERE key = 'conversation'").fetchone()
    assert row == ("written since",)


@LINUX_SCRIPTS
@pytest.mark.parametrize("recreated", [False, True])
def test_resume_extracts_again_restored_volumes_that_were_deleted(
    sandbox: Sandbox, recreated: bool
) -> None:
    """After a roll-back no container uses the restored volumes, so `docker volume
    prune -a` deletes them. --resume used to go on: docker compose created them
    again, empty, R5 reported success and --finalize deleted the real data."""
    archive, env_file = sandbox.upload()
    assert sandbox.script("restore", str(archive), str(env_file), FAIL="up").returncode
    journal = sandbox.journal()
    new = (journal["new_data_volume"], journal["new_home_volume"])
    sandbox.prune(*new)
    if recreated:  # e.g. by a `docker compose up` while .env named them: empty
        for name in new:
            sandbox.volume(name).mkdir()
    sandbox.log.write_text("")

    resumed = sandbox.script("restore", "--resume")

    assert resumed.returncode == 0, resumed.stderr
    assert "WARNING" in resumed.stderr
    # Extracted again from the upload, and never started on empty volumes.
    assert sandbox.assert_restored(archive, env_file) == new
    assert "created volume" not in sandbox.log.read_text()
    assert sandbox.script("restore", "--finalize", stdin="delete\n").returncode == 0
    assert sandbox.volumes() == sorted(new) and sandbox.marker(new[0]) == "BACKUP"


@LINUX_SCRIPTS
def test_resume_without_the_image_keeps_the_restored_volumes(sandbox: Sandbox) -> None:
    """The database check needs the image: without it the volumes are not "gone"."""
    archive, env_file = sandbox.upload()
    assert sandbox.script("restore", str(archive), str(env_file), FAIL="up").returncode
    before = sandbox.journal()
    (sandbox.root / "image-missing").touch()

    resumed = sandbox.script("restore", "--resume")

    assert resumed.returncode != 0 and "docker compose build" in resumed.stderr
    assert sandbox.journal() == before
    assert sandbox.marker(before["new_data_volume"]) == "BACKUP"
    assert sandbox.app() == ("running", OLD_VOLUMES)


@LINUX_SCRIPTS
def test_resume_after_r4_stops_if_the_restored_volumes_are_gone(sandbox: Sandbox) -> None:
    archive, env_file = sandbox.upload()
    command = sandbox.command("restore", str(archive), str(env_file))
    sandbox.interrupt(command, "up", signal.SIGKILL)
    journal = sandbox.journal()
    sandbox.prune(journal["new_data_volume"], journal["new_home_volume"])
    sandbox.log.write_text("")

    resumed = sandbox.script("restore", "--resume")

    # .env names the restored volumes already: it cannot extract them again safely.
    assert resumed.returncode != 0 and "ERROR" in resumed.stderr
    assert "bash deploy/restore.sh --undo" in resumed.stderr
    assert "created volume" not in sandbox.log.read_text()
    assert (sandbox.journal()["step"], sandbox.journal()["status"]) == ("R4", "running")
    assert sandbox.script("restore", "--undo").returncode == 0
    sandbox.assert_before_restore(archive, env_file)


@LINUX_SCRIPTS
def test_a_restore_never_starts_the_app_on_volumes_that_vanished(sandbox: Sandbox) -> None:
    archive, env_file = sandbox.upload()
    new = ("claudegpt_app_data_r20260928-101500", "claudegpt_app_home_r20260928-101500")

    # A `docker volume prune` in another terminal while the app stops (R3).
    result = sandbox.script(
        "restore", str(archive), str(env_file), FAKE_DATE="2026-09-28 10:15:00", PRUNE=" ".join(new)
    )

    assert result.returncode != 0 and "ERROR" in result.stderr
    assert "created volume" not in sandbox.log.read_text()
    assert sandbox.app() == ("running", OLD_VOLUMES)
    assert (sandbox.journal()["step"], sandbox.journal()["status"]) == ("R4", "rolled_back")
    resumed = sandbox.script("restore", "--resume")
    assert resumed.returncode == 0, resumed.stderr
    assert sandbox.assert_restored(archive, env_file) == new


@LINUX_SCRIPTS
@pytest.mark.parametrize("fault", ["recreated", "check"])
def test_finalize_deletes_nothing_unless_the_app_runs_on_the_restored_data(
    sandbox: Sandbox, fault: str
) -> None:
    archive, env_file = sandbox.upload()
    assert sandbox.script("restore", str(archive), str(env_file)).returncode == 0
    new = sandbox.assert_restored(archive, env_file)
    env = {}
    if fault == "recreated":  # deleted and created again, empty, by docker compose
        sandbox.prune(*new)
        for name in new:
            sandbox.volume(name).mkdir()
    else:  # the database in use is damaged
        env["FAIL"] = "check"

    result = sandbox.script("restore", "--finalize", stdin="delete\n", **env)

    assert result.returncode != 0 and "ERROR" in result.stderr
    assert sandbox.marker(OLD_VOLUMES[0]) == "CURRENT"
    assert (sandbox.project / ".env.prev").read_text() == CURRENT_ENV
    assert archive.exists() and env_file.exists()
    assert sandbox.journal()["status"] == "done"


@LINUX_SCRIPTS
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGHUP])
def test_a_second_signal_cannot_stop_the_roll_back(sandbox: Sandbox, sig: signal.Signals) -> None:
    """A second Ctrl+C or hang-up in the first moments of the clean-up used to end
    it before the roll-back, leaving the app stopped with the new .env."""
    (sandbox.bin / "rm").write_text(CLEANUP_RM_STUB)
    (sandbox.bin / "rm").chmod(0o755)
    archive, env_file = sandbox.upload()

    command = sandbox.command("restore", str(archive), str(env_file))
    output = sandbox.interrupt(command, "up", sig, again="rm")

    assert (sandbox.project / ".env").read_text() == CURRENT_ENV, output
    assert sandbox.app() == ("running", OLD_VOLUMES)
    assert (sandbox.journal()["step"], sandbox.journal()["status"]) == ("R4", "rolled_back")


@LINUX_SCRIPTS
def test_ctrl_c_as_a_restore_finishes_does_not_undo_it(sandbox: Sandbox) -> None:
    """Ctrl+C while the journal was being told R5 used to roll back a restore that
    had worked, and left a journal that said both "done" and "rolled back"."""
    (sandbox.bin / "sync").write_text(R5_SYNC_STUB)
    archive, env_file = sandbox.upload()

    command = sandbox.command("restore", str(archive), str(env_file))
    sandbox.interrupt(command, "r5", signal.SIGINT)

    sandbox.assert_restored(archive, env_file)
    status = sandbox.script("restore", "--status").stdout
    assert "  Status: done;" in status


@LINUX_SCRIPTS
def test_restore_takes_paths_relative_to_where_it_is_run(sandbox: Sandbox) -> None:
    archive, env_file = sandbox.upload()
    command = sandbox.command("restore", archive.name, env_file.name)

    result = sandbox.bash(f"cd {shlex.quote(str(sandbox.uploads))} && {command}")

    assert result.returncode == 0, result.stderr
    sandbox.assert_restored(archive, env_file)
    assert sandbox.journal()["archive"] == str(archive.resolve())


@LINUX_SCRIPTS
def test_a_second_restore_replaces_the_volumes_of_the_first(sandbox: Sandbox) -> None:
    archive, env_file = sandbox.upload("FIRST")
    first = sandbox.script("restore", str(archive), str(env_file), FAKE_DATE="2026-09-28 10:00")
    assert first.returncode == 0, first.stderr
    assert sandbox.script("restore", "--finalize", stdin="delete\n").returncode == 0

    archive, env_file = sandbox.upload("SECOND")
    second = sandbox.script("restore", str(archive), str(env_file), FAKE_DATE="2026-09-28 11:00")
    assert second.returncode == 0, second.stderr

    journal = sandbox.journal()
    assert journal["old_data_volume"] == "claudegpt_app_data_r20260928-100000"
    assert journal["new_data_volume"] == "claudegpt_app_data_r20260928-110000"
    assert sandbox.marker(journal["new_data_volume"]) == "SECOND"
    assert sandbox.script("restore", "--finalize", stdin="delete\n").returncode == 0
    assert sandbox.volumes() == [
        "claudegpt_app_data_r20260928-110000",
        "claudegpt_app_home_r20260928-110000",
    ]


@LINUX_SCRIPTS
def test_backups_and_restores_never_run_at_the_same_time(sandbox: Sandbox) -> None:
    """A backup would start the app again in the middle of a restore."""
    archive, env_file = sandbox.upload()
    sandbox.state.parent.mkdir(parents=True)
    with Path(f"{sandbox.state}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # as a running restore.sh does
        backup = sandbox.script("backup", AGE_KEY)
        restore = sandbox.script("restore", str(archive), str(env_file))
    for result in (backup, restore):
        assert result.returncode != 0 and "ERROR" in result.stderr
    assert "docker compose stop app" not in sandbox.calls()
    assert sandbox.backup_files() == {}
    sandbox.assert_before_restore(archive, env_file)


@LINUX_SCRIPTS
def test_restore_commands_without_a_restore(sandbox: Sandbox) -> None:
    status = sandbox.script("restore", "--status")
    assert status.returncode == 0 and "No restore in progress" in status.stdout
    assert sandbox.script("restore", "--undo").returncode == 0
    for option in ("--resume", "--finalize"):
        result = sandbox.script("restore", option)
        assert result.returncode != 0 and "ERROR" in result.stderr
    assert sandbox.app() == ("running", OLD_VOLUMES)
    assert sandbox.calls() == []


# ------------------------------------------- the Catalan names of earlier versions


@LINUX_SCRIPTS
@pytest.mark.parametrize(
    ("legacy", "option"),
    [
        ("--estat", "--status"),
        ("--reprèn", "--resume"),
        ("--repren", "--resume"),
        ("--desfés", "--undo"),
        ("--desfes", "--undo"),
        ("--finalitza", "--finalize"),
        ("--ajuda", "--help"),
    ],
)
def test_restore_still_takes_the_catalan_names_of_its_options(
    sandbox: Sandbox, legacy: str, option: str
) -> None:
    """The options had Catalan names before the scripts were in English: an owner
    who learnt them, or the guide of an earlier version, gets the same command."""
    old, new = sandbox.script("restore", legacy), sandbox.script("restore", option)
    assert (old.returncode, old.stdout, old.stderr) == (new.returncode, new.stdout, new.stderr)


@LINUX_SCRIPTS
def test_backup_still_takes_the_catalan_names_of_its_options(sandbox: Sandbox) -> None:
    result = sandbox.script("backup", "--sense-xifrar", FAKE_DATE="2026-09-28 10:00:00")
    assert result.returncode == 0, result.stderr
    assert list(sandbox.backup_files()) == [
        "claudegpt-2026-09-28_100000.tar.gz",
        "env-2026-09-28_100000",
    ]
    old, new = sandbox.script("backup", "--ajuda"), sandbox.script("backup", "--help")
    assert old.returncode == new.returncode == 0 and old.stdout == new.stdout


@LINUX_SCRIPTS
@pytest.mark.parametrize(
    "comment",
    [
        "# Volumes restored by deploy/restore.sh",
        "# Volums restaurats per deploy/restore.sh",  # as earlier versions wrote it
    ],
)
def test_a_restore_replaces_the_volume_lines_of_an_earlier_restore(
    sandbox: Sandbox, comment: str
) -> None:
    """The .env of a server that was itself restored carries the lines that R2 wrote
    there: R2 drops them, comment included, and writes its own."""
    archive, env_file = sandbox.upload()
    env_file.write_text(
        f"{BACKUP_ENV}{comment} (20260101-000000)\n"
        "APP_DATA_VOLUME=claudegpt_app_data_r20260101-000000\n"
        "APP_HOME_VOLUME=claudegpt_app_home_r20260101-000000\n"
    )

    result = sandbox.script("restore", str(archive), str(env_file))

    assert result.returncode == 0, result.stderr
    sandbox.assert_restored(archive, env_file)
    env = (sandbox.project / ".env").read_text()
    stamp = sandbox.journal()["stamp"]
    assert [line for line in env.splitlines() if line.startswith("#")] == [
        f"# Volumes restored by deploy/restore.sh ({stamp})"
    ]
