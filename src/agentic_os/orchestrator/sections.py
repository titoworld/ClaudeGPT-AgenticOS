"""Incremental parser for debate revisions.

A revision reply has the form::

    <critique>...</critique>
    <answer>...</answer>          (or exactly UNCHANGED inside)
    <agreement>N</agreement>

:class:`RevisionStreamParser` is fed the raw text chunks as they arrive and
returns the critique/answer pieces to show while streaming. It never emits tag
text, tolerates tags split across chunks, stray whitespace and odd casing, and
falls back to treating untagged text as the answer when the model ignores the
format. What it emits for a section is always exactly what ``final()`` returns
for it (after stripping), so the live view matches the stored message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RevisionSection = Literal["critique", "answer"]
Piece = tuple[RevisionSection, str]

_TAG_NAMES = ("critique", "answer", "agreement")
_TAG_RE = re.compile(r"<\s*(/?)\s*(critique|answer|agreement)\s*/?\s*>", re.IGNORECASE)
_PARTIAL_TAG_RE = re.compile(r"<\s*/?\s*([A-Za-z]*)(\s*/?\s*)")
_MAX_TAG_CHARS = 40

_UNCHANGED_FULL_RE = re.compile(r"[*_`\"']*UNCHANGED[*_`\"'.!]*", re.IGNORECASE)
_UNCHANGED_PREFIX_RE = re.compile(
    r"[*_`\"']*(?:U(?:N(?:C(?:H(?:A(?:N(?:G(?:E(?:D[*_`\"'.!]*)?)?)?)?)?)?)?)?)?",
    re.IGNORECASE,
)
_MAX_UNCHANGED_CHARS = 24

_FALLBACK_AFTER_CHARS = 200
"""Untagged text longer than this before any tag is streamed as the answer."""

_NUMBER_RE = re.compile(r"\d{1,3}")
_LOOSE_AGREEMENT_RE = re.compile(r"agreement\W{0,5}(\d{1,3})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RevisionParse:
    critique: str
    answer: str
    """The revised answer; the literal ``UNCHANGED`` marker when ``unchanged``."""
    agreement: int | None
    """Agreement with the other agent's answer, clamped to 0-100 (None if missing)."""
    unchanged: bool


def _could_be_tag(text: str) -> bool:
    """Whether ``text`` (starting with '<') may still become a known tag."""
    if len(text) > _MAX_TAG_CHARS:
        return False
    match = _PARTIAL_TAG_RE.fullmatch(text)
    if match is None:
        return False
    name = match.group(1).lower()
    if match.group(2):
        return name in _TAG_NAMES
    return any(tag.startswith(name) for tag in _TAG_NAMES)


class _Section:
    """Accumulates one section: strips outer whitespace while streaming and, for
    the answer, holds back a possible ``UNCHANGED`` marker so it never reaches the UI."""

    def __init__(self, *, detect_unchanged: bool) -> None:
        self.parts: list[str] = []
        self.opened = False
        self.unchanged = False
        self._started = False
        self._pending_ws = ""
        self._holding = detect_unchanged
        self._held = ""

    def reopen(self) -> None:
        if self._started and not self._holding:
            self._pending_ws = "\n\n"

    def add(self, text: str) -> str:
        if not self._started:
            text = text.lstrip()
            if not text:
                return ""
            self._started = True
        if self._holding:
            self._held += text
            candidate = self._held.rstrip()
            if len(candidate) <= _MAX_UNCHANGED_CHARS and _UNCHANGED_PREFIX_RE.fullmatch(
                candidate
            ):
                return ""
            self._holding = False
            text, self._held = self._held, ""
        body = text.rstrip()
        if not body:
            self._pending_ws += text
            return ""
        out = self._pending_ws + body
        self._pending_ws = text[len(body) :]
        self.parts.append(out)
        return out

    def flush(self) -> str:
        """End of stream: release (or recognise as UNCHANGED) any held text."""
        if not self._holding or not self._held:
            return ""
        self._holding = False
        held, self._held = self._held.strip(), ""
        if _UNCHANGED_FULL_RE.fullmatch(held):
            self.unchanged = True
            return ""
        self.parts.append(held)
        return held

    @property
    def text(self) -> str:
        return "".join(self.parts).strip()


class RevisionStreamParser:
    """Streaming parser of a revision reply (see the module docstring)."""

    def __init__(self) -> None:
        self._buffer = ""
        self._state: Literal["outside", "critique", "answer", "agreement"] = "outside"
        self._seen_tag = False
        self._fallback = False
        self._outside: list[str] = []
        self._agreement_text: str | None = None
        self._raw: list[str] = []
        self._closed = False
        self._critique = _Section(detect_unchanged=False)
        self._answer = _Section(detect_unchanged=True)

    def feed(self, chunk: str) -> list[Piece]:
        """Consume a chunk; return the pieces that can be shown now."""
        if self._closed:
            raise RuntimeError("parser already closed")
        self._raw.append(chunk)
        self._buffer += chunk
        out: list[Piece] = []
        while self._buffer:
            lt = self._buffer.find("<")
            if lt == -1:
                self._text(self._buffer, out)
                self._buffer = ""
                break
            if lt > 0:
                self._text(self._buffer[:lt], out)
                self._buffer = self._buffer[lt:]
            match = _TAG_RE.match(self._buffer)
            if match is not None:
                self._tag(closing=bool(match.group(1)), name=match.group(2).lower())
                self._buffer = self._buffer[match.end() :]
                continue
            if _could_be_tag(self._buffer):
                break  # wait for the rest of the tag
            self._text("<", out)
            self._buffer = self._buffer[1:]
        return out

    def close(self) -> list[Piece]:
        """End of stream: return the remaining pieces to show (idempotent)."""
        if self._closed:
            return []
        self._closed = True
        out: list[Piece] = []
        # A dangling partial tag at the very end is a truncated tag: drop it.
        self._buffer = ""
        if not self._answer.opened and not self._fallback:
            untagged = "".join(self._outside)
            if untagged.strip():
                self._answer.opened = True
                self._emit("answer", self._answer.add(untagged), out)
        self._emit("answer", self._answer.flush(), out)
        return out

    def final(self) -> RevisionParse:
        """The parsed reply (closes the parser if needed)."""
        self.close()
        answer = "UNCHANGED" if self._answer.unchanged else self._answer.text
        return RevisionParse(
            critique=self._critique.text,
            answer=answer,
            agreement=self._agreement(),
            unchanged=self._answer.unchanged,
        )

    # -- internals -------------------------------------------------------------

    def _agreement(self) -> int | None:
        match: re.Match[str] | None = None
        if self._agreement_text is not None:
            match = _NUMBER_RE.search(self._agreement_text)
        if match is None:
            loose = _LOOSE_AGREEMENT_RE.search("".join(self._raw))
            if loose is None:
                return None
            return max(0, min(100, int(loose.group(1))))
        return max(0, min(100, int(match.group(0))))

    def _tag(self, *, closing: bool, name: str) -> None:
        self._seen_tag = True
        if closing:
            self._state = "outside"
            return
        if name == "critique":
            self._critique.reopen()
            self._critique.opened = True
            self._state = "critique"
        elif name == "answer":
            self._answer.reopen()
            self._answer.opened = True
            self._state = "answer"
        else:
            self._agreement_text = ""
            self._state = "agreement"

    def _text(self, text: str, out: list[Piece]) -> None:
        if self._state == "critique":
            self._emit("critique", self._critique.add(text), out)
        elif self._state == "answer":
            self._emit("answer", self._answer.add(text), out)
        elif self._state == "agreement":
            if self._agreement_text is not None and len(self._agreement_text) < 64:
                self._agreement_text += text
        elif self._fallback:
            self._emit("answer", self._answer.add(text), out)
        else:
            self._outside.append(text)
            if not self._seen_tag:
                untagged = "".join(self._outside)
                if len(untagged.strip()) > _FALLBACK_AFTER_CHARS:
                    # The model is ignoring the format: stream its text as the answer.
                    self._fallback = True
                    self._outside.clear()
                    self._answer.opened = True
                    self._emit("answer", self._answer.add(untagged), out)

    @staticmethod
    def _emit(section: RevisionSection, text: str, out: list[Piece]) -> None:
        if text:
            out.append((section, text))
