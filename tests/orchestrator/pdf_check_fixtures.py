"""Claude's check of a PDF in the tests (orchestrator/pdf_check.py): the JSON lines Claude
writes, analysed PDFs as the server stores them, and a Claude whose check calls follow a
script (a reply, an error, or a call that waits until the test releases it)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Collection, Mapping
from dataclasses import replace

from agentic_os.domain import Usage
from agentic_os.providers.base import (
    Attachment,
    DeclinedAttempt,
    GenerationRequest,
    GenerationResult,
    ProviderEvent,
    TextDelta,
)
from agentic_os.providers.fake import FakeProvider
from orchestrator.attachment_fixtures import AttachmentFiles, analysed_pages

END = '{"end": true}'
SALES = "Les vendes del 2025 van créixer un 12 % respecte de l'any anterior."
COSTS = "Els costos de personal es van mantenir estables durant tot l'exercici."
TABLE = "Taula de vendes: gener 1.234 €, febrer 1.310 €, març 1.402 €."

CHECK_USAGE = Usage(input_tokens=9000, output_tokens=120, cache_write_tokens=500)
"""What a scripted check reply bills (plus its call index in input tokens)."""


def finding(page: int, status: str, **fields: str) -> str:
    """One line of Claude's reply: a page's finding as JSON."""
    return json.dumps({"page": page, "status": status, **fields}, ensure_ascii=False)


def reply(*lines: str) -> str:
    return "\n".join(lines)


def analysed(
    files: AttachmentFiles,
    *texts: str | None,
    name: str = "informe.pdf",
    facts: Mapping[str, Mapping[str, int | bool]] | None = None,
) -> Attachment:
    """A PDF whose pages have these texts (None: a page without text), analysed as the
    server does (``analysed_pages``; ``facts`` override a page's counts by its number,
    ``{"3": {"invisible": 40}}``)."""
    text, pages = analysed_pages(texts, **(facts or {}))
    return replace(files.pdf(name, pages=len(texts), text=text), pdf_pages=pages)


class ScriptedClaude(FakeProvider):
    """Claude whose "check" calls follow ``script``, one step per call (the last one
    repeats): a reply, or an exception to raise. The calls in ``gated`` (by their index,
    from 0) first wait for :attr:`gate`: :attr:`waiting` is set when one starts waiting,
    and :attr:`cancelled` counts the ones cancelled meanwhile. A reply bills
    :data:`CHECK_USAGE` plus its call index in input tokens, after the ``declined``
    attempts (a server-side fallback). Every other call is the fake's."""

    def __init__(
        self,
        *script: str | Exception,
        gated: Collection[int] = (),
        declined: tuple[DeclinedAttempt, ...] = (),
    ) -> None:
        super().__init__("claude", chunk_delay=0)
        self.script: tuple[str | Exception, ...] = script or (END,)
        self.gated = frozenset(gated)
        self.declined = declined
        self.gate = asyncio.Event()
        self.waiting = asyncio.Event()
        self.cancelled = 0
        self.check_calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        if request.purpose != "check":
            async for event in super().stream(request):
                yield event
            return
        index = self.check_calls
        self.check_calls += 1
        self.requests.append(request)
        self.attachments.append(tuple(request.attachments))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if index in self.gated:
                self.waiting.set()
                try:
                    await self.gate.wait()
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
            step = self.script[min(index, len(self.script) - 1)]
            if isinstance(step, Exception):
                raise step
            yield TextDelta(step)
            yield GenerationResult(
                text=step,
                usage=replace(CHECK_USAGE, input_tokens=CHECK_USAGE.input_tokens + index),
                model=request.model or "fake-claude",
                latency_ms=7,
                ttft_ms=3,
                declined=self.declined,
            )
        finally:
            self.in_flight -= 1
