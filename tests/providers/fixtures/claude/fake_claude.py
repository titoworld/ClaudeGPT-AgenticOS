#!/usr/bin/env python3
"""Fake ``claude`` CLI for the ClaudeCliProvider tests (standard library only).

Tests symlink this script as ``<dir>/claude`` and write ``<dir>/scenario.json``
next to the link, so the provider's environment allow-list stays untouched. Every
run records what it saw (argv, environment, cwd, pids, stdin message) in
``<dir>/calls/<pid>.json`` and replays a recorded stream-json fixture from this
file's real directory.

Scenario keys (all optional):
  stream           fixture file to replay (default stream_success.jsonl)
  result_override  fields merged into the replayed ``result`` event
  delay            seconds between replayed events (default 0.005)
  action           "replay" (default), "crash" (exit without a result) or "hang"
  crash_after      events replayed before crashing (default 3)
  stderr           text written to stderr before crashing
  exit_code        exit code of a crash (default 3)
  spawn_child      start a sleeping child process in the same process group
  ignore_sigterm   ignore SIGTERM (the provider must escalate to SIGKILL)
  status           {"json": {...}, "exit_code": 0} for ``auth status --json``
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(sys.argv[0]).absolute().parent
FIXTURES = Path(__file__).resolve().parent

FLAGS = (
    "-p",
    "--verbose",
    "--include-partial-messages",
    "--setting-sources=",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
)
PAIRS = {
    "--input-format": "stream-json",
    "--output-format": "stream-json",
    "--tools": "",
    "--max-turns": "1",
}
PREFIXED = ("--system-prompt=", "--model=")
FORBIDDEN = ("--append-system-prompt", "--bare", "--system-prompt-file")


def record(entry: dict[str, Any]) -> None:
    calls = HERE / "calls"
    calls.mkdir(exist_ok=True)
    target = calls / f"{os.getpid()}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry))
    tmp.replace(target)


def fail(message: str) -> None:
    sys.stderr.write(f"fake-claude: {message}\n")
    sys.stderr.flush()
    sys.exit(2)


def check_argv(argv: list[str]) -> None:
    for flag in FLAGS:
        if flag not in argv:
            fail(f"missing {flag}")
    for flag, value in PAIRS.items():
        if flag not in argv or argv.index(flag) + 1 >= len(argv):
            fail(f"missing {flag}")
        if argv[argv.index(flag) + 1] != value:
            fail(f"{flag} must be {value!r}")
    for prefix in PREFIXED:
        if not any(arg.startswith(prefix) for arg in argv):
            fail(f"missing {prefix}")
    for arg in argv:
        if arg.startswith(FORBIDDEN):
            fail(f"forbidden flag {arg}")


def emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def main() -> None:
    scenario: dict[str, Any] = {}
    scenario_file = HERE / "scenario.json"
    if scenario_file.exists():
        scenario = json.loads(scenario_file.read_text())
    argv = sys.argv[1:]
    entry: dict[str, Any] = {
        "argv": argv,
        "env": dict(os.environ),
        "cwd": os.getcwd(),
        "cwd_entries": sorted(os.listdir(".")),
        "cwd_mode": os.stat(".").st_mode & 0o777,
        "pid": os.getpid(),
        "pgid": os.getpgid(0),
        "sid": os.getsid(0),
        "phase": "started",
    }

    if argv[:2] == ["auth", "status"]:
        status = scenario.get("status", {})
        record({**entry, "phase": "status"})
        time.sleep(float(status.get("delay", 0)))
        sys.stdout.write(json.dumps(status.get("json", {"loggedIn": False, "authMethod": "none"})))
        sys.stdout.flush()
        sys.exit(int(status.get("exit_code", 0)))

    check_argv(argv)
    if scenario.get("ignore_sigterm"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if scenario.get("spawn_child"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        entry["child_pid"] = child.pid
    record(entry)

    line = sys.stdin.readline()
    if not line:
        record({**entry, "phase": "no_input"})
        return
    message = json.loads(line)
    if message.get("type") != "user" or message.get("client_composed") is not True:
        fail("stdin message must be a client_composed user message")
    if message["message"]["role"] != "user" or not isinstance(message["message"]["content"], str):
        fail("bad user message")
    entry["stdin"] = message
    record({**entry, "phase": "input"})

    delay = float(scenario.get("delay", 0.005))
    events = [
        json.loads(raw)
        for raw in (FIXTURES / scenario.get("stream", "stream_success.jsonl"))
        .read_text()
        .splitlines()
        if raw.strip()
    ]
    action = scenario.get("action", "replay")
    if action == "crash":
        for event in events[: int(scenario.get("crash_after", 3))]:
            emit(event)
            time.sleep(delay)
        sys.stderr.write(scenario.get("stderr", ""))
        sys.stderr.flush()
        sys.exit(int(scenario.get("exit_code", 3)))
    if action == "hang":
        emit(events[0])
        while True:
            time.sleep(1)

    for event in events:
        if event.get("type") == "result":
            event.update(scenario.get("result_override", {}))
        emit(event)
        time.sleep(delay)
    # Like the real CLI: wait for more stdin turns and exit when stdin is closed.
    if sys.stdin.readline():
        fail("unexpected second message")
    record({**entry, "phase": "exited"})


if __name__ == "__main__":
    main()
