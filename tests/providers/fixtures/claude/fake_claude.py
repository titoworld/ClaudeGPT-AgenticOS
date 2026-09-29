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
  sigterm_grace    shut down gracefully on SIGTERM, like the real CLI: record it, go on
                   replaying and exit only this many seconds later
  continue_delay   seconds before the CLI's own follow-up message (default ``delay``)
  status           {"json": {...}, "exit_code": 0} for ``auth status --json``
  startup_lines    true: print the stream's leading start-up events (``active_goal`` and
                   ``autocompact_state``) as soon as the process starts, before it reads
                   stdin, and replay the rest after the message (``crash_after`` counts
                   from there). A list of strings prints those raw lines instead. The
                   run is then recorded as phase "startup_printed" until its message.

Start-up lines: logged in to the real API, the CLI 2.1.283 printed ``active_goal`` and
``autocompact_state`` within a second of starting, while it still waited on stdin, and
``system``/``init`` only after reading the message (then ``system``/``status``
"requesting", then the API request). With no session, or against a local mock of the
API, it printed no start-up lines. By default the fake prints none either.

The CLI going on by itself: with no tools, a ``user`` event in the stream is always the
CLI's own follow-up message after a reply it did not accept (the continuation after
``stop_reason: "max_tokens"``, the retry after a refusal...), and the real CLI sends its
next API request right after it. The fake records that as phase "continued" and keeps
``continued: true`` in every later record, so a test can check it never happened.

Fixtures: ``stream_success.jsonl``, ``stream_auth_error.jsonl`` and
``stream_rate_limited.jsonl`` were recorded from the real CLI 2.1.283 and the real API.
``stream_max_tokens.jsonl``, ``stream_refusal.jsonl`` and ``stream_refusal_recovered.jsonl``
keep the event order the real CLI 2.1.283 printed against a local mock of the Messages
API (no real model), with the ids and fields of ``stream_success.jsonl``:

- max_tokens: ``message_delta`` with ``stop_reason: "max_tokens"``, ``message_stop``, then
  at once the CLI's own ``user`` message ("Output token limit hit. Resume directly...")
  and a second API request, about 10 ms after ``message_stop`` (up to 3 of them); the
  ``result`` has ``is_error: false`` and only the continuation's text;
- refusal: ``message_delta`` with ``stop_reason: "refusal"``, ``message_stop``, a
  ``system``/``informational`` notice and the CLI's own ``user`` message ("Your response
  above was stopped by a safety classifier...") with a second API request. When that one
  refuses too, the CLI prints ``system``/``model_refusal_no_fallback``, a synthetic
  "API Error" message and a ``result`` with ``is_error: true`` and
  ``stop_reason: "refusal"`` (``stream_refusal.jsonl``); when it answers, the ``result``
  has ``is_error: false`` and only the follow-up's text (``stream_refusal_recovered.jsonl``).

On SIGTERM the real CLI shuts down gracefully: it still printed the ``user`` follow-up
and ``system``/``status`` events and exited about 20 ms later, usually after its next
request had left (``sigterm_grace`` models that). SIGKILL stops it at once.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
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
STARTUP_TYPES = ("active_goal", "autocompact_state")
"""Events the real CLI prints before it reads stdin (see "Start-up lines")."""


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


def split_startup(events: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """The stream's leading start-up events, as lines, and the events after them."""
    count = 0
    while count < len(events) and events[count].get("type") in STARTUP_TYPES:
        count += 1
    return [json.dumps(event) for event in events[:count]], events[count:]


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
    elif scenario.get("sigterm_grace") is not None:
        grace = float(scenario["sigterm_grace"])

        def graceful_shutdown(signum: int, frame: object) -> None:
            entry["sigterm"] = True
            record({**entry, "phase": "sigterm"})
            threading.Timer(grace, os._exit, (128 + signum,)).start()

        signal.signal(signal.SIGTERM, graceful_shutdown)
    if scenario.get("spawn_child"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        entry["child_pid"] = child.pid
    record(entry)

    events = [
        json.loads(raw)
        for raw in (FIXTURES / scenario.get("stream", "stream_success.jsonl"))
        .read_text()
        .splitlines()
        if raw.strip()
    ]
    startup: list[str] = []
    if scenario.get("startup_lines") is True:
        startup, events = split_startup(events)
    elif isinstance(scenario.get("startup_lines"), list):
        startup = [str(raw) for raw in scenario["startup_lines"]]
    if startup:
        # Like the real CLI: they leave while it still waits on stdin.
        for raw in startup:
            sys.stdout.write(raw + "\n")
        sys.stdout.flush()
        entry["startup_printed"] = len(startup)
        record({**entry, "phase": "startup_printed"})

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

    continue_delay = float(scenario.get("continue_delay", delay))
    for event in events:
        if event.get("type") == "user":
            # The CLI goes on by itself: its next API request leaves right after this.
            time.sleep(max(0.0, continue_delay - delay))
            entry["continued"] = True
            record({**entry, "phase": "continued"})
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
