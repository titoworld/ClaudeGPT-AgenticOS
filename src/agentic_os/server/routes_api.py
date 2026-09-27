"""REST routes of docs/PROTOCOL.md (except ``/api/auth``, see ``routes_auth``)."""

from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from agentic_os.server.deps import StateDep, read_json, require_session
from agentic_os.server.status import status_to_wire
from agentic_os.storage import MAX_LIST_LIMIT, RuntimeSettings

NOT_FOUND_DETAIL: Final = "La conversa no existeix."

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


@router.get("/settings")
async def get_settings(state: StateDep) -> JSONResponse:
    settings = await state.store.get_runtime_settings()
    return JSONResponse(settings.to_wire())


@router.put("/settings")
async def put_settings(request: Request, state: StateDep) -> JSONResponse:
    try:
        settings = RuntimeSettings.from_wire(await read_json(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await state.store.put_runtime_settings(settings)
    return JSONResponse(settings.to_wire())


@router.get("/conversations")
async def list_conversations(
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> JSONResponse:
    conversations = await state.store.list_conversations(limit=limit, before=before)
    return JSONResponse([c.to_wire() for c in conversations])


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int, state: StateDep) -> JSONResponse:
    detail = await state.store.get_conversation(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)
    return JSONResponse(detail.to_wire())


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: int, request: Request, state: StateDep
) -> JSONResponse:
    body = await read_json(request)
    title = body.get("title") if isinstance(body, dict) else None
    if not isinstance(title, str):
        raise HTTPException(status_code=422, detail="Cal indicar el títol (text).")
    try:
        summary = await state.store.rename_conversation(conversation_id, title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if summary is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)
    return JSONResponse(summary.to_wire())


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, state: StateDep) -> Response:
    # A running turn would fail on the deleted conversation: stop it first.
    state.turns.cancel_conversation(conversation_id)
    if not await state.store.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)
    return Response(status_code=204)


@router.get("/stats")
async def stats(state: StateDep, days: Annotated[int, Query(ge=1, le=365)] = 30) -> JSONResponse:
    return JSONResponse(await state.store.stats(days, state.clock()))
