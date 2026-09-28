"""Incremental parser for debate revisions.

A revision reply has the form::

    <critique>...</critique>
    <answer>...</answer>          (or UNCHANGED, optionally with a short note)
    <agreement>N</agreement>

:class:`RevisionStreamParser` is fed the raw text chunks as they arrive and returns the
critique/answer pieces to show while streaming. It never emits the text of a structural
tag, tolerates tags split across chunks, stray whitespace and odd casing, and falls back
to treating untagged text as the answer when the model ignores the format. What it
emits for a section is always exactly what ``final()`` returns for it (after
stripping), so the live view matches the stored message; to keep that promise it holds
back only the text it cannot place yet.

A tag written as content is text, not structure (an answer that explains XML, a
critique that quotes a tag; docs/adr/0005-integritat-de-les-respostes.md):

- a tag inside fenced code (```` ``` ```` or ``~~~`` fences, at any indentation) or inside
  an inline code span (a backtick run closed by a run of the same length on the same
  line) is text. A tag after a backtick that is never closed on its line is not, and
  neither is a tag inside a fence that never closes: from such a tag on, the text is held
  until the fence closes (it was code) or the reply ends (it is read again as prose);
- an opening tag of the section that is open is text;
- a closing tag of a section that is not open is text;
- a closing tag of the open section followed (after whitespace) by the opening tag of a
  section, by the end of the reply or, after ``</answer>``, by a loose agreement line such
  as ``Agreement: 80`` ends the section. When other text follows it, the decision waits
  for the next tag. The same closing tag again means the first one was text (a critique
  or an answer that mentions it) and the section goes on; the opening tag of a section or
  the end of the reply means it was the real end, and the text in between is dropped (a
  header such as "Here is my revised answer:", a sign-off) or, when the reply tags no
  answer at all, is the untagged answer, as before. After ``</answer>`` the wait lasts at
  most :data:`TAIL_MAX_CHARS` characters (whitespace aside): longer text is answer text,
  and so was the tag.

An answer whose first non-empty line is ``UNCHANGED`` (optionally wrapped in ``*``,
``_``, backticks or quotes, with an optional trailing ``.`` or ``!``) keeps the previous
answer. After the marker, and on its line only, the exact upper-case ``UNCHANGED`` may
carry a short note (after a dash, a colon, a parenthesis or a ``.``/``!`` and a space; up
to :data:`NOTE_MAX_CHARS`), which is kept apart; any other casing must stand alone. The
answer is held while it can still be that; anything else is a normal answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

RevisionSection = Literal["critique", "answer"]
Piece = tuple[RevisionSection, str]
_SectionName = Literal["critique", "answer", "agreement"]
_State = Literal["outside", "critique", "answer", "agreement"]

_TAG_NAMES = ("critique", "answer", "agreement")
_TAG_RE = re.compile(r"<\s*(/?)\s*(critique|answer|agreement)\s*/?\s*>", re.IGNORECASE)
_PARTIAL_TAG_RE = re.compile(r"<\s*/?\s*([A-Za-z]*)(\s*/?\s*)")
_MAX_TAG_CHARS = 40
_SPECIAL_RE = re.compile(r"[<`~\n]")
_HELD_STOP_RE = re.compile(r"[`\n]")
_NAMES: dict[str, _SectionName] = {name: name for name in ("critique", "answer", "agreement")}
_LINE_BLANKS = " \t\r"

NOTE_MAX_CHARS = 200
"""Longest note after UNCHANGED that still keeps the previous answer."""
_WRAP = "*_`\"'\u201c\u201d\u2018\u2019"
"""Characters that may wrap the marker: emphasis, backticks and straight or curly quotes."""
_MARKER_RE = re.compile(rf"[{_WRAP}]*(UNCHANGED)(?![A-Za-z0-9])[{_WRAP}.!]*", re.IGNORECASE)
_MARKER = "UNCHANGED"
"""The marker exactly as the revision prompt asks for it (required before a note)."""
_MARKER_PREFIX_RE = re.compile(
    rf"[{_WRAP}]*(?:U(?:N(?:C(?:H(?:A(?:N(?:G(?:E(?:D)?)?)?)?)?)?)?)?)?", re.IGNORECASE
)
_MAX_MARKER_PREFIX_CHARS = 24
_NOTE_SEPARATORS = "-\u2013\u2014:(["
"""What may introduce a note after the marker: a hyphen, an en or em dash, a colon or an
opening parenthesis or bracket."""
_NOTE_CLOSERS = {"(": ")", "[": "]"}

_FALLBACK_AFTER_CHARS = 200
"""Untagged text longer than this before any tag is streamed as the answer."""
TAIL_MAX_CHARS = 300
"""Most non-whitespace characters after ``</answer>`` held to decide whether the tag
ended the answer (a header or a sign-off is short; an answer that mentions the tag goes
on longer)."""

_NUMBER_RE = re.compile(r"-?\d{1,3}")
_LOOSE_AGREEMENT_RE = re.compile(r"agreement\W{0,5}(\d{1,3})", re.IGNORECASE)
_AGREEMENT_LINE_RE = re.compile(r"agreement\W{0,5}\d", re.IGNORECASE)
_AGREEMENT_WORD = "agreement"
_AGREEMENT_MAX_CHARS = 64


@dataclass(frozen=True, slots=True)
class RevisionParse:
    critique: str
    answer: str
    """The revised answer; the literal ``UNCHANGED`` marker when ``unchanged``."""
    agreement: int | None
    """Agreement with the other agent's answer, clamped to 0-100 (None if missing)."""
    unchanged: bool
    unchanged_note: str | None = None
    """The short note written after UNCHANGED, if any (only when ``unchanged``)."""


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


def _agreement_line(text: str) -> bool | None:
    """Whether ``text`` (what follows ``</answer>``, without leading whitespace) is a loose
    agreement line such as ``Agreement: 80``; None while it may still become one."""
    if _AGREEMENT_LINE_RE.match(text):
        return True
    lowered = text.lower()
    if len(lowered) <= len(_AGREEMENT_WORD):
        return None if _AGREEMENT_WORD.startswith(lowered) else False
    if not lowered.startswith(_AGREEMENT_WORD):
        return False
    rest = lowered[len(_AGREEMENT_WORD) :]
    return None if len(rest) <= 5 and not re.search(r"\w", rest) else False


_Decision = Literal["yes", "no", "maybe"]


def _unchanged(text: str, *, final: bool) -> tuple[_Decision, str | None]:
    """Whether an answer (``text``, without leading whitespace) is the UNCHANGED marker,
    alone or with a short note on its line, and the note. ``"maybe"`` while more text
    could still decide it (never when ``final``)."""
    if not final and len(text) <= _MAX_MARKER_PREFIX_CHARS and _MARKER_PREFIX_RE.fullmatch(text):
        return "maybe", None
    match = _MARKER_RE.match(text)
    if match is None:
        return "no", None
    line, _, later = text[match.end() :].partition("\n")
    if later.strip():
        return "no", None  # more lines: an answer that starts with the word
    note = line.strip()
    if note:
        if match.group(1) != _MARKER:
            return "no", None  # "Unchanged: the rate is 2%." is an answer
        if note[0] in _NOTE_SEPARATORS:
            closer = _NOTE_CLOSERS.get(note[0])
            note = note[1:].strip()
            if closer and note.endswith(closer):
                note = note[:-1]
        elif not (line[:1].isspace() and match.group(0).rstrip(_WRAP)[-1:] in ".!"):
            return "no", None
        note = " ".join(note.split())
        if len(note) > NOTE_MAX_CHARS:
            return "no", None
    if not final:
        return "maybe", None
    return "yes", note or None


class _Section:
    """Accumulates one section: strips outer whitespace while streaming and, for the
    answer, holds back a possible ``UNCHANGED`` marker (and its note) so neither reaches
    the UI."""

    def __init__(self, *, detect_unchanged: bool) -> None:
        self.parts: list[str] = []
        self.opened = False
        self.unchanged = False
        self.note: str | None = None
        self._started = False
        self._pending_ws: list[str] = []
        self._holding = detect_unchanged
        self._held: list[str] = []
        self._marked = False
        """The held text starts with a whole marker: whitespace after it decides nothing."""

    def reopen(self) -> None:
        if self._started and not self._holding:
            self._pending_ws = ["\n\n"]

    def add(self, text: str) -> str:
        if not self._started:
            text = text.lstrip()
            if not text:
                return ""
            self._started = True
        if self._holding:
            self._held.append(text)
            if self._marked and not text.strip():
                return ""
            held = "".join(self._held)
            if _unchanged(held, final=False)[0] == "maybe":
                self._marked = _MARKER_RE.match(held) is not None
                return ""
            self._holding = False
            text, self._held = held, []
        body = text.rstrip()
        if not body:
            self._pending_ws.append(text)
            return ""
        out = "".join(self._pending_ws) + body
        self._pending_ws = [text[len(body) :]]
        self.parts.append(out)
        return out

    def flush(self) -> str:
        """End of the reply: recognise the held text as UNCHANGED, or release it."""
        if not self._holding or not self._held:
            return ""
        self._holding = False
        held, self._held = "".join(self._held).strip(), []
        decision, note = _unchanged(held, final=True)
        if decision == "yes":
            self.unchanged = True
            self.note = note
            return ""
        self.parts.append(held)
        return held

    @property
    def text(self) -> str:
        return "".join(self.parts).strip()


@dataclass(frozen=True, slots=True)
class _Tag:
    """A structural candidate: a known tag outside any code."""

    closing: bool
    name: _SectionName
    raw: str


_Token = str | _Tag


def _run_end(text: str, start: int, char: str) -> int:
    end = start
    while end < len(text) and text[end] == char:
        end += 1
    return end


class _Lexer:
    """Splits the raw reply into text and :class:`_Tag` tokens, following Markdown code so
    that a tag inside a fenced block or an inline code span stays text.

    Fences open on a line that starts (after any indentation) with three or more
    backticks or tildes and close on a line with a run of the same character at least
    as long and nothing else (the last line of the reply needs no line break). An inline
    span opens with a backtick run and closes with a run of the same length on the same
    line.

    Code is only known to be code once it closes. A tag inside a span that is still open
    is held until the span closes (it was code) or the line ends (the backtick was text,
    and the held text is read again without spans). A tag inside an open fence is held
    until the fence closes (it was code) or the reply ends (the fence never closed, so it
    was not one: the held text is read again as prose). Code up to its first tag streams
    at once."""

    def __init__(self) -> None:
        self._buffer = ""
        self._fence: tuple[str, int] | None = None
        """Character and length of the open fence."""
        self._line_start = True
        """Only blanks so far on the current line."""
        self._line_fence: Literal["opener", "closer"] | None = None
        """The current line starts with a fence run: the opening line of a block (its info
        string is code) or, inside a block, a possible closing line."""
        self._run: tuple[str, int] = ("", 0)
        self._opener_invalid = False
        """A backtick fence line with a backtick after its run: not a fence."""
        self._closer_clean = False
        """A possible closing line with nothing but blanks after its run so far."""
        self._inline: int | None = None
        """Length of the backtick run of an inline span open on this line."""
        self._inline_off = False
        """A span failed on this line: the rest of the line has no spans."""
        self._held: list[str] | None = None
        """Raw text from a tag inside an open span until the span is decided."""
        self._fence_held: list[str] | None = None
        """Raw text from a tag inside an open fence until the fence is decided."""

    def feed(self, chunk: str) -> list[_Token]:
        self._buffer += chunk
        out: list[_Token] = []
        self._scan(out, final=False)
        return out

    def close(self) -> list[_Token]:
        out: list[_Token] = []
        while True:
            self._scan(out, final=True)
            if self._held is not None:  # a span still open at the end: it never closed
                self._buffer, self._held = "".join(self._held), None
                self._inline, self._inline_off = None, True
            elif self._fence_held is None:
                break
            elif self._line_fence == "closer" and self._closer_clean:
                # The last line closes the fence: what it held was code.
                out.extend(self._fence_held)
                self._fence_held = None
                break
            else:  # the fence never closed: read what it held again as prose
                self._buffer, self._fence_held = "".join(self._fence_held), None
                self._fence, self._line_fence, self._line_start = None, None, False
                self._inline, self._inline_off = None, False
        self._buffer = ""
        return out

    def _in_code(self) -> bool:
        return self._fence is not None or self._line_fence is not None

    def _put(self, text: str, out: list[_Token]) -> None:
        if self._fence_held is not None:
            self._fence_held.append(text)
        else:
            out.append(text)

    def _scan(self, out: list[_Token], final: bool) -> None:
        text, pos = self._buffer, 0
        while pos < len(text):
            char = text[pos]
            if self._held is not None:
                if char == "`":
                    end = _run_end(text, pos, "`")
                    if end == len(text) and not final:
                        break  # the run may go on
                    run = text[pos:end]
                    if len(run) == self._inline:  # the span closes: what it held was code
                        out.append("".join(self._held) + run)
                        self._held, self._inline = None, None
                    else:
                        self._held.append(run)
                    pos = end
                elif char == "\n":  # the span never closed: read the held text again
                    text = "".join(self._held) + text[pos:]
                    pos = 0
                    self._held, self._inline, self._inline_off = None, None, True
                else:
                    stop = _HELD_STOP_RE.search(text, pos)
                    end = stop.start() if stop else len(text)
                    self._held.append(text[pos:end])
                    pos = end
                continue
            if char == "\n":
                if self._end_line() and self._fence_held is not None:
                    out.extend(self._fence_held)  # the fence closed: what it held was code
                    self._fence_held = None
                self._put(char, out)
                pos += 1
            elif self._line_start and char in _LINE_BLANKS:
                end = pos
                while end < len(text) and text[end] in _LINE_BLANKS:
                    end += 1
                self._put(text[pos:end], out)
                pos = end
            elif char in "`~":
                end = _run_end(text, pos, char)
                if end == len(text) and not final:
                    break  # the run may go on
                self._on_run(char, end - pos)
                self._put(text[pos:end], out)
                pos = end
            elif char == "<":
                match = _TAG_RE.match(text, pos)
                if match is None:
                    if _could_be_tag(text[pos:]):
                        if not final:
                            break  # wait for the rest of the tag
                        if not self._in_code():
                            pos = len(text)  # a truncated tag at the very end: dropped
                            continue
                    self._seen(char)
                    self._put(char, out)
                    pos += 1
                    continue
                raw = match.group(0)
                self._seen(raw)
                if self._in_code():
                    if self._fence is not None and self._fence_held is None:
                        self._fence_held = []  # code only if the fence closes
                    self._put(raw, out)
                elif self._inline is not None:
                    self._held = [raw]
                else:
                    name = _NAMES[match.group(2).lower()]
                    out.append(_Tag(closing=bool(match.group(1)), name=name, raw=raw))
                pos = match.end()
            else:
                found = _SPECIAL_RE.search(text, pos)
                end = found.start() if found else len(text)
                self._seen(text[pos:end])
                self._put(text[pos:end], out)
                pos = end
        self._buffer = text[pos:]

    def _seen(self, text: str) -> None:
        """Text on the current line (a line with content is not a fence line after it)."""
        if text.strip(_LINE_BLANKS):
            self._line_start = False
            if self._line_fence == "closer":
                self._closer_clean = False

    def _on_run(self, char: str, length: int) -> None:
        at_start, self._line_start = self._line_start, False
        if at_start and length >= 3:
            if self._fence is None:
                self._line_fence, self._run, self._opener_invalid = "opener", (char, length), False
            elif char == self._fence[0] and length >= self._fence[1]:
                self._line_fence, self._closer_clean = "closer", True
            return
        if self._line_fence == "closer":
            self._closer_clean = False
        elif self._line_fence == "opener":
            if char == "`" and self._run[0] == "`":
                self._opener_invalid = True
        elif self._fence is None and char == "`" and not self._inline_off:
            if self._inline is None:
                self._inline = length
            elif self._inline == length:
                self._inline = None

    def _end_line(self) -> bool:
        """The current line ended; True when it closed a fence."""
        closed = False
        if self._line_fence == "opener" and not self._opener_invalid:
            self._fence = self._run
        elif self._line_fence == "closer" and self._closer_clean:
            self._fence = None
            closed = True
        self._line_fence = None
        self._line_start = True
        self._inline, self._inline_off = None, False
        return closed


@dataclass(slots=True)
class _PendingClose:
    """A closing tag of the open section whose meaning depends on what follows it."""

    section: RevisionSection
    raw: str
    after: list[str] = field(default_factory=list)
    """Raw text after the tag, not placed yet."""
    chars: int = 0
    """How many characters of that text are not whitespace."""
    held: bool = False
    """Other text follows the tag: waiting for the next tag or the end (after
    ``</answer>``, for at most :data:`TAIL_MAX_CHARS` characters)."""

    @property
    def text(self) -> str:
        return "".join(self.after)


class RevisionStreamParser:
    """Streaming parser of a revision reply (see the module docstring)."""

    def __init__(self) -> None:
        self._lexer = _Lexer()
        self._state: _State = "outside"
        self._seen_tag = False
        self._answer_implicit = False
        """The answer was opened by the untagged fallback, not by an ``<answer>`` tag."""
        self._outside: list[str] = []
        self._agreement_text: str | None = None
        self._raw: list[str] = []
        self._closed = False
        self._pending: _PendingClose | None = None
        self._critique = _Section(detect_unchanged=False)
        self._answer = _Section(detect_unchanged=True)

    def feed(self, chunk: str) -> list[Piece]:
        """Consume a chunk; return the pieces that can be shown now."""
        if self._closed:
            raise RuntimeError("parser already closed")
        self._raw.append(chunk)
        out: list[Piece] = []
        for token in self._lexer.feed(chunk):
            self._token(token, out)
        return out

    def close(self) -> list[Piece]:
        """End of stream: return the remaining pieces to show (idempotent)."""
        if self._closed:
            return []
        self._closed = True
        out: list[Piece] = []
        for token in self._lexer.close():
            self._token(token, out)
        self._end_pending(out)
        if not self._answer.opened:
            untagged = "".join(self._outside)
            if untagged.strip():
                self._answer.opened = True
                self._emit("answer", self._answer.add(untagged), out)
        self._emit("answer", self._answer.flush(), out)
        return out

    def final(self) -> RevisionParse:
        """The parsed reply (closes the parser if needed)."""
        self.close()
        unchanged = self._answer.unchanged
        return RevisionParse(
            critique=self._critique.text,
            answer="UNCHANGED" if unchanged else self._answer.text,
            agreement=self._agreement(),
            unchanged=unchanged,
            unchanged_note=self._answer.note if unchanged else None,
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

    def _token(self, token: _Token, out: list[Piece]) -> None:
        if self._pending is not None and self._resolve(self._pending, token, out):
            return
        if isinstance(token, str):
            self._text(token, out)
        else:
            self._tag(token, out)

    def _resolve(self, pending: _PendingClose, token: _Token, out: list[Piece]) -> bool:
        """Feed ``token`` to a pending closing tag; True when the token was used up."""
        if isinstance(token, _Tag) and token.closing and token.name == pending.section:
            self._settle(pending, structural=False, out=out)  # the first one was text
            return False  # this one is the new candidate
        if isinstance(token, _Tag) and not token.closing:
            if pending.held and pending.section == "critique" and token.name == "critique":
                pending.after.append(token.raw)  # text inside the critique either way
                return True
            self._settle(pending, structural=True, out=out)  # a section starts: it ended
            return False  # the opening tag is handled normally
        # Text, or a closing tag of a section that is not open (text either way).
        piece = token if isinstance(token, str) else token.raw
        pending.after.append(piece)
        pending.chars += len("".join(piece.split()))
        if not pending.held:
            if not pending.chars:
                return True  # whitespace: undecided
            if pending.section == "answer":
                trailer = _agreement_line(pending.text.lstrip())
                if trailer is None:
                    return True
                if trailer:
                    self._settle(pending, structural=True, out=out)
                    return True
            pending.held = True
        if pending.section == "answer" and pending.chars > TAIL_MAX_CHARS:
            self._settle(pending, structural=False, out=out)  # answer text: the tag was too
        return True

    def _settle(self, pending: _PendingClose, *, structural: bool, out: list[Piece]) -> None:
        self._pending = None
        if structural:
            self._state = "outside"
            if pending.after:
                self._text(pending.text, out)
        else:
            self._text(pending.raw + pending.text, out)

    def _end_pending(self, out: list[Piece]) -> None:
        """End of the reply: a closing tag still undecided was the real end."""
        if self._pending is not None:
            self._settle(self._pending, structural=True, out=out)

    def _tag(self, tag: _Tag, out: list[Piece]) -> None:
        state = self._state
        if tag.closing:
            if tag.name != state:
                self._text(tag.raw, out)  # closes nothing: text
            elif state == "critique":
                self._pending = _PendingClose(section="critique", raw=tag.raw)
            elif state == "answer":
                self._pending = _PendingClose(section="answer", raw=tag.raw)
            else:
                self._state = "outside"
            return
        if tag.name == state and (
            state == "critique" or (state == "answer" and not self._answer_implicit)
        ):
            self._text(tag.raw, out)  # the open section's own opening tag: text
            return
        self._seen_tag = True
        if tag.name == "critique":
            self._critique.reopen()
            self._critique.opened = True
        elif tag.name == "answer":
            self._answer.reopen()
            self._answer.opened = True
            self._answer_implicit = False
        else:
            self._agreement_text = ""
        self._state = tag.name

    def _text(self, text: str, out: list[Piece]) -> None:
        if self._state == "critique":
            self._emit("critique", self._critique.add(text), out)
        elif self._state == "answer":
            self._emit("answer", self._answer.add(text), out)
        elif self._state == "agreement":
            if self._agreement_text is not None:
                # Only its start matters; cut the same way whatever the chunking.
                self._agreement_text = (self._agreement_text + text)[:_AGREEMENT_MAX_CHARS]
        else:
            self._outside.append(text)
            if not self._seen_tag and not self._answer.opened:
                untagged = "".join(self._outside)
                if len(untagged.strip()) > _FALLBACK_AFTER_CHARS:
                    # The model is ignoring the format: stream its text as the answer.
                    self._outside.clear()
                    self._answer.opened = True
                    self._answer_implicit = True
                    self._state = "answer"
                    self._emit("answer", self._answer.add(untagged), out)

    @staticmethod
    def _emit(section: RevisionSection, text: str, out: list[Piece]) -> None:
        if text:
            out.append((section, text))
