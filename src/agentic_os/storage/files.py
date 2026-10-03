"""The attachments' files (docs/adr/0009-attachments.md), under ``<data_dir>/attachments``:

- ``<sha256[:2]>/<sha256>``: the uploaded files, content-addressed, so one file serves
  every upload of the same content and a stored file never changes;
- ``thumbnails/<id>``: the thumbnail the browser made of attachment ``id``;
- ``incoming/``: uploads being received (moved into place once complete; leftovers
  of a crash are swept).

Directories are 0700 and files 0600, like the database next to them, so the backups
of the data directory include them. Nothing here trusts a name it did not make: paths
are built only from a validated SHA-256 or an integer id. The methods are blocking
(file system calls): :class:`~agentic_os.storage.store.SqliteStore` runs the slow
ones in a thread.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import tempfile
import time
from collections.abc import Collection
from pathlib import Path
from typing import Final

from agentic_os.attachments import is_sha256

THUMBNAILS: Final = "thumbnails"
INCOMING: Final = "incoming"
STALE_SECONDS: Final = 3600.0
"""A file younger than this is never swept: it may belong to an upload in progress."""


def _private_dir(path: Path) -> Path:
    """Create ``path`` (0700) if needed and tighten it if it is looser."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        path.chmod(0o700)
    return path


def _fsync_dir(path: Path) -> None:
    """Make a rename in ``path`` durable (best effort)."""
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class IncomingFile:
    """An upload being received: a private temporary file (0600) in ``directory`` (which
    must exist), on the same file system as the stored files so it is moved into place
    atomically, with the size and SHA-256 of what was written so far."""

    def __init__(self, directory: Path) -> None:
        fd, name = tempfile.mkstemp(dir=directory, prefix="upload-")
        self.path = Path(name)
        self._file = os.fdopen(fd, "wb")
        self._hash = hashlib.sha256()
        self.size = 0
        self.sha256 = ""
        """Set by :meth:`finish`."""

    def write(self, chunk: bytes) -> None:
        self._file.write(chunk)
        self._hash.update(chunk)
        self.size += len(chunk)

    def finish(self) -> str:
        """Flush it to disk (blocking) and close it; returns its SHA-256."""
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self.sha256 = self._hash.hexdigest()
        return self.sha256

    def read(self) -> bytes:
        """The whole content (only for the small files: images and text)."""
        return self.path.read_bytes()

    def discard(self) -> None:
        """Close and delete it, unless it was moved into place. Idempotent."""
        with contextlib.suppress(OSError):
            self._file.close()
        self.path.unlink(missing_ok=True)


class AttachmentFiles:
    """The files of the attachments under ``root`` (see the module docstring)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _directory(self, name: str) -> Path:
        """A directory under the root, both created (0700) if needed."""
        _private_dir(self.root)
        return _private_dir(self.root / name)

    def content_path(self, sha256: str) -> Path:
        if not is_sha256(sha256):
            raise ValueError("not a SHA-256")
        return self.root / sha256[:2] / sha256

    def thumbnail_path(self, attachment_id: int) -> Path:
        if isinstance(attachment_id, bool) or not isinstance(attachment_id, int):
            raise TypeError("an attachment id is an integer")
        return self.root / THUMBNAILS / str(attachment_id)

    def incoming(self) -> IncomingFile:
        return IncomingFile(self._directory(INCOMING))

    def place(self, upload: IncomingFile) -> Path:
        """Move a finished upload to its content-addressed path; if that content is
        already stored, the upload is deleted instead. Returns the stored path."""
        target = self.content_path(upload.sha256)
        directory = self._directory(target.parent.name)
        if target.exists():
            upload.discard()
        else:
            os.replace(upload.path, target)
            _fsync_dir(directory)
        return target

    def remove_content(self, sha256: str) -> None:
        path = self.content_path(sha256)
        path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):  # only when empty
            path.parent.rmdir()

    def write_thumbnail(self, attachment_id: int, data: bytes) -> None:
        """Store (or replace) the thumbnail of an attachment, atomically."""
        target = self.thumbnail_path(attachment_id)
        directory = self._directory(THUMBNAILS)
        fd, name = tempfile.mkstemp(dir=directory, prefix=".new-")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, target)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        _fsync_dir(directory)

    def remove_thumbnail(self, attachment_id: int) -> None:
        self.thumbnail_path(attachment_id).unlink(missing_ok=True)

    def sweep(
        self,
        *,
        stored: Collection[str],
        attachment_ids: Collection[int],
        now: float | None = None,
        stale_seconds: float = STALE_SECONDS,
    ) -> int:
        """Delete what no row uses: content files whose SHA-256 is not in ``stored``,
        thumbnails whose id is not in ``attachment_ids`` and uploads left behind, but
        only when older than ``stale_seconds`` (an upload may be about to add its row).
        Anything else in the directory is left alone. Returns how many files went."""
        if not self.root.is_dir():
            return 0
        now = time.time() if now is None else now
        removed = 0

        def stale(path: Path) -> bool:
            """A file (never a directory) older than ``stale_seconds``."""
            try:
                status = path.lstat()
            except OSError:
                return False
            return stat.S_ISREG(status.st_mode) and now - status.st_mtime > stale_seconds

        for shard in self.root.iterdir():
            name = shard.name
            if len(name) != 2 or not is_sha256(name + "0" * 62) or not shard.is_dir():
                continue
            for path in shard.iterdir():
                if (
                    is_sha256(path.name)
                    and path.name[:2] == name
                    and path.name not in stored
                    and stale(path)
                ):
                    path.unlink(missing_ok=True)
                    removed += 1
            with contextlib.suppress(OSError):
                shard.rmdir()
        thumbnails = self.root / THUMBNAILS
        if thumbnails.is_dir():
            for path in thumbnails.iterdir():
                number = path.name.isascii() and path.name.isdigit()
                orphan = number and int(path.name) not in attachment_ids
                leftover = path.name.startswith(".new-")
                if (orphan or leftover) and stale(path):
                    path.unlink(missing_ok=True)
                    removed += 1
        incoming = self.root / INCOMING
        if incoming.is_dir():
            for path in incoming.iterdir():
                if path.name.startswith("upload-") and stale(path):
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed
