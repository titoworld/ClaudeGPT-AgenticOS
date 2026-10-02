"""Readers of the replies of a refine turn (docs/adr/0010-mode-perfecciona.md).

A review proposes changes to the current version and scores it::

    <changes>
    - [kind] where: what — why        (at most REFINE_MAX_CHANGES, or only UNCHANGED)
    </changes>
    <score>N</score>

An edit (the merge of round 1, every later edit and its shortening) writes a version::

    <version>
    The complete document.
    </version>
    <changelog>
    - [kind] what                     (the merge: what it took from each answer)
    </changelog>

Both are read strictly: a bullet whose kind is not one of :data:`CHANGE_KINDS` (or that
has no text) is ignored, and only the first :data:`~agentic_os.domain.REFINE_MAX_CHANGES`
count; a kind may come in Catalan or Spanish (:data:`KIND_ALIASES`), as a reply in the
brief's language may write it, although the prompts ask for it in English; a score is a
number from 0 to 100 (None otherwise). A review failed when its reply has no closed
changes section, or when that section neither lists a valid change nor says UNCHANGED
(:func:`says_unchanged`): prose, or bullets of no known kind, are not «nothing to
change», which could end the turn while the reviewer proposed changes. Unlike a debate
revision, nothing here needs to follow Markdown
code: a document may quote the tags it is written between (a plan for this very format,
an XML file...), so the version ends at the last ``</version>`` followed by the
changelog (or by the end of the reply), and the changes at the last ``</changes>``
followed by the score (or by the end of the reply). A reply that was cut off never holds
a complete version: its end may be a quoted tag.

:class:`ReviewStream` and :class:`EditStream` show a reply while it streams: a review's
changes as section "critique", an edit's version as section "answer" and its changelog as
"critique". Text is shown up to the first closing tag; from there on it waits for the end
of the reply, which decides where the section really ends. What a stream shows of a
section is always exactly what its final parse holds, so the live view matches the
stored message, and no tag is ever shown.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from agentic_os.domain import REFINE_MAX_CHANGES
from agentic_os.orchestrator.events import RefineChange, Section

logger = logging.getLogger(__name__)

RefinePiece = tuple[Section, str]
"""A piece of a reply to show while it streams."""

CHANGE_KINDS: Final[tuple[str, ...]] = ("defect", "clarity", "simplification", "requirement")
"""The kinds of change a review may propose and an edit may apply."""
MERGE_KIND: Final = "merge"
"""The kind of every line of the merge's changelog (what it took from each answer)."""
KIND_ALIASES: Final[Mapping[str, str]] = {
    # Catalan
    "defecte": "defect",
    "claredat": "clarity",
    "simplificacio": "simplification",
    "requisit": "requirement",
    "fusio": MERGE_KIND,
    # Spanish
    "defecto": "defect",
    "claridad": "clarity",
    "simplificacion": "simplification",
    "requisito": "requirement",
    "fusion": MERGE_KIND,
}
"""The kinds as a reply in Catalan or Spanish may write them (compared in lower case and
without accents), and the kind each one is."""

_FLAGS = re.IGNORECASE


def _opening(name: str) -> re.Pattern[str]:
    return re.compile(rf"<\s*{name}\s*>", _FLAGS)


def _closing(name: str) -> re.Pattern[str]:
    return re.compile(rf"<\s*/\s*{name}\s*>", _FLAGS)


_CHANGES_OPEN = _opening("changes")
_CHANGES_CLOSE = _closing("changes")
_VERSION_OPEN = _opening("version")
_VERSION_CLOSE = _closing("version")
_CHANGELOG_CLOSE = _closing("changelog")
_AFTER_VERSION = re.compile(r"\s*<\s*changelog\s*>", _FLAGS)
"""What makes a ``</version>`` the end of the version: the changelog right after it."""
_AFTER_CHANGES = re.compile(r"\s*<\s*score\s*>", _FLAGS)
_SCORE = re.compile(r"<\s*score\s*>(.*?)<\s*/\s*score\s*>", _FLAGS | re.DOTALL)
_SCORE_VALUE = re.compile(r"\s*(\d{1,3})\s*(?:%|/\s*100)?\s*")
_PARTIAL_CLOSE = re.compile(r"<\s*(?:/\s*([A-Za-z]*)(\s*))?")
_BULLET = re.compile(r"[ \t]*(?:[-*\u2022\u2013]|\d{1,2}[.)])[ \t]+(.*)")
"""A bullet: a hyphen, an asterisk, a bullet sign or an en dash, or a number."""
_KIND = re.compile(r"[*_]*\[[ \t]*([^\W\d_]+)[ \t]*\][*_]*[ \t]*(.*)")
_UNCHANGED = re.compile(r"[\W_]*(unchanged)(?!\w)(.*)", re.IGNORECASE)
"""A line that starts with the word UNCHANGED, maybe after a bullet sign, emphasis or
quotes; the rest of the line after it."""
_MARKER = "UNCHANGED"


def _kind(name: str) -> str:
    """A kind as a bullet writes it, in lower case and in English (:data:`KIND_ALIASES`)."""
    plain = "".join(
        char
        for char in unicodedata.normalize("NFKD", name.casefold())
        if not unicodedata.combining(char)
    )
    return KIND_ALIASES.get(plain, plain)


# -- changes ---------------------------------------------------------------------------


def parse_changes(text: str, *, merge: bool = False) -> tuple[RefineChange, ...]:
    """The changes listed in ``text``, one bullet each (``- [kind] text``; ``*``, ``•``
    and ``1.`` bullets too, the kind in any case, maybe in bold, in English or in
    Catalan or Spanish), the first :data:`~agentic_os.domain.REFINE_MAX_CHANGES` of them.
    A bullet goes on in the indented lines right after it; any other line ends it. A
    bullet whose kind is not one of :data:`CHANGE_KINDS`, or that has no text, is
    ignored. With ``merge`` every bullet counts: of kind :data:`MERGE_KIND`, with or
    without the tag, unless it is tagged with a kind of change (a shortened version 1
    may say what it removed)."""
    changes: list[RefineChange] = []
    kind: str | None = None
    parts: list[str] = []

    def end_bullet() -> None:
        nonlocal kind
        if kind is not None and parts and parts[0]:
            changes.append(RefineChange(kind, " ".join(parts)))
        kind = None
        parts.clear()

    for line in text.splitlines():
        bullet = _BULLET.fullmatch(line)
        if bullet is not None:
            end_bullet()
            body = bullet.group(1).strip()
            tagged = _KIND.fullmatch(body)
            written = _kind(tagged.group(1)) if tagged is not None else None
            if merge and written not in CHANGE_KINDS:
                kind = MERGE_KIND
                if tagged is not None and written == MERGE_KIND:
                    body = tagged.group(2).strip()
            elif tagged is not None and written in CHANGE_KINDS:
                kind = written
                body = tagged.group(2).strip()
            parts.append(body)
        elif kind is not None and line[:1] in (" ", "\t") and line.strip():
            parts.append(line.strip())
        else:
            end_bullet()
    end_bullet()
    return tuple(changes[:REFINE_MAX_CHANGES])


# -- reviews ---------------------------------------------------------------------------


def says_unchanged(section: str) -> bool:
    """Whether a review's changes section (stripped) says it proposes nothing: it is
    empty, or its first line is UNCHANGED, alone (in any case, maybe after a bullet sign,
    in emphasis or quotes, with punctuation) or, written exactly as the prompt asks for
    it, followed by a note; the lines after it are a note too. «No hi ha res a canviar»
    is not: the prompts ask for UNCHANGED, in English."""
    if not section:
        return True
    match = _UNCHANGED.fullmatch(section.split("\n", 1)[0].strip())
    if match is None:
        return False
    return match.group(1) == _MARKER or re.search(r"\w", match.group(2)) is None


@dataclass(frozen=True, slots=True)
class ReviewParse:
    text: str | None
    """The changes section as written (stripped); None when the reply has none: the
    review failed."""
    changes: tuple[RefineChange, ...] = ()
    score: int | None = None
    """How well the version serves the brief, 0-100; None when missing or invalid."""
    unchanged: bool = False
    """The review proposes no change: its section lists no valid change and is empty or
    says UNCHANGED (:func:`says_unchanged`)."""

    @property
    def ok(self) -> bool:
        """The review says what it was asked for: the changes it proposes, or UNCHANGED.
        One without a changes section failed, and so did one whose section does neither
        (prose, bullets of no known kind)."""
        return self.text is not None and (bool(self.changes) or self.unchanged)


def _score(text: str) -> int | None:
    match = _SCORE.search(text)
    if match is None:
        return None
    value = _SCORE_VALUE.fullmatch(match.group(1))
    if value is None:
        return None
    score = int(value.group(1))
    return score if score <= 100 else None


def parse_review(reply: str) -> ReviewParse:
    """A review (see the module docstring). Its changes section starts at the first
    ``<changes>`` and ends at the last ``</changes>`` followed by the score or by the end
    of the reply (failing that, at the last ``</changes>``): a change that quotes the
    tag does not cut the list short."""
    opening = _CHANGES_OPEN.search(reply)
    if opening is None:
        return ReviewParse(None)
    closings = list(_CHANGES_CLOSE.finditer(reply, opening.end()))
    if not closings:
        return ReviewParse(None)
    ends = [
        closing
        for closing in closings
        if _AFTER_CHANGES.match(reply, closing.end()) or not reply[closing.end() :].strip()
    ]
    end = (ends or closings)[-1]
    text = reply[opening.end() : end.start()].strip()
    changes = parse_changes(text)
    unchanged = not changes and says_unchanged(text)
    return ReviewParse(text, changes, _score(reply[end.end() :]), unchanged)


# -- edits -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EditParse:
    complete: bool
    """The reply holds a whole version: its section is closed by the changelog or by the
    end of the reply, and the reply was not cut off."""
    text: str
    """The version (stripped). Without a complete one, what the reply wrote of it: the
    text after ``<version>`` (the whole reply without one), shown as it came."""
    changes: tuple[RefineChange, ...] = ()
    """The changes its changelog lists (none without a complete version)."""
    changelog_text: str = ""
    """The changelog as written (stripped)."""


def parse_edit(reply: str, *, merge: bool = False, truncated: bool = False) -> EditParse:
    """An edit (see the module docstring); ``merge`` reads the changelog of round 1's
    merge. The version starts at the first ``<version>`` (what comes before it is a
    preamble) and ends at the last ``</version>`` that is followed by ``<changelog>`` or
    by the end of the reply, so a document that quotes the tags is read whole. A reply
    that was cut off (``truncated``) is never complete: where it stopped, the last such
    tag may be one the document quotes."""
    opening = _VERSION_OPEN.search(reply)
    if opening is None:
        return EditParse(False, reply.strip())
    start = opening.end()
    end: re.Match[str] | None = None
    changelog_at: int | None = None
    if not truncated:
        for closing in _VERSION_CLOSE.finditer(reply, start):
            after = _AFTER_VERSION.match(reply, closing.end())
            if after is not None:
                end, changelog_at = closing, after.end()
            elif not reply[closing.end() :].strip():
                end, changelog_at = closing, None
    if end is None:
        return EditParse(False, reply[start:].strip())
    changelog = ""
    if changelog_at is not None:
        rest = reply[changelog_at:]
        closings = list(_CHANGELOG_CLOSE.finditer(rest))
        changelog = (rest[: closings[-1].start()] if closings else rest).strip()
    return EditParse(
        True,
        reply[start : end.start()].strip(),
        parse_changes(changelog, merge=merge),
        changelog,
    )


# -- streaming ---------------------------------------------------------------------------


def _may_close(text: str, name: str) -> bool:
    """Whether ``text`` (from a ``<`` to the end of what has arrived) may still become
    the closing tag ``</name>``."""
    match = _PARTIAL_CLOSE.fullmatch(text)
    if match is None:
        return False
    letters = (match.group(1) or "").lower()
    if match.group(2):  # blanks after the name: it must be whole
        return letters == name
    return name.startswith(letters)


class _Streamer:
    """Shows one section of a reply while it streams: the text after its opening tag
    (the first), up to the first closing tag; from there on it waits for the end of the
    reply (:meth:`finish`). Outer whitespace is never shown, and trailing whitespace is
    held until more text follows it."""

    def __init__(self, name: str, section: Section) -> None:
        self._name = name
        self._section: Section = section
        self._opening = _opening(name)
        self._closing = _closing(name)
        self._state: Literal["before", "inside", "held"] = "before"
        self._buffer = ""
        self._shown = ""
        self._blank = ""

    def feed(self, chunk: str) -> list[RefinePiece]:
        out: list[RefinePiece] = []
        if self._state == "held":
            return out
        self._buffer += chunk
        if self._state == "before":
            opening = self._opening.search(self._buffer)
            if opening is None:
                # Only text from the last "<" on can still hold the opening tag.
                cut = self._buffer.rfind("<")
                self._buffer = self._buffer[cut:] if cut >= 0 else ""
                return out
            self._buffer = self._buffer[opening.end() :]
            self._state = "inside"
        closing = self._closing.search(self._buffer)
        if closing is not None:
            self._show(self._buffer[: closing.start()], out)
            self._buffer = ""
            self._state = "held"
            return out
        cut = self._buffer.rfind("<")
        if cut >= 0 and _may_close(self._buffer[cut:], self._name):
            text, self._buffer = self._buffer[:cut], self._buffer[cut:]
        else:
            text, self._buffer = self._buffer, ""
        self._show(text, out)
        return out

    def _show(self, text: str, out: list[RefinePiece]) -> None:
        if not self._shown:
            text = text.lstrip()
        body = text.rstrip()
        if not body:
            if self._shown:
                self._blank += text
            return
        piece = self._blank + body
        self._blank = text[len(body) :]
        self._shown += piece
        out.append((self._section, piece))

    def finish(self, final: str) -> list[RefinePiece]:
        """End of the reply: the rest of the section as its parse holds it (``final``)."""
        if not final.startswith(self._shown):  # never expected: the parse decides it
            logger.warning("A refine stream showed text that its parse does not hold")
            return []
        rest = final[len(self._shown) :]
        self._shown = final
        return [(self._section, rest)] if rest else []


class ReviewStream:
    """Streaming reader of a review: its changes section as section "critique" (see the
    module docstring)."""

    def __init__(self) -> None:
        self._raw: list[str] = []
        self._streamer = _Streamer("changes", "critique")
        self._parse: ReviewParse | None = None

    def feed(self, chunk: str) -> list[RefinePiece]:
        if self._parse is not None:
            raise RuntimeError("stream already closed")
        self._raw.append(chunk)
        return self._streamer.feed(chunk)

    def close(self) -> list[RefinePiece]:
        """End of the reply: the rest to show (idempotent)."""
        if self._parse is not None:
            return []
        self._parse = parse_review("".join(self._raw))
        if self._parse.text is None:
            return []
        return self._streamer.finish(self._parse.text)

    def final(self) -> ReviewParse:
        """The parsed review (closes the stream if needed)."""
        self.close()
        assert self._parse is not None
        return self._parse


class EditStream:
    """Streaming reader of an edit: its version as section "answer", then its changelog
    as section "critique" (see the module docstring)."""

    def __init__(self, *, merge: bool = False) -> None:
        self._merge = merge
        self._raw: list[str] = []
        self._streamer = _Streamer("version", "answer")
        self._parse: EditParse | None = None

    def feed(self, chunk: str) -> list[RefinePiece]:
        if self._parse is not None:
            raise RuntimeError("stream already closed")
        self._raw.append(chunk)
        return self._streamer.feed(chunk)

    def close(self, *, truncated: bool = False) -> list[RefinePiece]:
        """End of the reply (``truncated``: it was cut off): the rest to show
        (idempotent)."""
        if self._parse is not None:
            return []
        self._parse = parse_edit("".join(self._raw), merge=self._merge, truncated=truncated)
        out = self._streamer.finish(self._parse.text)
        if self._parse.complete and self._parse.changelog_text:
            out.append(("critique", self._parse.changelog_text))
        return out

    def final(self) -> EditParse:
        """The parsed edit (closes the stream if needed, as a reply that was not cut
        off)."""
        self.close()
        assert self._parse is not None
        return self._parse
