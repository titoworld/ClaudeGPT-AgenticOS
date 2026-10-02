"""Searching conversations by title (docs/PROTOCOL.md, «Llista i cerca de converses»).

A title matches when it contains the searched text, without telling case or accents
apart: both sides are folded the same way (:func:`fold`), the titles by the SQL
function :data:`FOLD_FUNCTION` that :class:`~agentic_os.storage.db.Database` registers
on its connection. The match is literal: the text goes into a ``LIKE`` pattern with
``%``, ``_`` and the escape character itself escaped.
"""

from __future__ import annotations

import unicodedata
from typing import Final

MAX_SEARCH_LENGTH: Final = 200
"""Longest searched text, once trimmed (as long as the longest title)."""
FOLD_FUNCTION: Final = "aos_fold"
"""Name of the SQL function that applies :func:`fold` to a text."""
LIKE_ESCAPE: Final = "\\"


def fold(text: str) -> str:
    """``text`` without case or accents: compatibility decomposition (NFKD), case
    folding, and the combining marks dropped (Unicode's compatibility caseless match,
    plus accents). "Cafè", "CAFE" and "cafe" fold alike, and so do "Straße" and
    "strasse", or the "ﬁ" ligature and "fi". NUL characters are dropped too: SQLite's
    ``LIKE`` would take them as the end of the text."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", text).casefold())
    return "".join(
        char for char in decomposed if char != "\x00" and not unicodedata.combining(char)
    )


def sql_fold(value: object) -> object:
    """:func:`fold` as a SQL function: text is folded, anything else (NULL) is kept."""
    return fold(value) if isinstance(value, str) else value


def search_pattern(query: str | None) -> str | None:
    """The ``LIKE`` pattern (with :data:`LIKE_ESCAPE`) of a searched text, to compare
    with the folded titles; ``None`` when there is nothing to search (no text, or only
    blank space). The text is trimmed and its runs of blank space count as one, as in
    the stored titles. Raises :class:`ValueError` (Catalan) for a text longer than
    :data:`MAX_SEARCH_LENGTH` once trimmed."""
    if query is None:
        return None
    text = query.strip()
    if len(text) > MAX_SEARCH_LENGTH:
        raise ValueError(f"La cerca no pot tenir més de {MAX_SEARCH_LENGTH} caràcters.")
    folded = fold(" ".join(text.split()))
    if not folded:
        return None
    escaped = "".join(
        LIKE_ESCAPE + char if char in ("%", "_", LIKE_ESCAPE) else char for char in folded
    )
    return f"%{escaped}%"
