"""The documentation says what the code does.

- Every number the docs give for a limit is the constant the code uses (audit point
  25: the 1013 threshold was documented ten times too low), like the CSP check of
  web/vite.config.ts (tests/server/test_server_http.py).
- Every ``AOS_*`` variable the docs or the interface name exists (N24: the dialog of a
  rejected origin named one that does not).
- The messages the docs quote are the ones the code sends (the refine turns' reasons).
- The README's deploy summary puts the Claude token where the app reads it (N25).
- No stale statement comes back (N26): files that do not exist, a ``hello`` version
  that is not the package's, a login message the web never shows, a model list the
  CLI mode does not have.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import AliasChoices

from agentic_os import __version__, attachments, pdf_facts
from agentic_os.config import Settings
from agentic_os.domain import (
    REFINE_BUDGET_FACTOR,
    REFINE_CHANGELOG_TAIL,
    REFINE_CONVERGENCE_ROUNDS,
    REFINE_MAX_CHANGES,
    REFINE_MIN_BUDGET_WORDS,
    RefineOptions,
)
from agentic_os.orchestrator import engine, pdf_check
from agentic_os.orchestrator.types import EngineConfig
from agentic_os.providers.claude_cli import CLAUDE_FAMILIES
from agentic_os.server import middleware, turns, ws
from agentic_os.storage import MAX_LIST_LIMIT, MAX_SEARCH_LENGTH, RuntimeSettings
from agentic_os.storage.models import (
    DEFAULT_MODE_REFINE,
    REFINE_BUDGET_EUR_RANGE,
    REFINE_ROUNDS_RANGE,
    REFINE_THRESHOLD_RANGE,
    REFINE_WORDS_RANGE,
    TURN_MODES,
)

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def value(text: str) -> int:
    """A whole number as the docs write it: ``200,000``, ``4 KiB``, ``4401`` (a code)."""
    match = re.fullmatch(r"(\d+|\d{1,3}(?:,\d{3})+)(?: ([KM])iB)?", text)
    assert match, text
    factor = {"K": 1024, "M": 1024 * 1024}.get(match.group(2) or "", 1)
    return int(match.group(1).replace(",", "")) * factor


def field_bound(name: str, bound: str) -> int:
    """The ``ge``/``le`` bound of a Settings field."""
    for constraint in Settings.model_fields[name].metadata:
        if hasattr(constraint, bound):
            return int(getattr(constraint, bound))
    raise AssertionError(f"{name} has no {bound} bound")


def field_default(name: str) -> int:
    default = Settings.model_fields[name].default
    assert isinstance(default, int)
    return default


PROTOCOL = "docs/PROTOCOL.md"
ARCHITECTURE = "docs/ARCHITECTURE.md"
DEPLOYMENT = "docs/DEPLOYMENT.md"
LOGIN_BODY = middleware.SMALL_BODY_PATHS["/api/auth/login"]
BODY_SECONDS = int(middleware.BODY_TIMEOUT_SECONDS)
assert BODY_SECONDS == middleware.BODY_TIMEOUT_SECONDS  # the docs give whole seconds
UPLOAD_SECONDS = int(middleware.UPLOAD_TIMEOUT_SECONDS)
assert UPLOAD_SECONDS == middleware.UPLOAD_TIMEOUT_SECONDS


def megabytes(size: int) -> int:
    assert size % 1_000_000 == 0, size  # the docs give whole megabytes
    return size // 1_000_000


def kilobytes(size: int) -> int:
    assert size % 1000 == 0, size
    return size // 1000


UPLOAD_MB = megabytes(middleware.LARGE_BODY_PATHS[middleware.UPLOAD_PATH])
ORPHAN_HOURS = int(attachments.ORPHAN_TTL.total_seconds() // 3600)
CHECK_MINUTES = int(pdf_check.CHECK_TIMEOUT_SECONDS // 60)
assert CHECK_MINUTES * 60 == pdf_check.CHECK_TIMEOUT_SECONDS  # the docs give whole minutes
GARBAGE_PERCENT = round(pdf_facts.GARBAGE_RATIO * 100)
TINY_POINTS = int(attachments.TINY_POINTS)
OFFPAGE_POINTS = int(attachments.OFFPAGE_MARGIN)
assert (TINY_POINTS, OFFPAGE_POINTS) == (attachments.TINY_POINTS, attachments.OFFPAGE_MARGIN)


DOCUMENTED_NUMBERS: list[tuple[str, str, int]] = [
    # WebSocket close codes and limits.
    (PROTOCOL, r"- `(\d+)` if there is no session", ws.CLOSE_UNAUTHORIZED),
    (PROTOCOL, r"- `(\d+)` if the origin is not valid", ws.CLOSE_FORBIDDEN_ORIGIN),
    (PROTOCOL, r"- `(\d+)` if the client does not receive fast enough", ws.CLOSE_TOO_SLOW),
    (PROTOCOL, r"- `(\d+)` if there is an internal error", ws.CLOSE_INTERNAL_ERROR),
    (PROTOCOL, r"it has ([\d,]+) messages waiting to be sent", ws.SEND_QUEUE_SIZE),
    (PROTOCOL, r"or ([\d,]+) events or more", ws.MAX_PENDING_EVENTS),
    (
        PROTOCOL,
        r"No client message can exceed ([\d,]+) characters",
        ws.MAX_MESSAGE_CHARS,
    ),
    (PROTOCOL, r"checks it again every (\d+) s\b", int(ws.SESSION_CHECK_SECONDS)),
    (
        PROTOCOL,
        r"The question \(`text`\) can have at most ([\d,]+) characters",
        EngineConfig().max_question_chars,
    ),
    (PROTOCOL, r"Limit: (\d+) simultaneous turns", turns.MAX_CONCURRENT_TURNS),
    (PROTOCOL, r"ended less than (\d+) minutes ago", int(turns.RETENTION_SECONDS // 60)),
    # HTTP bodies.
    (PROTOCOL, r"`413` body too large: at most (\d+ [KM]iB)", middleware.MAX_BODY_BYTES),
    (PROTOCOL, r"or (\d+ [KM]iB) on `POST /api/auth/login`", LOGIN_BODY),
    (PROTOCOL, r"`408` if the body does not arrive whole within (\d+) s", BODY_SECONDS),
    (ARCHITECTURE, r"Request bodies have a maximum of (\d+ [KM]iB)", middleware.MAX_BODY_BYTES),
    (ARCHITECTURE, r"\((\d+ [KM]iB) for logging in", LOGIN_BODY),
    (ARCHITECTURE, r"has not arrived whole within (\d+) s\b", BODY_SECONDS),
    (DEPLOYMENT, r"Request bodies have a maximum of (\d+ [KM]iB)", middleware.MAX_BODY_BYTES),
    (DEPLOYMENT, r"\((\d+ [KM]iB) for the login", LOGIN_BODY),
    (DEPLOYMENT, r"has not arrived whole within (\d+) seconds", BODY_SECONDS),
    # Attachments.
    (PROTOCOL, r"; (\d+) s on `PUT /api/attachments`", UPLOAD_SECONDS),
    (PROTOCOL, r"or (\d+) MB on `PUT /api/attachments`", UPLOAD_MB),
    (ARCHITECTURE, r"has a limit of its own: (\d+) MB", UPLOAD_MB),
    (ARCHITECTURE, r"has a limit of its own: \d+ MB and (\d+) s\b", UPLOAD_SECONDS),
    (DEPLOYMENT, r"has a limit of its own: (\d+) MB", UPLOAD_MB),
    (DEPLOYMENT, r"has a limit of its own: \d+ MB and (\d+) seconds", UPLOAD_SECONDS),
    (PROTOCOL, r"at most (\d+) attachments per message", attachments.MAX_ATTACHMENTS),
    (PROTOCOL, r"at most (\d+), each one once", attachments.MAX_ATTACHMENTS),
    (ARCHITECTURE, r"at most (\d+) and \d+ MB in all", attachments.MAX_ATTACHMENTS),
    (PROTOCOL, r"and (\d+) MB in total", megabytes(attachments.MAX_TURN_BYTES)),
    (ARCHITECTURE, r"and (\d+) MB in all", megabytes(attachments.MAX_TURN_BYTES)),
    (PROTOCOL, r"adding up to more than (\d+) MB", megabytes(attachments.MAX_TURN_BYTES)),
    (PROTOCOL, r"image: (\d+) MB", megabytes(attachments.MAX_IMAGE_BYTES)),
    (PROTOCOL, r"image: \d+ MB and ([\d,]+) pixels", attachments.MAX_IMAGE_SIDE),
    (PROTOCOL, r"PDF: (\d+) MB", megabytes(attachments.MAX_PDF_BYTES)),
    (PROTOCOL, r"PDF: \d+ MB and (\d+) pages", attachments.MAX_PDF_PAGES),
    (PROTOCOL, r"text: (\d+) kB", kilobytes(attachments.MAX_TEXT_BYTES)),
    (PROTOCOL, r"over ([\d,]+) pixels on the long side", attachments.DOWNSCALE_EDGE),
    (PROTOCOL, r"shrunk to ([\d,]+) pixels on the long side", attachments.DOWNSCALE_EDGE),
    (PROTOCOL, r"at most ([\d,]+), with `\(w', h'\)`", attachments.MAX_IMAGE_TOKENS),
    (PROTOCOL, r"PDF, ([\d,]+) per page", attachments.PDF_PAGE_TOKENS),
    (PROTOCOL, r"which the first (\d+) bytes decide", attachments.SNIFF_BYTES),
    (PROTOCOL, r"at most (\d+) characters \(a longer one", attachments.MAX_NAME_LENGTH),
    (PROTOCOL, r"is deleted after (\d+) h\b", ORPHAN_HOURS),
    (PROTOCOL, r"in a separate process, for at most (\d+) s", int(attachments.PDF_TIMEOUT_SECONDS)),
    (DEPLOYMENT, r"limits on time \((\d+) seconds\)", int(attachments.PDF_TIMEOUT_SECONDS)),
    (PROTOCOL, r"If it exceeds ([\d,]+) characters", attachments.MAX_PDF_TEXT_CHARS),
    (
        PROTOCOL,
        r"thumbnail: PNG or WebP, at most (\d+) kB",
        kilobytes(attachments.MAX_THUMBNAIL_BYTES),
    ),
    (PROTOCOL, r"of at most (\d+) kB and \d+ pixels", kilobytes(attachments.MAX_THUMBNAIL_BYTES)),
    (PROTOCOL, r"of at most \d+ kB and (\d+) pixels", attachments.MAX_THUMBNAIL_SIDE),
    # The pages of a PDF, and Claude's check of its text for ChatGPT.
    (PROTOCOL, r"`no_text`: fewer than (\d+) letters", pdf_facts.NO_TEXT_LETTERS),
    (PROTOCOL, r"`garbled`: (\d+) broken characters or more", pdf_facts.GARBAGE_MIN),
    (PROTOCOL, r"and at least (\d+)% of the text", GARBAGE_PERCENT),
    (PROTOCOL, r"`hidden`: (\d+) characters or more", pdf_facts.HIDDEN_MIN),
    (PROTOCOL, r"smaller than (\d+) point", TINY_POINTS),
    (ARCHITECTURE, r"smaller than (\d+) point", TINY_POINTS),
    (PROTOCOL, r"origin more than (\d+) point outside", OFFPAGE_POINTS),
    (PROTOCOL, r"ChatGPT waits for the check for at most (\d+) minutes", CHECK_MINUTES),
    (ARCHITECTURE, r"the same task, for at most (\d+) minutes", CHECK_MINUTES),
    (PROTOCOL, r"Claude makes at most (\d+) calls per PDF", pdf_check.MAX_CHECK_CALLS),
    (ARCHITECTURE, r"There are at most (\d+) calls per PDF", pdf_check.MAX_CHECK_CALLS),
    (ARCHITECTURE, r"at most (\d+) PDFs at a time", pdf_check.CHECK_CONCURRENCY),
    (ARCHITECTURE, r"PDF checks: ([\d,]+) tokens", pdf_check.CHECK_BASE_TOKENS),
    (ARCHITECTURE, r"plus ([\d,]+) for each page of the call", pdf_check.CHECK_TEXT_PAGE_TOKENS),
    (ARCHITECTURE, r"and ([\d,]+) for each other page", pdf_check.CHECK_PAGE_TOKENS),
    (ARCHITECTURE, r"each other page, at most ([\d,]+), with", pdf_check.CHECK_MAX_TOKENS),
    # Conversation list and search.
    (PROTOCOL, r"`limit`: from 1 to (\d+)", MAX_LIST_LIMIT),
    (PROTOCOL, r"The search text can be at most (\d+) characters", MAX_SEARCH_LENGTH),
    # Sessions.
    (
        PROTOCOL,
        r"`AOS_SESSION_IDLE_HOURS` without activity \((\d+) h\)",
        field_default("session_idle_hours"),
    ),
    (PROTOCOL, r"`AOS_SESSION_MAX_DAYS` \((\d+) days\)", field_default("session_max_days")),
    (
        DEPLOYMENT,
        r"`AOS_SESSION_IDLE_HOURS`, from 1 to ([\d,]+) hours",
        field_bound("session_idle_hours", "le"),
    ),
    (
        DEPLOYMENT,
        r"`AOS_SESSION_MAX_DAYS`, from 1 to ([\d,]+) days",
        field_bound("session_max_days", "le"),
    ),
    (DEPLOYMENT, r"up to (\d+) hours without activity", field_default("session_idle_hours")),
    (DEPLOYMENT, r"and (\d+) days at most", field_default("session_max_days")),
]


@pytest.mark.parametrize(
    ("document", "pattern", "expected"),
    DOCUMENTED_NUMBERS,
    ids=[f"{Path(doc).stem}:{pattern[:40]}" for doc, pattern, _ in DOCUMENTED_NUMBERS],
)
def test_every_number_the_docs_give_is_the_one_the_code_uses(
    document: str, pattern: str, expected: int
) -> None:
    found = re.findall(pattern, read(document))
    assert found, f"{document} no longer says this ({pattern!r}): update this test"
    assert {value(text) for text in found} == {expected}, found


def test_the_413_answer_of_the_docs_names_the_limit_it_applies() -> None:
    protocol = read(PROTOCOL)
    for limit in (middleware.MAX_BODY_BYTES, LOGIN_BODY, UPLOAD_MB * 1_000_000):
        assert f"`{middleware.too_large_detail(limit)}`" in protocol


# -- the refine mode (docs/adr/0010-refine-mode.md) ---------------------------------

ADR_REFINE = "docs/adr/0010-refine-mode.md"
REFINE = RefineOptions()
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5}


def number(text: str) -> float:
    """A number as the prose of the docs writes it: ``20,000``, ``0.1`` or a word (``two``)."""
    if text in NUMBER_WORDS:
        return NUMBER_WORDS[text]
    assert re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text), text
    return float(text.replace(",", ""))


DOCUMENTED_REFINE_NUMBERS: list[tuple[str, str, tuple[float, ...]]] = [
    # The ranges and defaults of RefineOptions, as the server validates them.
    (
        PROTOCOL,
        r"max_rounds: number;\s+// (\d+)\u2013(\d+), default (\d+)",
        (*REFINE_ROUNDS_RANGE, REFINE.max_rounds),
    ),
    (
        PROTOCOL,
        r"budget_eur: number;\s+// ([\d.]+)\u2013([\d.]+), default ([\d.]+)",
        (*REFINE_BUDGET_EUR_RANGE, REFINE.budget_eur),
    ),
    (PROTOCOL, r"of each version: (\d+)\u2013(\d+)", REFINE_WORDS_RANGE),
    (
        PROTOCOL,
        r"convergence_threshold: number;\s+// (\d+)\u2013(\d+), default (\d+)",
        (*REFINE_THRESHOLD_RANGE, REFINE.convergence_threshold),
    ),
    (
        ARCHITECTURE,
        r"maximum number of rounds \((\d+) by default, from (\d+) to (\d+)\)",
        (REFINE.max_rounds, *REFINE_ROUNDS_RANGE),
    ),
    (
        ARCHITECTURE,
        r"the budget \(€([\d.]+) by default, from €([\d.]+) to €([\d.]+);",
        (REFINE.budget_eur, *REFINE_BUDGET_EUR_RANGE),
    ),
    (
        ADR_REFINE,
        r"maximum number of rounds \((\d+) by default, up to (\d+)\)",
        (REFINE.max_rounds, REFINE_ROUNDS_RANGE[1]),
    ),
    (ADR_REFINE, r"budget in euros \(€([\d.]+) by default", (REFINE.budget_eur,)),
    (ARCHITECTURE, r"give it the threshold \((\d+) by default\)", (REFINE.convergence_threshold,)),
    (ADR_REFINE, r"threshold \((\d+) by default\)", (REFINE.convergence_threshold,)),
    # The word budget when the owner gives no limit.
    (
        PROTOCOL,
        r"([\d.]+) times the word count of version 1, and at least (\d+)",
        (REFINE_BUDGET_FACTOR, REFINE_MIN_BUDGET_WORDS),
    ),
    (
        ARCHITECTURE,
        r"([\d.]+) times the words of version 1 \((\d+) at least\)",
        (REFINE_BUDGET_FACTOR, REFINE_MIN_BUDGET_WORDS),
    ),
    (
        ADR_REFINE,
        r"([\d.]+) times the word count of version 1 \(at least (\d+)\)",
        (REFINE_BUDGET_FACTOR, REFINE_MIN_BUDGET_WORDS),
    ),
    # The loop: changes a round, the changelog every prompt gets, when it stops by itself.
    (
        PROTOCOL,
        r"the changes it (?:proposes|has applied) \(at most (\d+)\)",
        (REFINE_MAX_CHANGES,),
    ),
    (ARCHITECTURE, r"at most (\d+) changes", (REFINE_MAX_CHANGES,)),
    (
        ADR_REFINE,
        r"At most (\d+) changes per round:\*\* (\d+) proposals per review and (\d+) changes",
        (REFINE_MAX_CHANGES,) * 3,
    ),
    (ARCHITECTURE, r"the last (\d+) lines of the changelog", (REFINE_CHANGELOG_TAIL,)),
    (ADR_REFINE, r"the last (\d+) lines", (REFINE_CHANGELOG_TAIL,)),
    *(
        (document, r"(\w+) rounds in a row", (REFINE_CONVERGENCE_ROUNDS,))
        for document in (PROTOCOL, ARCHITECTURE, ADR_REFINE)
    ),
    # Every version is one reply: what does not fit in its output is cut off.
    *(
        (document, r"at most ([\d,]+) output tokens", (EngineConfig().max_output_tokens,))
        for document in (PROTOCOL, ARCHITECTURE, ADR_REFINE)
    ),
]


@pytest.mark.parametrize(
    ("document", "pattern", "expected"),
    DOCUMENTED_REFINE_NUMBERS,
    ids=[f"{Path(doc).stem}:{pattern[:40]}" for doc, pattern, _ in DOCUMENTED_REFINE_NUMBERS],
)
def test_every_number_the_docs_give_for_the_refine_mode_is_the_one_the_code_uses(
    document: str, pattern: str, expected: tuple[float, ...]
) -> None:
    found: list[str | tuple[str, ...]] = re.findall(pattern, read(document))
    assert found, f"{document} no longer says this ({pattern!r}): update this test"
    for groups in found:
        texts = groups if isinstance(groups, tuple) else (groups,)
        assert tuple(number(text) for text in texts) == expected, groups


def test_the_turn_modes_the_protocol_gives_are_the_servers() -> None:
    protocol = read(PROTOCOL)
    [modes] = re.findall(r"type TurnMode = ([^;]+);", protocol)
    assert tuple(re.findall(r'"(\w+)"', modes)) == TURN_MODES
    [counted] = re.findall(r"  turns: \{([^}]*)\}", protocol)  # Stats
    assert tuple(re.findall(r"(\w+): number", counted)) == TURN_MODES


def test_the_activity_messages_the_protocol_names_are_the_servers() -> None:
    """``turn.stop`` is the owner's too: it refreshes the idle timeout."""
    [named] = re.findall(
        r"Only ((?:`[\w.]+`(?:, | and )?)+), which are the owner's actions", read(PROTOCOL)
    )
    assert set(re.findall(r"`([\w.]+)`", named)) == ws.ACTIVITY_MESSAGES


def event_row(protocol: str, event: str) -> str:
    """The row of an event in the protocol's table of events."""
    rows: list[str] = re.findall(rf"^\| `{re.escape(event)}` \|.*$", protocol, re.M)
    [row] = rows
    return row


def test_the_refine_reasons_the_protocol_quotes_are_the_engines() -> None:
    """Why a round of a refine turn wrote no version (``refine.round``'s ``reason``) and
    why a review failed, as the engine says them: the web rebuilds the rounds of a
    reloaded turn from some of these strings."""
    protocol = read(PROTOCOL)
    reasons = (
        engine.REFINE_OVER_BUDGET,
        engine.REFINE_INCOMPLETE,
        engine.REFINE_IDENTICAL,
        engine.REFINE_NOTHING_TO_CHANGE,
        engine.REFINE_FAILED_ROUND,
    )
    row = event_row(protocol, "refine.round")
    for reason in reasons:
        assert f"`{reason}`" in row, reason
    assert f"`{engine.REFINE_NO_CHANGES}`" in event_row(protocol, "stream.failed")


def test_the_refine_answers_the_protocol_quotes_are_the_servers() -> None:
    protocol = read(PROTOCOL)
    assert f"`{turns.STOP_ONLY_REFINE}`" in protocol
    assert f"`{DEFAULT_MODE_REFINE}`" in protocol
    with pytest.raises(ValueError) as refused:
        RuntimeSettings.from_wire({"refine": {"max_rounds": REFINE_ROUNDS_RANGE[0] - 1}})
    assert f"`{refused.value}`" in protocol


# -- AOS_* variables (N24) -------------------------------------------------------------


def settings_variables() -> set[str]:
    """Every ``AOS_*`` environment variable that Settings reads."""
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            names.update(choice for choice in alias.choices if isinstance(choice, str))
        elif isinstance(alias, str):
            names.add(alias)
        else:
            names.add(f"AOS_{name.upper()}")
    return {name for name in names if name.startswith("AOS_")}


def owner_facing_files() -> Iterator[Path]:
    """What the owner reads or sees: the docs, the deployment files, the interface
    (not its tests) and the server's messages."""
    yield from (ROOT / name for name in ("README.md", "CLAUDE.md", ".env.example"))
    yield from (ROOT / name for name in ("docker-compose.yml", "Dockerfile"))
    yield from sorted((ROOT / "docs").rglob("*.md"))
    yield from sorted(p for p in (ROOT / "deploy").iterdir() if p.is_file())
    yield from sorted((ROOT / "src" / "agentic_os").rglob("*.py"))
    for path in sorted((ROOT / "web" / "src").rglob("*")):
        if path.suffix in (".ts", ".svelte") and not (
            ".test." in path.name or path.name.startswith("test-")
        ):
            yield path


def test_every_aos_variable_named_to_the_owner_exists() -> None:
    known = settings_variables()
    assert {"AOS_PUBLIC_ORIGIN", "AOS_EXTRA_ORIGINS", "AOS_ANTHROPIC_API_KEY"} <= known
    unknown = {
        f"{path.relative_to(ROOT)}: {name}"
        for path in owner_facing_files()
        for name in re.findall(r"\bAOS_[A-Z][A-Z0-9_]*", path.read_text(encoding="utf-8"))
        if name not in known
    }
    assert not unknown, sorted(unknown)


# -- the README's deploy summary (N25) -------------------------------------------------


def section(markdown: str, title: str) -> str:
    """Text of a ``## title`` section of a Markdown document."""
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", markdown, re.M | re.S)
    assert match, title
    return match.group(1)


def test_the_readme_deploy_summary_puts_the_claude_token_in_env() -> None:
    [block] = re.findall(
        r"```bash\n(.*?)```", section(read("README.md"), "Deploy to your VPS"), re.S
    )
    lines = block.splitlines()
    token = next(i for i, line in enumerate(lines) if "claude setup-token" in line)
    # The token is only printed: it works once it is CLAUDE_CODE_OAUTH_TOKEN in .env
    # and the app is recreated with it (docs/DEPLOYMENT.md, step 6).
    assert "CLAUDE_CODE_OAUTH_TOKEN" in lines[token] and ".env" in lines[token]
    doctor = next(i for i, line in enumerate(lines) if "agentic-os doctor" in line)
    assert any(line.startswith("docker compose up -d") for line in lines[token + 1 : doctor])
    # The guide's later commands (updates, backups) run from /opt/claudegpt.
    assert "/opt/claudegpt" in lines[0] and any("deploy/harden.sh" in line for line in lines)
    guide = section(read(DEPLOYMENT), "6. Connect Claude (Pro/Max subscription)")
    assert "CLAUDE_CODE_OAUTH_TOKEN=" in guide and "docker compose up -d" in guide


# -- stale statements (N26) --------------------------------------------------------------

BUILD_OUTPUTS = frozenset({"web/dist"})
"""Paths that only exist once the frontend is built."""


def test_the_docs_and_comments_name_only_files_that_exist() -> None:
    sources = [*owner_facing_files(), ROOT / "web" / "vite.config.ts"]
    missing = {
        f"{path.relative_to(ROOT)}: {reference}"
        for path in sources
        for reference in re.findall(
            r"(?<![\w./-])((?:src|docs|web|deploy|tests)/[\w@./-]*\w)",
            path.read_text(encoding="utf-8"),
        )
        if reference not in BUILD_OUTPUTS and not (ROOT / reference).exists()
    }
    assert not missing, sorted(missing)


def test_the_hello_version_in_the_protocol_is_the_package_version() -> None:
    versions = re.findall(r'"version": "([^"]+)"', read(PROTOCOL))
    assert all(version == __version__ for version in versions), versions


def test_the_login_messages_the_guide_quotes_are_the_ones_the_web_shows() -> None:
    troubleshooting = section(read(DEPLOYMENT), "Troubleshooting")
    login = troubleshooting[troubleshooting.index("**I can't log in**") :]
    login = login.split("\n\n")[0]
    quoted = re.findall(r'"([^"]+)"', login)
    assert quoted
    # The login screen's English texts (web/src/views/Login.svelte reads them).
    shown = read("web/src/lib/i18n/areas/app.ts")
    for message in quoted:
        assert message.rstrip(".") in shown, message


def test_the_readme_gives_the_claude_cli_model_aliases() -> None:
    lines = read("README.md").splitlines()
    [models] = [line for line in lines if "Choose each agent's model" in line]
    for alias, _, _ in CLAUDE_FAMILIES:
        assert f"`{alias}`" in models, alias
