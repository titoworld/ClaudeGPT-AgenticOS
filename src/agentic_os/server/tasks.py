"""Small asyncio helpers for the server's background tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any


async def cancel_and_wait(tasks: Iterable[asyncio.Task[Any]]) -> None:
    """Cancel ``tasks`` and wait until they have all finished.

    ``asyncio.wait`` is used instead of ``gather`` so that, if the caller itself is
    cancelled meanwhile, the ``CancelledError`` it gets is its own (with its own
    cancel message, which anyio cancel scopes rely on) and not a child's. Errors of
    the tasks are marked as retrieved: whoever cares has inspected them already."""
    tasks = list(tasks)
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)
    for task in tasks:
        if task.done() and not task.cancelled():
            task.exception()
