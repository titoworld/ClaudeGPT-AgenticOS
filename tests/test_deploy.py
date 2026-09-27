"""Security invariants of the deployment files (docker-compose.yml, Dockerfile,
deploy/, .gitignore, docs/DESPLEGAMENT.md).

They read the files as text or YAML, so they need neither Docker nor Caddy; the
deploy job of the CI checks the same files with the real tools (compose config,
caddy validate, shellcheck, image build)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentic_os.server.middleware import MAX_BODY_BYTES

yaml = pytest.importorskip("yaml")  # PyYAML comes with uvicorn[standard]

ROOT = Path(__file__).resolve().parents[1]
BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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


def caddy_blocks(text: str, header: str) -> list[str]:
    """Bodies of the Caddyfile blocks whose opening line matches ``header``."""
    lines = [re.sub(r"(^|\s)#.*$", "", line).strip() for line in text.splitlines()]
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


def test_caddy_buffers_request_bodies_but_never_websockets() -> None:
    caddyfile = read("deploy/Caddyfile")
    methods = re.search(r"^\s*@body method (.+)$", caddyfile, re.M)
    assert methods and set(methods.group(1).split()) == BODY_METHODS

    [limits] = caddy_blocks(caddyfile, r"request_body @body")
    max_size = size_bytes(re.findall(r"max_size (\S+)", limits)[0])
    assert max_size <= MAX_BODY_BYTES
    assert re.search(r"read_timeout \d+s", limits)

    [buffered] = caddy_blocks(caddyfile, r"reverse_proxy @body app:8000")
    assert size_bytes(re.findall(r"request_buffers (\S+)", buffered)[0]) >= max_size
    # Unmatched request_body or buffering would also catch a WebSocket (GET, or a
    # CONNECT whose body is the socket over HTTP/2 and HTTP/3) and break it.
    assert caddy_blocks(caddyfile, r"request_body") == []
    for block in caddy_blocks(caddyfile, r"reverse_proxy app:8000") + caddy_blocks(
        caddyfile, r"\(app_proxy\)"
    ):
        assert "request_buffers" not in block and "request_body" not in block


def test_ssh_hardening_keeps_the_default_max_auth_tries() -> None:
    # Each key an SSH agent offers counts as a try: a low value locks out owners
    # whose agent holds several keys. Passwords are off, so there is nothing to guess.
    assert "MaxAuthTries" not in read("deploy/harden.sh")


def test_update_steps_refresh_base_images_and_the_host() -> None:
    assert services()["app"]["build"]["pull"] is True
    update = section(read("docs/DESPLEGAMENT.md"), "Actualitzar")
    assert "docker compose build --pull" in update
    assert "apt-get upgrade" in update  # Docker Engine, containerd and runc


def test_backups_are_written_outside_the_repository() -> None:
    backups = section(read("docs/DESPLEGAMENT.md"), "Còpies de seguretat")
    assert "/var/backups/claudegpt" in backups
    # Neither inside the clone (/opt/claudegpt) nor relative to it.
    assert not re.search(r"\$PWD/backups|/opt/claudegpt/backups|(?<![\w/])backups/", backups)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "path", ["backups/claudegpt-2026-09-27.tar.gz", "env-2026-09-27", "backups/env-2026-09-27"]
)
def test_leftover_backups_in_the_clone_are_ignored_by_git(path: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path], cwd=ROOT, check=False
    )
    assert result.returncode == 0, f"{path} is not ignored"
