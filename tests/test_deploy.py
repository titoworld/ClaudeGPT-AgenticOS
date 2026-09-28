"""Security invariants of the deployment files (docker-compose.yml, Dockerfile,
deploy/, .gitignore, docs/).

They read the files as text or YAML, so they need neither Docker nor Caddy; the
deploy job of the CI checks the same files with the real tools (compose config,
caddy validate, shellcheck, image build). The backup and restore commands of
docs/DESPLEGAMENT.md are run with bash against stand-ins for docker and age."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from agentic_os.server.middleware import MAX_BODY_BYTES

yaml = pytest.importorskip("yaml")  # PyYAML comes with uvicorn[standard]

ROOT = Path(__file__).resolve().parents[1]
BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BACKUP_DIR = "/var/backups/claudegpt"
RESTORE_DIR = "/home/usuari"

# Stand-ins for the commands of the backup and restore blocks that must not run for
# real: every call is logged to $LOG. `docker run` writes part of an archive and then
# fails when $FAIL is "tar"; `age` fails half-way through when $FAIL is "age".
COMMAND_STUBS = r"""
cd() { :; }
chown() { printf 'chown %s\n' "$*" >> "$LOG"; }
docker() {
  printf 'docker %s\n' "$*" >> "$LOG"
  if [ "$1" = run ]; then
    cat > /dev/null
    printf 'partial archive'
    [ "$FAIL" = tar ] && return 2
  fi
  return 0
}
age() {
  out=
  while [ $# -gt 0 ]; do
    case $1 in -o) out=$2; shift 2 ;; -r | -i) shift 2 ;; *) shift ;; esac
  done
  if [ -n "$out" ]; then printf 'encrypted env' > "$out"; return 0; fi
  if [ "$FAIL" = age ]; then printf 'enc'; cat > /dev/null; return 1; fi
  printf 'encrypted:'; cat
}
"""


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


def test_caddy_caps_request_bodies_but_never_websockets() -> None:
    caddyfile = read("deploy/Caddyfile")
    methods = re.search(r"^\s*@body method (.+)$", caddyfile, re.M)
    assert methods and set(methods.group(1).split()) == BODY_METHODS

    [limits] = caddy_blocks(caddyfile, r"request_body @body")
    assert size_bytes(re.findall(r"max_size (\S+)", limits)[0]) <= MAX_BODY_BYTES
    assert re.search(r"read_timeout \d+s", limits)
    # An unmatched request_body would also catch a WebSocket (a GET, or a CONNECT
    # whose body is the socket over HTTP/2 and HTTP/3): capped and timed out, it breaks.
    assert caddy_blocks(caddyfile, r"request_body") == []
    proxies = caddy_blocks(caddyfile, r"reverse_proxy(\s+\S+)*")
    assert proxies
    for block in proxies:
        assert "request_body" not in block


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
        ("docs/ARQUITECTURA.md", "sense eines"),
        # agents.max_threads caps sub-agents running at once, not per call.
        ("docs/ARQUITECTURA.md", "un per crida"),
        ("docs/DESPLEGAMENT.md", "un per crida"),
        ("docs/adr/0002-subscripcions-via-cli-oficials.md", "un subagent per crida"),
        # Caddy streams request bodies (test_caddy_streams_request_bodies_...).
        ("docs/ARQUITECTURA.md", "llegeix sencer"),
        ("docs/DESPLEGAMENT.md", "llegeix sencer"),
        # The Codex logs are deleted before every start: a full tmpfs cannot stop it.
        ("docs/DESPLEGAMENT.md", "failed to initialize sqlite state runtime"),
    ],
)
def test_docs_drop_stale_claims(document: str, stale: str) -> None:
    assert stale not in read(document)


def test_login_troubleshooting_covers_new_devices_during_an_attack() -> None:
    text = section(read("docs/DESPLEGAMENT.md"), "Resolució de problemes")
    login = text[text.index("**No puc iniciar sessió**") :].split("\n\n")[0]
    assert "agentic-os reset-throttle" in login
    advice = next(
        line for line in login.splitlines() if "dispositiu nou" in line and "atac" in line
    )
    assert "ALLOWED_IPS" in advice and "VPN" in advice


def heading_anchor(heading: str) -> str:
    """GitHub's anchor for a Markdown heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


@pytest.mark.parametrize("document", ["docs/DESPLEGAMENT.md", "docs/ARQUITECTURA.md"])
def test_links_to_sections_resolve(document: str) -> None:
    text = read(document)
    anchors = {heading_anchor(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, target


def test_harden_script_points_to_existing_sections() -> None:
    headings = set(re.findall(r"^## (.+)$", read("docs/DESPLEGAMENT.md"), re.M))
    named = re.findall(r"apartat «([^»]+)»", read("deploy/harden.sh"))
    assert named
    for name in named:
        assert name in headings, name


def test_ssh_hardening_keeps_the_default_max_auth_tries() -> None:
    # Each key an SSH agent offers counts as a try: a low value locks out owners
    # whose agent holds several keys. Passwords are off, so there is nothing to guess.
    assert "MaxAuthTries" not in read("deploy/harden.sh")


def test_update_steps_refresh_base_images_and_the_host() -> None:
    assert services()["app"]["build"]["pull"] is True
    update = section(read("docs/DESPLEGAMENT.md"), "Actualitzar")
    assert "docker compose build --pull" in update
    assert "apt-get upgrade" in update  # Docker Engine, containerd and runc


def test_update_steps_apply_a_new_caddyfile() -> None:
    # deploy/Caddyfile is bind-mounted as a single file: `git pull` replaces it with
    # a new inode that the running container never sees, and `docker compose up -d`
    # keeps a container whose service definition did not change.
    assert "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro" in services()["caddy"]["volumes"]
    [steps] = bash_blocks(section(read("docs/DESPLEGAMENT.md"), "Actualitzar"))[:1]
    commands = steps.splitlines()
    assert commands.index("docker compose restart caddy") > commands.index("git pull")


def test_backups_are_written_outside_the_repository() -> None:
    backups = section(read("docs/DESPLEGAMENT.md"), "Còpies de seguretat")
    assert "/var/backups/claudegpt" in backups
    # Neither inside the clone (/opt/claudegpt) nor relative to it.
    assert not re.search(r"\$PWD/backups|/opt/claudegpt/backups|(?<![\w/])backups/", backups)


def bash_blocks(markdown: str) -> list[str]:
    return re.findall(r"^```bash\n(.*?)^```", markdown, re.M | re.S)


def doc_script(marker: str, directory: str, replacement: Path) -> str:
    """The ``bash`` block of the backup section that contains ``marker``, with
    ``directory`` moved to ``replacement`` (never the real one) and the commands
    that must not run for real replaced by :data:`COMMAND_STUBS`."""
    backups = section(read("docs/DESPLEGAMENT.md"), "Còpies de seguretat")
    [script] = [block for block in bash_blocks(backups) if marker in block]
    assert directory in script
    return COMMAND_STUBS + script.replace(directory, str(replacement))


def run_bash(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        cwd=cwd,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), **env},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("fail", ["", "tar", "age"])
def test_a_failed_backup_leaves_no_archive_and_restarts_the_app(tmp_path: Path, fail: str) -> None:
    target = tmp_path / "backups"
    log = tmp_path / "commands.log"
    script = doc_script("tar czf", BACKUP_DIR, target)
    result = run_bash(script, tmp_path, LOG=str(log), FAIL=fail)

    archives = sorted(target.glob("claudegpt-*"))
    if fail:
        # Without pipefail the pipeline reports only age's status: a truncated
        # archive would be kept and look like a good backup.
        assert archives == [], result.stderr
        assert "ERROR" in result.stderr
    else:
        [archive] = archives
        assert archive.name.endswith(".tar.gz.age")
        assert archive.read_text() == "encrypted:partial archive"
        assert list(target.glob("env-*.age"))
    calls = log.read_text().splitlines()
    run = next(i for i, call in enumerate(calls) if call.startswith("docker run "))
    assert calls.index("docker compose stop app") < run < calls.index("docker compose start app")


def fake_backup(path: Path) -> None:
    """A tar.gz like the backup's: ``data`` and ``home/app``."""
    with tarfile.open(path, "w:gz") as archive:
        for name, data in (("data/agentic_os.sqlite3", b"db"), ("home/app/.codex/x", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("tar") is None, reason="tar")
@pytest.mark.parametrize("damaged", [False, True])
def test_restore_checks_the_archive_before_deleting_anything(tmp_path: Path, damaged: bool) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    archive = uploads / "claudegpt-AAAA-MM-DD.tar.gz"
    fake_backup(archive)
    if damaged:  # an interrupted upload
        archive.write_bytes(archive.read_bytes()[:-20])
    (uploads / "env-AAAA-MM-DD").write_text("DOMAIN=ia.example.com\n")
    log = tmp_path / "commands.log"
    log.touch()
    script = doc_script("tar xzf", RESTORE_DIR, uploads)
    result = run_bash(script, tmp_path, LOG=str(log))

    calls = log.read_text().splitlines()
    if damaged:
        # The restore deletes both volumes before it unpacks the archive.
        assert calls == [], calls
        assert "ERROR" in result.stderr
        assert archive.exists()
    else:
        assert any(call.startswith("docker run ") and "-delete" in call for call in calls)
        assert (tmp_path / ".env").read_text() == "DOMAIN=ia.example.com\n"
        assert not archive.exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "path", ["backups/claudegpt-2026-09-27.tar.gz", "env-2026-09-27", "backups/env-2026-09-27"]
)
def test_leftover_backups_in_the_clone_are_ignored_by_git(path: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path], cwd=ROOT, check=False
    )
    assert result.returncode == 0, f"{path} is not ignored"
