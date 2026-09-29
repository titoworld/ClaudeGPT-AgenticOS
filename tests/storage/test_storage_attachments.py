"""Attachments in the SQLite store and their files (docs/adr/0009-adjunts.md): the
migration, content-addressed private files, links to questions, deletion, the sweep of
orphans and leftovers, and the engine's contract (get_attachments, link_attachments)."""

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

from agentic_os.orchestrator.store import AttachmentNotFoundError, NewMessage, Store
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
            incoming, kind="pdf", mime="application/pdf", name=name, pages=2, text=None
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
        assert await db.schema_version() == SCHEMA_VERSION == 4
        async with db.transaction(write=False) as tx:
            tables = {r[0] for r in await tx.fetchall("SELECT name FROM sqlite_master")}
            kept = await tx.fetchone("SELECT value FROM settings WHERE key = 'a'")
        assert {"attachments", "message_attachments"} <= tables
        assert kept is not None and kept[0] == "1"
    finally:
        await db.close()


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
