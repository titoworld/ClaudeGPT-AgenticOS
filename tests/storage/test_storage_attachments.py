"""Attachments in the SQLite store and their files (docs/adr/0009-attachments.md): the
migrations, content-addressed private files, links to questions, deletion, the sweep of
orphans and leftovers, the engine's contract (get_attachments, link_attachments), the
facts of a PDF's pages and Claude's stored check of its text."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import stat
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from attachment_files import png
from orchestrator.attachment_fixtures import analysed_pages

from agentic_os.orchestrator.store import AttachmentNotFoundError, NewMessage, Store
from agentic_os.pdf_facts import CHECK_VERSION, PageFinding, PdfCheck, PdfNotes, PdfPage
from agentic_os.providers.base import AttachmentKind
from agentic_os.storage import AttachmentInUseError, AttachmentRecord, SqliteStore
from agentic_os.storage.db import MIGRATIONS, SCHEMA_VERSION, Database
from agentic_os.storage.files import AttachmentFiles

T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def store(tmp_path: Path, clock: FakeClock) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "data" / "db.sqlite3", clock=clock) as store:
        yield store


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


async def upload(
    store: SqliteStore,
    data: bytes,
    *,
    name: str = "nota.txt",
    kind: str = "text",
    pdf_pages: tuple[PdfPage, ...] | None = None,
) -> AttachmentRecord:
    incoming = store.new_upload()
    incoming.write(data[:3])
    incoming.write(data[3:])
    incoming.finish()
    if kind == "image":
        return await store.add_attachment(
            incoming, kind="image", mime="image/png", name=name, width=10, height=20
        )
    if kind == "pdf":
        return await store.add_attachment(
            incoming,
            kind="pdf",
            mime="application/pdf",
            name=name,
            pages=len(pdf_pages) if pdf_pages is not None else 2,
            text=None,
            pdf_pages=pdf_pages,
        )
    return await store.add_attachment(
        incoming, kind="text", mime="text/plain", name=name, text=data.decode()
    )


async def question(store: SqliteStore, conversation_id: int, text: str = "Pregunta") -> int:
    return await store.add_message(
        NewMessage(conversation_id, "question", text, final=True, meta={"mode": "solo"})
    )


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- migration -----------------------------------------------------------------------------


async def test_version_3_databases_get_the_attachment_tables(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with contextlib.closing(sqlite3.connect(path)) as conn:
        for statement in (*MIGRATIONS[0], *MIGRATIONS[1], *MIGRATIONS[2]):
            conn.execute(statement)
        conn.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    db = await Database.open(path)
    try:
        assert await db.schema_version() == SCHEMA_VERSION == 6
        async with db.transaction(write=False) as tx:
            tables = {r[0] for r in await tx.fetchall("SELECT name FROM sqlite_master")}
            kept = await tx.fetchone("SELECT value FROM settings WHERE key = 'a'")
        assert {"attachments", "message_attachments", "pdf_checks"} <= tables
        assert kept is not None and kept[0] == "1"
    finally:
        await db.close()


async def test_version_4_databases_get_the_page_facts_and_the_checks(tmp_path: Path) -> None:
    """Migration 5: the PDFs uploaded before keep their text and have no page facts (they
    are read as before, unchecked), and Claude's checks get their table."""
    path = tmp_path / "data" / "db.sqlite3"
    path.parent.mkdir()
    data = b"%PDF-1.7 un PDF d'abans"
    stored = tmp_path / "data" / "attachments" / sha(data)[:2] / sha(data)
    stored.parent.mkdir(parents=True)
    stored.write_bytes(data)
    with contextlib.closing(sqlite3.connect(path)) as conn:
        for statement in (*MIGRATIONS[0], *MIGRATIONS[1], *MIGRATIONS[2], *MIGRATIONS[3]):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO attachments (id, sha256, kind, mime, name, size, pages, text, "
            "created_at) VALUES (7, ?, 'pdf', 'application/pdf', 'abans.pdf', ?, 2, ?, ?)",
            (sha(data), len(data), "--- Pàgina 1 ---\nHola", "2026-09-28T10:00:00.000Z"),
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    async with await SqliteStore.open(path) as store:
        async with store._db.transaction(write=False) as tx:
            assert await tx.user_version() == SCHEMA_VERSION == 6
        [old] = await store.get_attachments([7])
        assert (old.text, old.pdf_pages, old.pdf_notes) == ("--- Pàgina 1 ---\nHola", None, None)
        record = await store.get_attachment(7)
        assert record is not None and record.pdf_notes is None
        assert record.to_wire()["pdf_notes"] is None
        assert await store.get_pdf_check(sha(data)) is None
        await store.put_pdf_check(sha(data), CHECK)
        assert await store.get_pdf_check(sha(data)) == CHECK


# -- files ---------------------------------------------------------------------------------


async def test_uploads_are_stored_by_content_in_private_files(
    store: SqliteStore, tmp_path: Path
) -> None:
    data = "Bon dia, món!\n".encode()
    record = await upload(store, data, name="salutació.txt")
    root = tmp_path / "data" / "attachments"
    stored = root / sha(data)[:2] / sha(data)
    assert store.content_path(record.sha256) == stored
    assert stored.read_bytes() == data
    assert mode(root) == 0o700
    assert mode(stored.parent) == 0o700
    assert mode(stored) == 0o600
    assert not list((root / "incoming").iterdir())  # the upload was moved into place
    assert record == AttachmentRecord(
        id=record.id,
        sha256=sha(data),
        kind="text",
        mime="text/plain",
        name="salutació.txt",
        size=len(data),
        pages=None,
        width=None,
        height=None,
        text_chars=len(data.decode()),
        has_thumbnail=False,
        created_at=T0,
    )
    assert await store.get_attachment(record.id) == record
    assert await store.get_attachment(record.id + 1) is None
    assert record.to_wire() == {
        "id": record.id,
        "name": "salutació.txt",
        "kind": "text",
        "mime": "text/plain",
        "size": len(data),
        "pages": None,
        "width": None,
        "height": None,
        "sha256": sha(data),
        "created_at": "2026-09-29T12:00:00.000Z",
        "has_thumbnail": False,
        "text_available": True,
        "estimated_tokens": 4,
        "pdf_notes": None,
    }


async def test_the_same_content_is_stored_once(store: SqliteStore) -> None:
    data = png(10, 20)
    first = await upload(store, data, name="a.png", kind="image")
    second = await upload(store, data, name="b.png", kind="image")
    assert first.id != second.id
    assert first.sha256 == second.sha256
    path = store.content_path(first.sha256)
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    assert not list((path.parent.parent / "incoming").iterdir())


def test_an_incoming_file_is_private_and_can_be_discarded(tmp_path: Path) -> None:
    files = AttachmentFiles(tmp_path / "attachments")
    incoming = files.incoming()
    assert mode(tmp_path / "attachments") == 0o700
    assert mode(tmp_path / "attachments" / "incoming") == 0o700
    assert mode(incoming.path) == 0o600
    incoming.write(b"abc")
    assert incoming.size == 3
    incoming.discard()
    incoming.discard()
    assert not incoming.path.exists()


async def test_an_unfinished_upload_cannot_be_stored(store: SqliteStore) -> None:
    incoming = store.new_upload()
    incoming.write(b"x")
    with pytest.raises(ValueError):
        await store.add_attachment(incoming, kind="text", mime="text/plain", name="x.txt")
    incoming.discard()


def test_paths_are_only_built_from_hashes_and_ids(tmp_path: Path) -> None:
    files = AttachmentFiles(tmp_path)
    for bad in ("../../etc/passwd", "a" * 63, "A" * 64, "a" * 63 + "/", ""):
        with pytest.raises(ValueError):
            files.content_path(bad)
    with pytest.raises(TypeError):
        files.thumbnail_path("1/../../x")  # type: ignore[arg-type]
    assert files.thumbnail_path(12) == tmp_path / "thumbnails" / "12"


# -- the engine's contract -----------------------------------------------------------------


async def test_get_attachments_in_the_given_order(store: SqliteStore, clock: FakeClock) -> None:
    engine_store: Store = store  # checked by mypy
    text = await upload(store, "Línia 1\nLínia 2".encode(), name="notes.md")
    clock.advance(seconds=5)
    image = await upload(store, png(10, 20), name="foto.png", kind="image")
    assert await store.set_thumbnail(image.id, png(5, 10))
    loaded = await engine_store.get_attachments([image.id, text.id])
    assert [a.name for a in loaded] == ["foto.png", "notes.md"]
    first, second = loaded
    assert (first.kind, first.mime, first.width, first.height) == ("image", "image/png", 10, 20)
    assert (first.text, first.mode, first.has_thumbnail) == (None, "full", True)
    assert first.created_at == T0 + timedelta(seconds=5)
    assert first.path == store.content_path(image.sha256)
    assert first.path.is_absolute()
    assert first.read() == png(10, 20)
    assert (second.kind, second.text, second.has_thumbnail) == ("text", "Línia 1\nLínia 2", False)
    assert await store.get_attachments([]) == []
    assert [a.name for a in await store.get_attachments([text.id, text.id])] == ["notes.md"] * 2


async def test_a_missing_attachment_names_the_first_missing_id(store: SqliteStore) -> None:
    record = await upload(store, b"hola")
    with pytest.raises(AttachmentNotFoundError) as missing:
        await store.get_attachments([record.id, 999, 998])
    assert missing.value.attachment_id == 999
    assert str(missing.value) == "L'adjunt 999 no existeix."
    store.content_path(record.sha256).unlink()  # its file is gone: missing too
    with pytest.raises(AttachmentNotFoundError) as missing:
        await store.get_attachments([record.id])
    assert missing.value.attachment_id == record.id


async def test_link_attachments_to_a_question(store: SqliteStore) -> None:
    conversation_id = await store.create_conversation("Amb adjunts")
    question_id = await question(store, conversation_id)
    a = await upload(store, b"a")
    b = await upload(store, b"b")
    await store.link_attachments(question_id, [b.id, a.id])
    async with store._db.transaction(write=False) as tx:
        rows = await tx.fetchall(
            "SELECT attachment_id, position FROM message_attachments WHERE message_id = ? "
            "ORDER BY position",
            (question_id,),
        )
    assert [tuple(row) for row in rows] == [(b.id, 0), (a.id, 1)]
    with pytest.raises(ValueError, match="already has attachments"):
        await store.link_attachments(question_id, [a.id])


async def test_link_attachments_refuses_what_it_cannot_link(store: SqliteStore) -> None:
    conversation_id = await store.create_conversation("C")
    question_id = await question(store, conversation_id)
    answer_id = await store.add_message(
        NewMessage(conversation_id, "answer", "R", turn_id=question_id, agent="claude")
    )
    a = await upload(store, b"a")
    with pytest.raises(ValueError, match="not a question"):
        await store.link_attachments(answer_id, [a.id])
    with pytest.raises(ValueError, match="not a question"):
        await store.link_attachments(12345, [a.id])
    with pytest.raises(ValueError, match="twice"):
        await store.link_attachments(question_id, [a.id, a.id])
    with pytest.raises(AttachmentNotFoundError) as missing:
        await store.link_attachments(question_id, [a.id, 777])
    assert missing.value.attachment_id == 777
    # Nothing was linked: the attachment is still an orphan and can be deleted.
    assert await store.delete_attachment(a.id)


async def links(store: SqliteStore) -> list[tuple[int, int, int]]:
    async with store._db.transaction(write=False) as tx:
        rows = await tx.fetchall(
            "SELECT message_id, attachment_id, position FROM message_attachments "
            "ORDER BY message_id, position"
        )
    return [(int(row[0]), int(row[1]), int(row[2])) for row in rows]


async def test_a_question_is_stored_with_its_attachments_in_one_transaction(
    store: SqliteStore, clock: FakeClock
) -> None:
    """The engine stores a question and its links together: a question never exists
    without the attachments its ``meta.attachments`` describes."""
    conversation_id = await store.create_conversation("C")
    a = await upload(store, b"a")
    b = await upload(store, b"b")
    before = await store.get_conversation(conversation_id)
    clock.advance(minutes=1)
    with pytest.raises(AttachmentNotFoundError) as missing:
        await store.add_message(
            NewMessage(conversation_id, "question", "Q", final=True, attachments=(a.id, 777))
        )
    assert missing.value.attachment_id == 777
    with pytest.raises(ValueError, match="twice"):
        await store.add_message(
            NewMessage(conversation_id, "question", "Q", final=True, attachments=(a.id, a.id))
        )
    # Nothing was stored, not even the conversation's new updated_at.
    assert await store.get_conversation(conversation_id) == before
    assert await links(store) == []

    question_id = await store.add_message(
        NewMessage(conversation_id, "question", "Q", final=True, attachments=(b.id, a.id))
    )
    assert await links(store) == [(question_id, b.id, 0), (question_id, a.id, 1)]
    with pytest.raises(AttachmentInUseError):  # sent: kept with its conversation
        await store.delete_attachment(a.id)
    with pytest.raises(ValueError, match="only a question"):
        await store.add_message(
            NewMessage(
                conversation_id,
                "answer",
                "R",
                turn_id=question_id,
                agent="claude",
                attachments=(a.id,),
            )
        )
    detail = await store.get_conversation(conversation_id)
    assert detail is not None and [m.id for m in detail.messages] == [question_id]


async def test_only_an_empty_conversation_is_discarded(store: SqliteStore) -> None:
    empty = await store.create_conversation("Buida")
    used = await store.create_conversation("Amb una pregunta")
    await question(store, used)
    assert await store.discard_conversation(empty)
    assert await store.get_conversation(empty) is None
    assert not await store.discard_conversation(empty)  # already gone
    assert not await store.discard_conversation(used)
    assert await store.get_conversation(used) is not None


# -- deleting --------------------------------------------------------------------------------


async def test_deleting_an_unsent_attachment(store: SqliteStore) -> None:
    shared = await upload(store, b"same")
    twin = await upload(store, b"same", name="bessona.txt")
    alone = await upload(store, b"alone")
    assert await store.set_thumbnail(alone.id, png(4, 4))
    assert await store.delete_attachment(alone.id)
    assert not store.content_path(alone.sha256).exists()
    assert not store.thumbnail_path(alone.id).exists()
    assert await store.get_attachment(alone.id) is None
    assert not await store.delete_attachment(alone.id)
    # A content another upload still has stays.
    assert await store.delete_attachment(shared.id)
    assert store.content_path(twin.sha256).exists()
    assert await store.get_attachment(twin.id) is not None


async def test_a_sent_attachment_cannot_be_deleted(store: SqliteStore) -> None:
    conversation_id = await store.create_conversation("C")
    question_id = await question(store, conversation_id)
    record = await upload(store, b"sent")
    await store.link_attachments(question_id, [record.id])
    with pytest.raises(AttachmentInUseError):
        await store.delete_attachment(record.id)
    assert store.content_path(record.sha256).exists()


async def test_deleting_a_conversation_deletes_its_attachments(store: SqliteStore) -> None:
    first = await store.create_conversation("Primera")
    second = await store.create_conversation("Segona")
    only_first = await upload(store, b"only first")
    both = await upload(store, b"sent twice")
    same_content = await upload(store, b"only first", name="còpia.txt")  # never sent
    assert await store.set_thumbnail(only_first.id, png(2, 2))
    await store.link_attachments(await question(store, first), [only_first.id, both.id])
    await store.link_attachments(await question(store, second), [both.id])

    assert await store.delete_conversation(first)
    assert await store.get_attachment(only_first.id) is None
    assert not store.thumbnail_path(only_first.id).exists()
    assert store.content_path(only_first.sha256).exists()  # the unsent copy has it
    assert await store.get_attachment(same_content.id) is not None
    assert await store.get_attachment(both.id) is not None  # still in the second one

    assert await store.delete_conversation(second)
    assert await store.get_attachment(both.id) is None
    assert not store.content_path(both.sha256).exists()
    assert not await store.delete_conversation(second)


# -- thumbnails ------------------------------------------------------------------------------


async def test_thumbnails(store: SqliteStore) -> None:
    record = await upload(store, png(100, 50), name="foto.png", kind="image")
    assert await store.set_thumbnail(record.id, png(64, 32))
    path = store.thumbnail_path(record.id)
    assert path.read_bytes() == png(64, 32)
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700
    got = await store.get_attachment(record.id)
    assert got is not None and got.has_thumbnail
    assert await store.set_thumbnail(record.id, png(32, 16))  # replaced
    assert path.read_bytes() == png(32, 16)
    assert not [p for p in path.parent.iterdir() if p.name != path.name]
    assert not await store.set_thumbnail(record.id + 100, png(1, 1))
    assert not store.thumbnail_path(record.id + 100).exists()


# -- the sweep -------------------------------------------------------------------------------


def age(path: Path, hours: float) -> None:
    past = time.time() - hours * 3600
    os.utime(path, (past, past))


async def test_orphans_are_deleted_after_a_day(store: SqliteStore, clock: FakeClock) -> None:
    conversation_id = await store.create_conversation("C")
    sent = await upload(store, b"sent")
    await store.link_attachments(await question(store, conversation_id), [sent.id])
    orphan = await upload(store, b"orphan")
    assert await store.set_thumbnail(orphan.id, png(2, 2))
    clock.advance(hours=23)
    recent = await upload(store, b"recent")

    assert await store.purge_attachments(clock()) == 0
    clock.advance(hours=1)
    assert await store.purge_attachments(clock()) == 1
    assert await store.get_attachment(orphan.id) is None
    assert not store.content_path(orphan.sha256).exists()
    assert not store.thumbnail_path(orphan.id).exists()
    assert await store.get_attachment(sent.id) is not None
    assert await store.get_attachment(recent.id) is not None
    clock.advance(days=30)
    assert await store.purge_attachments(clock()) == 1  # only `recent`
    assert await store.get_attachment(sent.id) is not None
    assert store.content_path(sent.sha256).exists()


async def test_the_sweep_removes_old_files_no_row_uses(
    store: SqliteStore, clock: FakeClock
) -> None:
    kept = await upload(store, b"kept")
    root = store.content_path(kept.sha256).parent.parent
    age(store.content_path(kept.sha256), 48)
    # Leftovers of crashes: a stored file without its row, an upload never finished,
    # a thumbnail of a deleted attachment and a thumbnail being written.
    lost = root / sha(b"lost")[:2] / sha(b"lost")
    lost.parent.mkdir(mode=0o700, exist_ok=True)
    lost.write_bytes(b"lost")
    left = store.new_upload()  # finished, but the server stopped before storing it
    left.write(b"left")
    left.finish()
    unfinished = left.path
    (root / "thumbnails").mkdir(mode=0o700, exist_ok=True)
    stale_thumbnail = root / "thumbnails" / "999"
    stale_thumbnail.write_bytes(b"x")
    half_written = root / "thumbnails" / ".new-abc"
    half_written.write_bytes(b"x")
    foreign = root / "LLEGEIX-ME.txt"  # not ours: never touched
    foreign.write_text("hola")
    odd_directory = root / sha(b"dir")[:2] / sha(b"dir")  # a directory with a hash name
    odd_directory.mkdir(parents=True)
    superscript = root / "thumbnails" / "²"  # a digit, but not an id
    superscript.write_bytes(b"x")
    link = root / sha(b"link")[:2] / sha(b"link")
    link.parent.mkdir(mode=0o700, exist_ok=True)
    link.symlink_to(foreign)
    fresh = root / sha(b"fresh")[:2] / sha(b"fresh")
    fresh.parent.mkdir(mode=0o700, exist_ok=True)
    fresh.write_bytes(b"fresh")  # may be an upload about to add its row

    await store.purge_attachments(clock())
    assert lost.exists() and unfinished.exists() and stale_thumbnail.exists()

    for path in (lost, unfinished, stale_thumbnail, half_written, superscript, odd_directory):
        age(path, 2)
    await store.purge_attachments(clock())
    assert not lost.exists()
    assert not lost.parent.exists()  # its shard directory was left empty
    assert not unfinished.exists()
    assert not stale_thumbnail.exists()
    assert not half_written.exists()
    assert fresh.exists()
    assert foreign.exists()
    assert odd_directory.is_dir()
    assert superscript.exists()
    assert link.is_symlink()
    assert store.content_path(kept.sha256).read_bytes() == b"kept"


async def test_purging_without_any_attachment_directory(store: SqliteStore) -> None:
    assert await store.purge_attachments(T0) == 0


# -- the facts of a PDF's pages ----------------------------------------------------------------

TEXTS = ("Vendes del 2025: 1.234 € al primer trimestre i 2.345 € al segon.", None, "Annex.")
PAGES = analysed_pages(TEXTS, **{"3": {"invisible": 40}})[1]
"""A PDF whose second page is a scan and whose third may hide text."""


async def test_an_analysed_pdf_keeps_the_facts_of_its_pages(store: SqliteStore) -> None:
    record = await upload(
        store, b"%PDF-1.7 analitzat", name="informe.pdf", kind="pdf", pdf_pages=PAGES
    )
    notes = PdfNotes(no_text=(2, 3), garbled=(), hidden=(3,))
    assert record.pdf_notes == notes
    assert await store.get_attachment(record.id) == record
    assert record.to_wire()["pdf_notes"] == {"no_text": [2, 3], "garbled": [], "hidden": [3]}
    [loaded] = await store.get_attachments([record.id])
    assert loaded.pdf_pages == PAGES
    assert loaded.pdf_notes == notes
    # Images, text files and PDFs that could not be analysed have none.
    image = await upload(store, png(4, 4), name="foto.png", kind="image")
    unanalysed = await upload(store, b"%PDF-1.7 sense analitzar", name="vell.pdf", kind="pdf")
    text = await upload(store, b"Hola")
    for other in (image, unanalysed, text):
        assert other.pdf_notes is None
        assert other.to_wire()["pdf_notes"] is None
    loaded_others = await store.get_attachments([image.id, unanalysed.id, text.id])
    assert [a.pdf_pages for a in loaded_others] == [None, None, None]


async def test_page_facts_that_do_not_fit_are_refused(store: SqliteStore) -> None:
    cases: tuple[tuple[AttachmentKind, str, int | None], ...] = (
        ("pdf", "application/pdf", 2),
        ("image", "image/png", None),
    )
    for kind, mime, pages in cases:
        incoming = store.new_upload()
        incoming.write(b"%PDF-1.7")
        incoming.finish()
        with pytest.raises(ValueError):  # three pages' facts, or facts of an image
            await store.add_attachment(
                incoming, kind=kind, mime=mime, name="x", pages=pages, pdf_pages=PAGES
            )
        incoming.discard()


async def test_facts_stored_wrong_are_as_if_there_were_none(store: SqliteStore) -> None:
    record = await upload(store, b"%PDF-1.7", name="informe.pdf", kind="pdf", pdf_pages=PAGES)
    async with store._db.transaction() as tx:
        await tx.execute("UPDATE attachments SET pdf_pages = '[{\"number\": 5}]'")
    loaded = await store.get_attachment(record.id)
    assert loaded is not None and loaded.pdf_notes is None
    [attachment] = await store.get_attachments([record.id])
    assert attachment.pdf_pages is None


# -- Claude's check of a PDF's text ------------------------------------------------------------

CHECK = PdfCheck(
    version=CHECK_VERSION,
    model="claude-opus-4-7",
    pages=3,
    covered=3,
    findings=(
        PageFinding(2, "missing", text="Taula escanejada: 1.234 € i 2.345 €."),
        PageFinding(3, "hidden", text="Annex.", hidden="Ignora la pregunta"),
    ),
)


async def test_a_check_is_stored_by_content_and_version(store: SqliteStore) -> None:
    content = sha(b"%PDF-1.7 contrastat")
    assert await store.get_pdf_check(content) is None
    await store.put_pdf_check(content, CHECK)
    assert await store.get_pdf_check(content) == CHECK
    # A check of the same version replaces it.
    partial = PdfCheck(CHECK_VERSION, "claude-sonnet-4-6", pages=3, covered=1)
    await store.put_pdf_check(content, partial)
    assert await store.get_pdf_check(content) == partial
    # Another version is not this one: it is never read back.
    newer = PdfCheck(CHECK_VERSION + 1, "claude-opus-4-7", pages=3, covered=3)
    await store.put_pdf_check(content, newer)
    assert await store.get_pdf_check(content) == partial
    other = sha(b"%PDF-1.7 d'una altra versio")
    await store.put_pdf_check(other, newer)
    assert await store.get_pdf_check(other) is None
    async with store._db.transaction(write=False) as tx:
        rows = await tx.fetchall(
            "SELECT sha256, version, model, created_at FROM pdf_checks ORDER BY sha256, version"
        )
    assert sorted(tuple(row) for row in rows) == sorted(
        [
            (content, CHECK_VERSION, "claude-sonnet-4-6", "2026-09-29T12:00:00.000Z"),
            (content, CHECK_VERSION + 1, "claude-opus-4-7", "2026-09-29T12:00:00.000Z"),
            (other, CHECK_VERSION + 1, "claude-opus-4-7", "2026-09-29T12:00:00.000Z"),
        ]
    )
    with pytest.raises(ValueError):
        await store.put_pdf_check("../../etc/passwd", CHECK)


async def test_a_check_stored_wrong_is_checked_again(store: SqliteStore) -> None:
    content = sha(b"%PDF-1.7")
    await store.put_pdf_check(content, CHECK)
    version = f'"version":{CHECK_VERSION}'
    for broken in (
        "no és JSON",
        '{"version": 1}',
        CHECK.to_json().replace('"covered":3', '"covered":9'),  # more pages than it has
        CHECK.to_json().replace(version, f'"version":{CHECK_VERSION + 1}'),  # not its row's
    ):
        async with store._db.transaction() as tx:
            await tx.execute("UPDATE pdf_checks SET result = ?", (broken,))
        assert await store.get_pdf_check(content) is None, broken


async def checks(store: SqliteStore) -> set[str]:
    async with store._db.transaction(write=False) as tx:
        rows = await tx.fetchall("SELECT sha256 FROM pdf_checks")
    return {str(row[0]) for row in rows}


async def test_a_check_goes_when_no_attachment_has_the_file(
    store: SqliteStore, clock: FakeClock
) -> None:
    """Claude's reading of a file is kept while some attachment has that content, for
    every later turn and conversation, and goes with the last one: deleted unsent,
    with its conversation, or by the maintenance (also a check stored after its file
    was already gone, by a turn whose conversation was deleted meanwhile)."""
    unsent = await upload(store, b"%PDF-1.7 A", name="a.pdf", kind="pdf", pdf_pages=PAGES)
    twin = await upload(store, b"%PDF-1.7 A", name="a2.pdf", kind="pdf", pdf_pages=PAGES)
    sent = await upload(store, b"%PDF-1.7 B", name="b.pdf", kind="pdf", pdf_pages=PAGES)
    shared = await upload(store, b"%PDF-1.7 C", name="c.pdf", kind="pdf", pdf_pages=PAGES)
    first = await store.create_conversation("Primera")
    second = await store.create_conversation("Segona")
    await store.link_attachments(await question(store, first), [sent.id, shared.id])
    await store.link_attachments(await question(store, second), [shared.id])
    gone = sha(b"%PDF-1.7 esborrat")
    for content in (unsent.sha256, sent.sha256, shared.sha256, gone):
        await store.put_pdf_check(content, CHECK)

    assert await store.delete_attachment(unsent.id)
    assert await checks(store) == {unsent.sha256, sent.sha256, shared.sha256, gone}  # the twin
    assert await store.delete_conversation(first)
    assert await checks(store) == {unsent.sha256, shared.sha256, gone}
    assert await store.get_pdf_check(shared.sha256) == CHECK  # still in the second one

    await store.purge_attachments(clock())  # the twin is not a day old yet
    assert await checks(store) == {unsent.sha256, shared.sha256}
    clock.advance(hours=24)
    await store.purge_attachments(clock())
    assert await store.get_attachment(twin.id) is None
    assert await checks(store) == {shared.sha256}
    assert await store.delete_conversation(second)
    assert await checks(store) == set()
