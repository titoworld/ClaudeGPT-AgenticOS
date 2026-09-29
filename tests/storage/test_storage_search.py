"""Searching the conversations by title (audit point 12).

``list_conversations(query=...)`` keeps the order (newest activity first) and the
``before`` cursor of the plain list, so a search pages like the list does. A title
matches when it contains the text, without telling case or accents apart (both sides
are folded: NFKD, casefold, combining marks dropped) and literally: ``%``, ``_`` and
``\\`` are ordinary characters.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.storage import MAX_SEARCH_LENGTH, SqliteStore

T0 = datetime(2026, 9, 28, 8, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def store(tmp_path: Path, clock: Clock) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3", clock=clock) as store:
        yield store


async def create(store: SqliteStore, clock: Clock, titles: Sequence[str]) -> list[int]:
    """Create the conversations oldest first, one second apart."""
    ids = []
    for title in titles:
        clock.now += timedelta(seconds=1)
        ids.append(await store.create_conversation(title))
    return ids


async def titles(store: SqliteStore, query: str | None, limit: int = 50) -> list[str]:
    return [c.title for c in await store.list_conversations(limit=limit, query=query)]


async def test_the_search_ignores_case_and_accents(store: SqliteStore, clock: Clock) -> None:
    await create(
        store,
        clock,
        [
            "Pressupost de l'ÀREA",
            "Cafè amb llet",
            "CAFE sol",
            "Façana del carrer",
            "El vol del Ñandú",
            "Die Straße",
            "ﬁnances del mes",  # the "fi" ligature
            "Resum setmanal",
        ],
    )
    assert await titles(store, "area") == ["Pressupost de l'ÀREA"]
    assert await titles(store, "càfe") == ["CAFE sol", "Cafè amb llet"]
    assert await titles(store, "CAFÉ AMB") == ["Cafè amb llet"]
    assert await titles(store, "facana") == ["Façana del carrer"]
    assert await titles(store, "FAÇ") == ["Façana del carrer"]
    assert await titles(store, "nandu") == ["El vol del Ñandú"]
    assert await titles(store, "STRASSE") == ["Die Straße"]
    assert await titles(store, "fin") == ["ﬁnances del mes"]
    assert await titles(store, "setmanal resum") == []


async def test_the_search_is_literal(store: SqliteStore, clock: Clock) -> None:
    await create(
        store,
        clock,
        ["100% natural", "100 natural", "a_b", "axb", "ruta\\fitxer", "ruta/fitxer", "tot"],
    )
    assert await titles(store, "100%") == ["100% natural"]
    assert await titles(store, "%") == ["100% natural"]
    assert await titles(store, "a_b") == ["a_b"]
    assert await titles(store, "_") == ["a_b"]
    assert await titles(store, "\\") == ["ruta\\fitxer"]
    assert await titles(store, "ta\\fi") == ["ruta\\fitxer"]
    # Fullwidth "100%" folds to "100%", whose "%" still is only a character.
    assert await titles(store, "\uff11\uff10\uff10\uff05") == ["100% natural"]


async def test_a_nul_character_does_not_cut_the_text(store: SqliteStore, clock: Clock) -> None:
    # SQLite's LIKE takes a NUL as the end of a text: "%a\x00b%" would be "%a".
    await create(store, clock, ["ab\x00cd", "xa"])
    assert await titles(store, "cd") == ["ab\x00cd"]
    assert await titles(store, "a\x00b") == ["ab\x00cd"]


async def test_the_search_trims_the_text_and_runs_of_blank_space_count_as_one(
    store: SqliteStore, clock: Clock
) -> None:
    await create(store, clock, ["Hola món", "Adéu"])
    assert await titles(store, "  hola  ") == ["Hola món"]
    assert await titles(store, "HOLA \t  MON") == ["Hola món"]
    # A blank search is no search: the whole list, as without one.
    everything = await titles(store, None)
    assert everything == ["Adéu", "Hola món"]
    assert await titles(store, "") == everything
    assert await titles(store, " \n ") == everything


async def test_a_search_finds_conversations_beyond_the_first_page(
    store: SqliteStore, clock: Clock
) -> None:
    # The audit's case: 60 conversations and the one looked for is the 56th newest.
    ids = await create(
        store,
        clock,
        [
            "Pressupost zebra irrepetible" if i == 5 else f"Conversa número {i}"
            for i in range(1, 61)
        ],
    )
    first_page = await store.list_conversations(limit=50)
    assert ids[4] not in [c.id for c in first_page]
    found = await store.list_conversations(limit=50, query="ZÈBRA")
    assert [c.id for c in found] == [ids[4]]


async def test_a_search_pages_with_the_before_cursor(store: SqliteStore, clock: Clock) -> None:
    all_titles = [f"Informe {i}" if i % 2 else f"Nota {i}" for i in range(120)]
    ids = await create(store, clock, all_titles)
    expected = [cid for cid, title in zip(ids, all_titles, strict=True) if "Informe" in title]
    expected.reverse()  # newest first

    pages: list[list[int]] = []
    before: int | None = None
    while True:
        page = await store.list_conversations(limit=25, before=before, query="INFÒRME")
        if not page:
            break
        pages.append([c.id for c in page])
        before = page[-1].id
    assert [len(page) for page in pages] == [25, 25, 10]
    assert [cid for page in pages for cid in page] == expected
    # A cursor that no longer exists gives no page, as without a search.
    assert await store.list_conversations(before=10**9, query="informe") == []


async def test_the_search_text_may_have_up_to_the_maximum_length(
    store: SqliteStore, clock: Clock
) -> None:
    title = "x" * MAX_SEARCH_LENGTH
    await create(store, clock, [title])
    assert MAX_SEARCH_LENGTH == 200
    assert await titles(store, f"  {title}  ") == [title]
    with pytest.raises(ValueError) as caught:
        await store.list_conversations(query="x" * (MAX_SEARCH_LENGTH + 1))
    assert str(caught.value) == "La cerca no pot tenir més de 200 caràcters."
