#!/usr/bin/env python3
"""Fake ``codex app-server`` for the provider tests (JSON-RPC subset of Codex CLI 0.157.1).

It speaks newline-delimited JSON-RPC on stdio with the method and field names of the
0.157.1 schema. ``samples/*.jsonl`` are notification sequences recorded from the real
0.157.1 binary (against a local mock model); ``{THREAD_ID}``/``{TURN_ID}`` are replaced
on replay. The behaviour of a turn is chosen by a marker in its input text:

  (none)          replay samples/stream_turn.jsonl
  [echo]          stream the input text back, word by word
  [usage-limit]   a 100 % rate-limit update, then replay samples/usage_limit_turn.jsonl
  [approval]      send every server -> client request, record the answers, then finish
  [crash]         send one delta and exit abruptly
  [slow]          a delta every 50 ms until turn/interrupt (up to 20 s)
  [interrupted]   finish the turn with status "interrupted" on its own
  [retry-error]   a non-final error notification (willRetry: true) before the answer
  [commentary]    a commentary message before the final answer

Options from ``$CODEX_HOME/fake.json``: ``account`` (account/read value, may be null),
``requiresOpenaiAuth``, ``config_model``, ``init_error`` and ``spawn_child`` (start a
``sleep`` child to check process-group kills). Every message received is appended to
``$CODEX_HOME/requests.jsonl`` with the pid; the environment goes to ``env.json``.
Standard library only.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("CODEX_HOME") or ".")
OPTIONS: dict[str, Any] = (
    json.loads((HOME / "fake.json").read_text()) if (HOME / "fake.json").exists() else {}
)
PID = os.getpid()
DEFAULT_ACCOUNT = {"type": "chatgpt", "email": "owner@example.com", "planType": "plus"}

_out_lock = threading.Lock()
_log_lock = threading.Lock()
_ids = itertools.count(1)
_waiters: dict[str, tuple[threading.Event, list[dict[str, Any]]]] = {}
_interrupts: dict[str, threading.Event] = {}


def log(entry: dict[str, Any]) -> None:
    with _log_lock, open(HOME / "requests.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": PID, **entry}) + "\n")


def send(message: dict[str, Any]) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def notify(method: str, params: dict[str, Any]) -> None:
    send({"method": method, "params": params})


def respond(request_id: Any, result: Any) -> None:
    send({"id": request_id, "result": result})


def fail(request_id: Any, code: int, message: str) -> None:
    send({"id": request_id, "error": {"code": code, "message": message}})


def ask(method: str, params: dict[str, Any], timeout: float = 5.0) -> dict[str, Any] | None:
    """Send a server -> client request and wait for the client's answer."""
    request_id = f"srv-{next(_ids)}"
    event: threading.Event = threading.Event()
    box: list[dict[str, Any]] = []
    _waiters[request_id] = (event, box)
    send({"id": request_id, "method": method, "params": params})
    event.wait(timeout)
    return box[0] if box else None


def rate_limits(primary: float, secondary: float) -> dict[str, Any]:
    now = int(time.time())
    return {
        "limitId": "codex",
        "limitName": None,
        "normalModelSlug": None,
        "primary": {"usedPercent": primary, "windowDurationMins": 300, "resetsAt": now + 3600},
        "secondary": {
            "usedPercent": secondary,
            "windowDurationMins": 10080,
            "resetsAt": now + 86400,
        },
        "credits": None,
        "individualLimit": None,
        "spendControlReached": None,
        "planType": "plus",
        "rateLimitReachedType": None,
    }


def breakdown(inp: int, cached: int, written: int, out: int, reasoning: int) -> dict[str, int]:
    return {
        "totalTokens": inp + out,
        "inputTokens": inp,
        "cachedInputTokens": cached,
        "cacheWriteInputTokens": written,
        "outputTokens": out,
        "reasoningOutputTokens": reasoning,
    }


class Turn:
    """Builders for the notifications of one turn."""

    def __init__(self, thread_id: str, turn_id: str) -> None:
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.items: list[dict[str, Any]] = []

    def ids(self) -> dict[str, str]:
        return {"threadId": self.thread_id, "turnId": self.turn_id}

    def begin(self) -> None:
        notify(
            "thread/status/changed",
            {"threadId": self.thread_id, "status": {"type": "active", "activeFlags": []}},
        )
        notify("turn/started", {"threadId": self.thread_id, "turn": self.turn("inProgress")})

    def message(
        self, item_id: str, chunks: list[str], *, phase: str | None = None, delay: float = 0.0
    ) -> None:
        item = {
            "type": "agentMessage",
            "id": item_id,
            "text": "",
            "phase": phase,
            "memoryCitation": None,
            "delivery": None,
            "questions": None,
        }
        notify("item/started", {"item": item, **self.ids(), "startedAtMs": 0})
        for chunk in chunks:
            time.sleep(delay)
            notify("item/agentMessage/delta", {**self.ids(), "itemId": item_id, "delta": chunk})
        done = {**item, "text": "".join(chunks)}
        self.items.append(done)
        notify("item/completed", {"item": done, **self.ids(), "completedAtMs": 0})

    def usage(self, last: dict[str, int], total: dict[str, int]) -> None:
        notify(
            "thread/tokenUsage/updated",
            {
                **self.ids(),
                "tokenUsage": {"total": total, "last": last, "modelContextWindow": 258400},
            },
        )

    def turn(self, status: str, error: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "id": self.turn_id,
            "items": self.items,
            "itemsView": "summary",
            "status": status,
            "error": error,
            "startedAt": int(time.time()),
            "completedAt": None if status == "inProgress" else int(time.time()),
            "durationMs": None,
        }

    def finish(self, status: str = "completed") -> None:
        notify("thread/status/changed", {"threadId": self.thread_id, "status": {"type": "idle"}})
        notify("turn/completed", {"threadId": self.thread_id, "turn": self.turn(status)})


def replay(sample: str, thread_id: str, turn_id: str) -> None:
    for line in (HERE / "samples" / sample).read_text(encoding="utf-8").splitlines():
        if line.strip():
            send(json.loads(line.replace("{THREAD_ID}", thread_id).replace("{TURN_ID}", turn_id)))
            time.sleep(0.002)


SERVER_REQUESTS: list[tuple[str, dict[str, Any]]] = [
    ("item/commandExecution/requestApproval", {"itemId": "call_1", "command": "rm -rf /"}),
    ("item/fileChange/requestApproval", {"itemId": "call_2", "reason": None}),
    ("item/permissions/requestApproval", {"itemId": "call_3", "permissions": {}}),
    (
        "item/tool/requestUserInput",
        {
            "itemId": "call_4",
            "questions": [{"id": "q1", "header": "Q", "question": "Continue?", "options": None}],
            "isBlocking": True,
            "autoResolutionMs": None,
        },
    ),
    ("mcpServer/elicitation/request", {"serverName": "x", "message": "Give me data"}),
    ("item/tool/call", {"callId": "call_5", "tool": "lookup", "arguments": {}}),
    ("applyPatchApproval", {"callId": "call_6", "fileChanges": {}}),
    ("execCommandApproval", {"callId": "call_7", "command": ["ls"]}),
    ("account/chatgptAuthTokens/refresh", {"reason": "unauthorized", "previousAccountId": None}),
]


MARKERS = (
    "[echo]",
    "[crash]",
    "[approval]",
    "[slow]",
    "[interrupted]",
    "[retry-error]",
    "[commentary]",
)


def run_turn(thread_id: str, turn_id: str, text: str) -> None:
    turn = Turn(thread_id, turn_id)
    interrupted = _interrupts[turn_id]
    if "[usage-limit]" in text:
        notify("account/rateLimits/updated", {"rateLimits": rate_limits(100, 64)})
        replay("usage_limit_turn.jsonl", thread_id, turn_id)
        return
    if not any(marker in text for marker in MARKERS):
        replay("stream_turn.jsonl", thread_id, turn_id)
        return
    turn.begin()
    if "[crash]" in text:
        notify("item/agentMessage/delta", {**turn.ids(), "itemId": "m1", "delta": "Parcial"})
        time.sleep(0.05)
        os._exit(3)
    if "[slow]" in text:
        for _ in range(400):
            if interrupted.wait(0.05):
                turn.finish("interrupted")
                return
            notify("item/agentMessage/delta", {**turn.ids(), "itemId": "m1", "delta": "."})
        turn.finish()
        return
    if "[interrupted]" in text:
        turn.finish("interrupted")
        return
    if "[approval]" in text:
        answers: dict[str, dict[str, Any] | None] = {}
        for method, params in SERVER_REQUESTS:
            answers[method] = ask(method, {**turn.ids(), **params})
        log({"answers": answers})
        turn.usage(breakdown(100, 0, 0, 10, 5), breakdown(100, 0, 0, 10, 5))
        turn.message("m2", ["Totes ", "declinades."])
        turn.usage(breakdown(200, 50, 10, 20, 2), breakdown(300, 50, 10, 30, 7))
        turn.finish()
        return
    if "[retry-error]" in text:
        error = {
            "message": "Reconnecting... 1/5",
            "codexErrorInfo": None,
            "additionalDetails": None,
            "misalignment": None,
        }
        notify("error", {"error": error, "willRetry": True, **turn.ids()})
    if "[commentary]" in text:
        turn.message("c1", ["Pensant", "..."], phase="commentary")
        turn.message("m1", ["Primer."], phase="final_answer")
        turn.message("m2", ["Segon."], phase="final_answer")
    else:
        words = text.replace("[echo]", "").split()
        turn.message("m1", [f"{word} " for word in words], delay=0.01)
    turn.usage(breakdown(1000, 600, 0, 50, 10), breakdown(1000, 600, 0, 50, 10))
    notify("account/rateLimits/updated", {"rateLimits": rate_limits(42.5, 7)})
    turn.finish()


def handle(request_id: Any, method: str, params: dict[str, Any]) -> None:
    account = OPTIONS.get("account", DEFAULT_ACCOUNT)
    if method == "initialize":
        if OPTIONS.get("init_error"):
            fail(request_id, -32603, "initialization failed on purpose")
            return
        name = params.get("clientInfo", {}).get("name", "client")
        respond(
            request_id,
            {
                "userAgent": f"{name}/0.157.1 (fake; x86_64) linux ({name}; 0.1)",
                "codexHome": str(HOME),
                "platformFamily": "unix",
                "platformOs": "linux",
            },
        )
        notify(
            "configWarning",
            {"summary": "Codex could not find bubblewrap on PATH.", "details": None},
        )
    elif method == "account/read":
        respond(
            request_id,
            {"account": account, "requiresOpenaiAuth": OPTIONS.get("requiresOpenaiAuth", True)},
        )
    elif method == "account/rateLimits/read":
        if account is None:
            fail(request_id, -32600, "codex account authentication required to read rate limits")
            return
        respond(
            request_id,
            {
                "ordinaryUsageAllowed": True,
                "rateLimits": rate_limits(21, 3),
                "rateLimitsByLimitId": None,
                "rateLimitResetCredits": None,
                "accountId": None,
                "rateLimitUpsell": None,
            },
        )
    elif method == "config/read":
        respond(
            request_id,
            {"config": {"model": OPTIONS.get("config_model")}, "origins": {}, "layers": None},
        )
    elif method == "model/list":
        entry = {"id": "gpt-6-astra", "model": "gpt-6-astra", "isDefault": True, "hidden": False}
        respond(request_id, {"data": [entry], "nextCursor": None})
    elif method == "thread/start":
        thread_id = str(uuid.uuid4())
        model = params.get("model") or OPTIONS.get("config_model") or "gpt-6-astra"
        thread = {"id": thread_id, "ephemeral": bool(params.get("ephemeral")), "turns": []}
        respond(
            request_id,
            {
                "thread": thread,
                "model": model,
                "modelProvider": "openai",
                "serviceTier": None,
                "cwd": params.get("cwd"),
                "approvalPolicy": params.get("approvalPolicy"),
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "reasoningEffort": None,
            },
        )
        notify("thread/started", {"thread": thread})
    elif method == "turn/start":
        turn_id = str(uuid.uuid4())
        _interrupts[turn_id] = threading.Event()
        respond(request_id, {"turn": Turn(params["threadId"], turn_id).turn("inProgress")})
        text = "".join(part.get("text", "") for part in params.get("input", []))
        threading.Thread(
            target=run_turn, args=(params["threadId"], turn_id, text), daemon=True
        ).start()
    elif method == "turn/interrupt":
        event = _interrupts.get(params.get("turnId", ""))
        if event is not None:
            event.set()
        respond(request_id, {})
    elif method == "thread/unsubscribe":
        respond(request_id, {"status": "unsubscribed"})
    else:
        fail(request_id, -32601, f"unknown method {method}")


def main() -> None:
    log({"argv": sys.argv[1:], "cwd": os.getcwd()})
    (HOME / "env.json").write_text(json.dumps(dict(os.environ)), encoding="utf-8")
    if OPTIONS.get("spawn_child"):
        child = subprocess.Popen(["sleep", "60"])
        (HOME / "child.pid").write_text(str(child.pid), encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        log({"msg": message})
        if "method" not in message:
            waiter = _waiters.pop(str(message.get("id")), None)
            if waiter is not None:
                waiter[1].append(message)
                waiter[0].set()
        elif "id" in message:
            handle(message["id"], message["method"], message.get("params") or {})


if __name__ == "__main__":
    main()
