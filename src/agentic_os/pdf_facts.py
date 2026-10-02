"""Facts about a PDF's pages, and Claude's check of their text (docs/adr/0009-attachments.md).

- :class:`PdfPage`: what the server's PDF reader finds on each page when the file is
  uploaded, in every mode: where the page's text is in the stored text, and the signs of
  a page without text (a scan), of a broken text layer and of text that is not visible.
  :func:`pdf_notes` turns them into the warnings that the owner and the models see.
- :class:`PdfCheck`: Claude's reading of the pages whose extracted text would mislead
  ChatGPT with the subscription (Codex cannot open a PDF, so it reads the text): only
  the pages that differ, as JSON lines that :func:`parse_findings` reads strictly. The
  engine stores it and every later turn reuses it.

Pure data and pure functions: nothing here reads files or calls a model.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast

CHECK_VERSION: Final = 2
"""Version of Claude's check (its prompt, its output format and what makes a check): a
stored check of another version is never reused. 2: version 1 also stored the demo
Claude's canned check, which passes every page as right, and gave the pages that may hide
text too small an output budget, so its partial checks stopped short of them for good."""

NO_TEXT_LETTERS: Final = 25
"""A page with fewer letters in its extracted text has no text worth the name: a scan,
or text drawn as an image."""
GARBAGE_MIN: Final = 5
GARBAGE_RATIO: Final = 0.05
"""A page's text is garbled when at least this many of its characters, and this share of
them, are replacement, private-use or control characters (a font without a usable
character map)."""
HIDDEN_MIN: Final = 10
"""Characters shown invisibly (on a page without images: over a scan, an invisible layer
is the scan's recognized text), smaller than 1 point or outside the page's visible box
from which a page is suspected of hiding text."""

MAX_FINDING_TEXT: Final = 20_000
"""Longest text of one finding (a page as Claude reads it)."""
MAX_FINDING_NOTE: Final = 2_000
"""Longest hidden text quote or visual description of one finding."""


@dataclass(frozen=True, slots=True)
class PdfPage:
    """What the reader found on one page (``number`` counts from 1). The counts are
    characters; those of hidden text count the strings the page shows (approximate: a
    two-byte font counts each character twice)."""

    number: int
    start: int | None
    """The page's text is ``text[start:end]`` of the attachment's stored text; None when
    nothing of it is stored (it has no text, or the stored text's limit cut it off)."""
    end: int | None
    cut: bool
    """The stored text's limit cut off this page's text, all of it or its end."""
    chars: int
    """Characters of its extracted text other than white space."""
    letters: int
    garbage: int
    """Replacement (U+FFFD), private-use and control characters of its text."""
    images: bool
    """It draws an image (a scan, a photo, a chart saved as a picture)."""
    invisible: int
    """Characters shown in an invisible render mode (3 or 7)."""
    tiny: int
    """Characters shown smaller than 1 point."""
    offpage: int
    """Characters shown outside the page's visible box."""

    @property
    def no_text(self) -> bool:
        return self.letters < NO_TEXT_LETTERS

    @property
    def garbled(self) -> bool:
        return self.garbage >= GARBAGE_MIN and self.garbage >= GARBAGE_RATIO * max(self.chars, 1)

    @property
    def hidden(self) -> bool:
        invisible = self.invisible >= HIDDEN_MIN and not self.images
        return invisible or self.tiny >= HIDDEN_MIN or self.offpage >= HIDDEN_MIN

    def to_data(self) -> dict[str, int | bool | None]:
        return {
            "number": self.number,
            "start": self.start,
            "end": self.end,
            "cut": self.cut,
            "chars": self.chars,
            "letters": self.letters,
            "garbage": self.garbage,
            "images": self.images,
            "invisible": self.invisible,
            "tiny": self.tiny,
            "offpage": self.offpage,
        }


_COUNTS: Final = ("chars", "letters", "garbage", "invisible", "tiny", "offpage")


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _page(number: int, value: object) -> PdfPage | None:
    if not isinstance(value, dict):
        return None
    data = cast("dict[str, object]", value)
    if data.get("number") != number:
        return None
    start, end = data.get("start"), data.get("end")
    if start is None and end is None:
        span: tuple[int | None, int | None] = (None, None)
    else:
        first, last = _count(start), _count(end)
        if first is None or last is None or first > last:
            return None
        span = (first, last)
    counts = [_count(data.get(key)) for key in _COUNTS]
    cut, images = data.get("cut"), data.get("images")
    if any(count is None for count in counts) or not isinstance(cut, bool):
        return None
    if not isinstance(images, bool):
        return None
    chars, letters, garbage, invisible, tiny, offpage = cast("list[int]", counts)
    return PdfPage(
        number=number,
        start=span[0],
        end=span[1],
        cut=cut,
        chars=chars,
        letters=letters,
        garbage=garbage,
        images=images,
        invisible=invisible,
        tiny=tiny,
        offpage=offpage,
    )


def pages_from_data(value: object) -> tuple[PdfPage, ...] | None:
    """The pages of a list of :meth:`PdfPage.to_data` objects (what the reader's process
    writes, and what the database keeps), or None if any of them is not valid: the
    numbers must be 1, 2, 3... and the counts integers of at least 0."""
    if not isinstance(value, list) or not value:
        return None
    pages = [_page(number, item) for number, item in enumerate(value, start=1)]
    if any(page is None for page in pages):
        return None
    return tuple(cast("list[PdfPage]", pages))


def pages_to_json(pages: Sequence[PdfPage]) -> str:
    return json.dumps([page.to_data() for page in pages], separators=(",", ":"))


def pages_from_json(raw: str | None) -> tuple[PdfPage, ...] | None:
    """The pages stored as :func:`pages_to_json`, or None when there are none or the
    value is not valid (it is then as if the PDF had not been analysed)."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return pages_from_data(value)


@dataclass(frozen=True, slots=True)
class PdfNotes:
    """The pages of a PDF that deserve a warning, by kind (numbers from 1)."""

    no_text: tuple[int, ...] = ()
    """Pages without text: scans, or text drawn as images."""
    garbled: tuple[int, ...] = ()
    """Pages whose extracted text is unreadable."""
    hidden: tuple[int, ...] = ()
    """Pages that may hold text that is not visible."""

    def to_wire(self) -> dict[str, list[int]]:
        return {
            "no_text": list(self.no_text),
            "garbled": list(self.garbled),
            "hidden": list(self.hidden),
        }


def pdf_notes(pages: Sequence[PdfPage]) -> PdfNotes:
    """The warnings of a PDF's pages (:attr:`PdfPage.no_text`, ``garbled``, ``hidden``).
    A page without text is not also called garbled."""
    return PdfNotes(
        no_text=tuple(page.number for page in pages if page.no_text),
        garbled=tuple(page.number for page in pages if page.garbled and not page.no_text),
        hidden=tuple(page.number for page in pages if page.hidden),
    )


# -- Claude's check --------------------------------------------------------------------------

PageStatus = Literal["ok", "missing", "garbled", "partial", "hidden"]
"""How a page's extracted text compares with the page as Claude sees it:

- ``missing``: the page shows text that the extraction lacks (a scan, text in images);
  ``text`` is the page's text as Claude reads it.
- ``garbled``: the extracted text is unreadable or wrong; ``text`` as above.
- ``partial``: the extraction lacks part of the visible text; ``text`` is only that part.
- ``hidden``: the extraction has text that the page does not show; ``text`` is the
  visible text only, and ``hidden`` a short quote of the rest (never passed on).
- ``ok``: the text is right; the finding only carries a ``visual`` description.

Any finding may have ``visual``: what the page's figures, charts, tables or images say."""

PAGE_STATUSES: Final[frozenset[str]] = frozenset({"ok", "missing", "garbled", "partial", "hidden"})
_NEEDS_TEXT: Final = frozenset({"missing", "garbled", "partial"})
READ_BY_CLAUDE: Final[frozenset[str]] = frozenset({"missing", "garbled", "partial", "hidden"})
"""Statuses whose page's text ChatGPT reads, all of it or in part, through Claude (so does
what the figures of a page with a ``visual`` description show: :attr:`PdfCheck.claude_pages`)."""


@dataclass(frozen=True, slots=True)
class PageFinding:
    page: int
    status: PageStatus
    text: str = ""
    hidden: str = ""
    visual: str = ""

    def to_data(self) -> dict[str, int | str]:
        data: dict[str, int | str] = {"page": self.page, "status": self.status}
        for key, value in (("text", self.text), ("hidden", self.hidden), ("visual", self.visual)):
            if value:
                data[key] = value
        return data


def _text_field(data: dict[str, object], key: str, limit: int) -> str | None:
    """A finding's optional text field ("" when absent), or None when it is not valid."""
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        return None
    return value.strip()


def finding_from_data(value: object, *, after: int, last: int) -> PageFinding | None:
    """A finding as Claude writes it (:meth:`PageFinding.to_data`), or None when it is not
    valid: its page must come after ``after`` and be at most ``last``, its status must be
    known, and a missing, garbled or partial page must come with its text."""
    if not isinstance(value, dict):
        return None
    data = cast("dict[str, object]", value)
    page, status = data.get("page"), data.get("status")
    if not isinstance(page, int) or isinstance(page, bool) or not after < page <= last:
        return None
    if not isinstance(status, str) or status not in PAGE_STATUSES:
        return None
    text = _text_field(data, "text", MAX_FINDING_TEXT)
    hidden = _text_field(data, "hidden", MAX_FINDING_NOTE)
    visual = _text_field(data, "visual", MAX_FINDING_NOTE)
    if text is None or hidden is None or visual is None:
        return None
    if status in _NEEDS_TEXT and not text:
        return None
    return PageFinding(page, cast("PageStatus", status), text, hidden, visual)


@dataclass(frozen=True, slots=True)
class ParsedFindings:
    findings: tuple[PageFinding, ...]
    covered: int
    """The last page that the output checked: every page from the first asked for up to
    this one has its finding or is right. Less than the first page when none was."""
    ended: bool
    """The output ended with ``{"end": true}``: every page asked for was checked."""


def parse_findings(output: str, *, first: int, last: int) -> ParsedFindings:
    """Claude's findings for pages ``first`` to ``last``, one JSON object per line in
    page order, then ``{"end": true}``. Read strictly: blank lines and Markdown fences
    are skipped, and so are lines of prose before the first JSON line; after it, the
    first line that is not a valid finding (malformed, out of order, cut off by the
    output budget) ends the reading, and only the pages up to the last valid finding
    count as checked. A page that was not reported is right only up to there."""
    findings: list[PageFinding] = []
    covered = first - 1
    ended = False
    started = False
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        try:
            value: object = json.loads(line)
        except ValueError:
            if started:
                break
            continue
        started = True
        if isinstance(value, dict) and value.get("end") is True:
            ended = True
            break
        finding = finding_from_data(value, after=covered, last=last)
        if finding is None:
            break
        covered = finding.page
        if finding.status != "ok" or finding.visual:
            findings.append(finding)
    if ended:
        covered = last
    return ParsedFindings(tuple(findings), covered, ended)


@dataclass(frozen=True, slots=True)
class PdfCheck:
    """Claude's check of a PDF's text: the pages from 1 to ``covered`` were checked (a
    page without a finding is right), the rest were not."""

    version: int
    model: str
    """The Claude model that read the PDF."""
    pages: int
    covered: int
    findings: tuple[PageFinding, ...] = ()

    def finding(self, page: int) -> PageFinding | None:
        return next((finding for finding in self.findings if finding.page == page), None)

    @property
    def complete(self) -> bool:
        return self.covered >= self.pages

    @property
    def claude_pages(self) -> tuple[int, ...]:
        """Pages ChatGPT reads, all or in part, through Claude: their text (missing,
        garbled, partial or with text that is not visible) or what their figures show (a
        page with a ``visual`` description, its text right or not)."""
        return tuple(f.page for f in self.findings if f.status in READ_BY_CLAUDE or f.visual)

    @property
    def hidden_pages(self) -> tuple[int, ...]:
        return tuple(f.page for f in self.findings if f.status == "hidden")

    @property
    def unchecked_pages(self) -> tuple[int, ...]:
        return tuple(range(max(self.covered, 0) + 1, self.pages + 1))

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "model": self.model,
                "pages": self.pages,
                "covered": self.covered,
                "findings": [finding.to_data() for finding in self.findings],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def check_from_json(raw: str | None) -> PdfCheck | None:
    """A check stored as :meth:`PdfCheck.to_json`, or None when there is none or it is
    not valid (it is then checked again)."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    data = cast("dict[str, object]", value)
    version, model = data.get("version"), data.get("model")
    pages, covered = _count(data.get("pages")), _count(data.get("covered"))
    items = data.get("findings")
    if not isinstance(version, int) or not isinstance(model, str) or not isinstance(items, list):
        return None
    if pages is None or covered is None or covered > pages:
        return None
    findings: list[PageFinding] = []
    for item in items:
        finding = finding_from_data(item, after=findings[-1].page if findings else 0, last=covered)
        if finding is None:
            return None
        findings.append(finding)
    return PdfCheck(version, model, pages, covered, tuple(findings))
