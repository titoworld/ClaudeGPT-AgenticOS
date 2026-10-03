"""REST routes of docs/PROTOCOL.md (except ``/api/auth``, see ``routes_auth``)."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from agentic_os.i18n import t
from agentic_os.server.catalog import pricing_to_wire
from agentic_os.server.deps import MAX_SQLITE_ID, StateDep, read_json, require_session
from agentic_os.server.status import status_to_wire
from agentic_os.storage import MAX_LIST_LIMIT, RuntimeSettings, SettingsConflictError

ConversationId = Annotated[int, Path(ge=1, le=MAX_SQLITE_ID)]
"""A conversation id in a path: 422 outside SQLite's positive INTEGER range."""

public_router = APIRouter(prefix="/api")
"""Routes that never require a session."""

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
"""Routes that require a session (401 otherwise)."""


@public_router.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.get("/providers")
async def providers(state: StateDep) -> JSONResponse:
    return JSONResponse([status_to_wire(s) for s in await state.monitor.statuses()])


@router.get("/models")
async def models(state: StateDep, refresh: bool = False) -> JSONResponse:
    runtime, statuses, listings = await asyncio.gather(
        state.store.get_runtime_settings(),
        state.monitor.statuses(),
        state.catalog.listings(refresh=refresh),
    )
    return JSONResponse(state.catalog.to_wire(runtime, statuses, listings))


@router.get("/pricing")
async def pricing(state: StateDep) -> JSONResponse:
    runtime = await state.store.get_runtime_settings()
    fx = await state.store.current_fx(state.clock())
    return JSONResponse(pricing_to_wire(fx, runtime.prices))


@router.get("/spend")
async def spend(state: StateDep) -> JSONResponse:
    return JSONResponse(await state.store.month_spend(state.clock()))


@router.get("/settings")
async def get_settings(state: StateDep) -> JSONResponse:
    settings = await state.store.get_runtime_settings()
    return JSONResponse(settings.to_wire())


@router.put("/settings")
async def put_settings(request: Request, state: StateDep) -> JSONResponse:
    """Save the settings if they are based on the stored revision (ADR 0006): 422 if
    the body, or its ``revision``, is not valid; 409 with the stored settings if
    another tab or device saved since (the comparison and the write are atomic)."""
    body = await read_json(request)
    if isinstance(body, dict) and "revision" not in body:
        raise HTTPException(status_code=422, detail=t("server.settings.revision_required"))
    try:
        settings = RuntimeSettings.from_wire(body)
    except (ValueError, OverflowError) as exc:  # OverflowError: a backstop for huge ints
        detail = str(exc) if isinstance(exc, ValueError) else t("server.number_too_large")
        raise HTTPException(status_code=422, detail=detail) from None
    try:
        stored = await state.store.put_runtime_settings(settings, base_revision=settings.revision)
    except SettingsConflictError as exc:
        return JSONResponse(
            {"detail": t("server.settings.conflict"), "settings": exc.current.to_wire()},
            status_code=409,
        )
    if stored.fx.mode == "auto":
        state.fx.poke()  # fetch the ECB rate now if there is no recent one
    return JSONResponse(stored.to_wire())


@router.get("/conversations")
async def list_conversations(
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = 50,
    before: Annotated[int | None, Query(ge=1, le=MAX_SQLITE_ID)] = None,
    q: str | None = None,
) -> JSONResponse:
    """A page of conversations, newest first; ``q`` searches the titles (docs/PROTOCOL.md,
    «Llista i cerca de converses»): 422 for a text longer than MAX_SEARCH_LENGTH."""
    try:
        conversations = await state.store.list_conversations(limit=limit, before=before, query=q)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return JSONResponse([c.to_wire() for c in conversations])


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: ConversationId, state: StateDep) -> JSONResponse:
    detail = await state.store.get_conversation(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=t("server.conversation_not_found"))
    return JSONResponse(detail.to_wire())


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: ConversationId, request: Request, state: StateDep
) -> JSONResponse:
    body = await read_json(request)
    title = body.get("title") if isinstance(body, dict) else None
    if not isinstance(title, str):
        raise HTTPException(status_code=422, detail=t("server.title_required"))
    try:
        summary = await state.store.rename_conversation(conversation_id, title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if summary is None:
        raise HTTPException(status_code=404, detail=t("server.conversation_not_found"))
    return JSONResponse(summary.to_wire())


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: ConversationId, state: StateDep) -> Response:
    # A running turn would fail on the deleted conversation: stop it first.
    state.turns.cancel_conversation(conversation_id)
    if not await state.store.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail=t("server.conversation_not_found"))
    # Its turns cannot be replayed any more (turn.subscribe answers turn.unknown).
    state.turns.forget_conversation(conversation_id)
    return Response(status_code=204)


@router.get("/stats")
async def stats(state: StateDep, days: Annotated[int, Query(ge=1, le=365)] = 30) -> JSONResponse:
    return JSONResponse(await state.store.stats(days, state.clock()))
