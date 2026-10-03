"""``/api/attachments`` (docs/PROTOCOL.md "Attachments", docs/adr/0009-attachments.md): upload,
metadata, content, thumbnail and deletion of the files attached to questions.

- Every route needs the session (401), and the ones that change something an allowed
  ``Origin`` (403, :class:`~agentic_os.server.middleware.OriginCheckMiddleware`).
- An upload is the raw file as the body. Its type comes from its content, never from
  its name or ``Content-Type``; the body is written to a private temporary file as it
  arrives and cut at the limit of its type (413), which its first bytes decide.
- A PDF's text and the facts of its pages come from the PDF reader's own process
  (:class:`~agentic_os.attachments.PdfReader`); its ``Attachment`` carries the warnings
  of its pages (``pdf_notes``: without text, unreadable, possibly hidden text).
- Nothing uploaded is ever served as a document of the app's origin except images:
  PDFs and text files are downloads (``Content-Disposition: attachment``), and every
  file carries ``X-Content-Type-Options: nosniff`` and a policy that forbids it any
  script or resource (:data:`FILE_CSP`).
"""

import asyncio
import errno
import unicodedata
from typing import Annotated, Final
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import ClientDisconnect

from agentic_os.attachments import (
    EMPTY_DETAIL,
    MAX_THUMBNAIL_BYTES,
    SNIFF_BYTES,
    AttachmentError,
    FileType,
    image_dimensions,
    sniff,
    text_content,
    thumbnail_too_large_detail,
    thumbnail_type,
    too_large_detail,
    upload_type,
)
from agentic_os.attachments import display_name as sanitized_name
from agentic_os.i18n import t
from agentic_os.server.deps import (
    MAX_SQLITE_ID,
    AppState,
    StateDep,
    require_session,
)
from agentic_os.storage import AttachmentInUseError, AttachmentRecord, IncomingFile

FILE_CSP: Final = "default-src 'none'; sandbox"
"""Policy of every served file: even opened on its own, it can run nothing and load
nothing (on top of ``nosniff`` and the downloads' ``Content-Disposition``)."""
_DISK_FULL: Final = frozenset({errno.ENOSPC, errno.EDQUOT})

AttachmentId = Annotated[int, Path(ge=1, le=MAX_SQLITE_ID)]
"""An attachment id in a path: 422 outside SQLite's positive INTEGER range."""

router = APIRouter(prefix="/api/attachments", dependencies=[Depends(require_session)])


def _declared_length(request: Request) -> int | None:
    value = request.headers.get("content-length", "").strip()
    return int(value) if value.isascii() and value.isdigit() else None


async def _receive(request: Request, upload: IncomingFile, name: str) -> FileType:
    """Write the body into ``upload`` as it arrives; its type (from its first bytes)
    sets its limit: 413 as soon as it would pass it (at once when ``Content-Length``
    already says so), 415 for a type that is not accepted, 422 for an empty file."""
    declared = _declared_length(request)
    head = b""
    found: FileType | None = None
    limit = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        if found is None:
            head += chunk[: SNIFF_BYTES - len(head)]
            if len(head) < SNIFF_BYTES:
                upload.write(chunk)
                continue
            found, limit = upload_type(head, name)
            if declared is not None and declared > limit:
                raise AttachmentError(413, too_large_detail(found.kind))
        if upload.size + len(chunk) > limit:
            raise AttachmentError(413, too_large_detail(found.kind))
        upload.write(chunk)
    if upload.size == 0:
        raise AttachmentError(422, EMPTY_DETAIL)
    if found is None:  # shorter than the sniffed bytes
        found, _ = upload_type(head, name)
    return found


async def _store(
    state: AppState, upload: IncomingFile, found: FileType, name: str
) -> AttachmentRecord:
    """Check the whole file for its type and store it with what describes it."""
    await asyncio.to_thread(upload.finish)
    store = state.store
    if found.kind == "image":
        data = await asyncio.to_thread(upload.read)
        # A thread: the headers of a hostile JPEG can take a while to walk.
        width, height = await asyncio.to_thread(image_dimensions, data, found.mime)
        return await store.add_attachment(
            upload, kind="image", mime=found.mime, name=name, width=width, height=height
        )
    if found.kind == "text":
        text = text_content(await asyncio.to_thread(upload.read), name)
        return await store.add_attachment(
            upload, kind="text", mime=found.mime, name=name, text=text
        )
    info = await state.pdf.read(upload.path)
    return await store.add_attachment(
        upload,
        kind="pdf",
        mime=found.mime,
        name=name,
        pages=info.pages,
        text=info.text,
        pdf_pages=info.pdf_pages,
    )


@router.put("", status_code=201)
async def upload_attachment(
    request: Request,
    state: StateDep,
    name: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    """Upload a file (the raw body); ``name`` is the file's name, only shown and used
    for text files' extension. 201 with the ``Attachment``; 413, 415 or 422 (a ``detail``
    in the client's language) when it is refused. It stays unsent (an orphan) until a turn names
    it, and an orphan is deleted after a day."""
    try:
        clean_name = sanitized_name(name)
        upload = await asyncio.to_thread(state.store.new_upload)
        try:
            found = await _receive(request, upload, clean_name)
            record = await _store(state, upload, found, clean_name)
        finally:
            upload.discard()  # nothing once stored
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail=t("server.client_disconnected")) from None
    except OSError as exc:
        if exc.errno in _DISK_FULL:
            raise HTTPException(status_code=507, detail=t("server.attachment.disk_full")) from None
        raise
    return JSONResponse(record.to_wire(), status_code=201)


async def _record(state: AppState, attachment_id: int) -> AttachmentRecord:
    record = await state.store.get_attachment(attachment_id)
    if record is None:
        raise HTTPException(status_code=404, detail=t("server.attachment.not_found"))
    return record


@router.get("/{attachment_id}")
async def get_attachment(attachment_id: AttachmentId, state: StateDep) -> JSONResponse:
    return JSONResponse((await _record(state, attachment_id)).to_wire())


def _ascii_name(name: str) -> str:
    """A plain ASCII version of a display name, for the ``filename`` parameter."""
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    safe = "".join(c if c.isprintable() and c not in '"\\' else "_" for c in ascii_only)
    return safe.strip() or "fitxer"


def file_headers(name: str, *, inline: bool) -> dict[str, str]:
    """Headers of a served file: shown in place (images only) or downloaded, with its
    name (RFC 6266: an ASCII ``filename`` and the exact ``filename*``)."""
    disposition = "inline" if inline else "attachment"
    return {
        "Content-Disposition": (
            f'{disposition}; filename="{_ascii_name(name)}"; '
            f"filename*=UTF-8''{quote(name, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": FILE_CSP,
    }


@router.get("/{attachment_id}/content")
async def get_content(attachment_id: AttachmentId, state: StateDep) -> Response:
    """The file itself, with the type sniffed at the upload: images in place, PDFs and
    text files as downloads."""
    record = await _record(state, attachment_id)
    path = state.store.content_path(record.sha256)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=t("server.attachment.not_found"))
    media_type = "text/plain; charset=utf-8" if record.kind == "text" else record.mime
    return FileResponse(
        path,
        media_type=media_type,
        headers=file_headers(record.name, inline=record.kind == "image"),
    )


async def _small_body(request: Request, limit: int) -> bytes:
    """The whole body, refused with 413 as soon as it passes ``limit`` bytes."""
    declared = _declared_length(request)
    if declared is not None and declared > limit:
        raise AttachmentError(413, thumbnail_too_large_detail())
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise AttachmentError(413, thumbnail_too_large_detail())
    return bytes(body)


@router.put("/{attachment_id}/thumbnail", status_code=204)
async def put_thumbnail(attachment_id: AttachmentId, request: Request, state: StateDep) -> Response:
    """Store the thumbnail the browser made of an attachment (a PNG or WebP of at most
    100 kB and 512 px per side, sniffed): 204, or 404, 413, 415, 422."""
    await _record(state, attachment_id)  # 404 before reading any body
    try:
        data = await _small_body(request, MAX_THUMBNAIL_BYTES)
        thumbnail_type(data)
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail=t("server.client_disconnected")) from None
    if not await state.store.set_thumbnail(attachment_id, data):
        raise HTTPException(status_code=404, detail=t("server.attachment.not_found"))
    return Response(status_code=204)


@router.get("/{attachment_id}/thumbnail")
async def get_thumbnail(attachment_id: AttachmentId, state: StateDep) -> Response:
    record = await _record(state, attachment_id)
    path = state.store.thumbnail_path(attachment_id)
    try:
        data = await asyncio.to_thread(path.read_bytes) if record.has_thumbnail else b""
    except FileNotFoundError:
        data = b""
    found = sniff(data[:SNIFF_BYTES])
    if found is None or found.mime not in ("image/png", "image/webp"):
        raise HTTPException(status_code=404, detail=t("server.attachment.no_thumbnail"))
    extension = found.mime.removeprefix("image/")
    return Response(
        data, media_type=found.mime, headers=file_headers(f"miniatura.{extension}", inline=True)
    )


@router.delete("/{attachment_id}", status_code=204)
async def delete_attachment(attachment_id: AttachmentId, state: StateDep) -> Response:
    """Delete an attachment never sent in a turn (the owner removed it from the
    composer): 204, 404, or 409 once a question has it."""
    try:
        deleted = await state.store.delete_attachment(attachment_id)
    except AttachmentInUseError:
        raise HTTPException(status_code=409, detail=t("server.attachment.in_use")) from None
    if not deleted:
        raise HTTPException(status_code=404, detail=t("server.attachment.not_found"))
    return Response(status_code=204)
