"""InMemoryStore contract behaviour and the FakeProvider's output."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from agentic_os.domain import Usage
from agentic_os.orchestrator.events import ErrorInfo, Savings, TurnFailure, TurnOutcome
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.prompts import revision_prompt, system_prompt
from agentic_os.orchestrator.sections import RevisionStreamParser
from agentic_os.orchestrator.store import CachedTurn, JsonValue, NewMessage, Store
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
    RefusalError,
    TextDelta,
)
from agentic_os.providers.fake import FakeProvider

NOW = datetime(2026, 9, 27, tzinfo=UTC)


# -- InMemoryStore ----------------------------------------------------------------------


async def test_store_history_and_summary() -> None:
    store: Store = InMemoryStore(clock=lambda: NOW)
    conversation_id = await store.create_conversation("Títol")
    assert await store.conversation_exists(conversation_id)
    assert not await store.conversation_exists(conversation_id + 1)

    question = await store.add_message(
        NewMessage(conversation_id=conversation_id, kind="question", content="Q", final=True)
    )
    draft = await store.add_message(
        NewMessage(conversation_id, "answer", "esborrany", turn_id=question, agent="claude")
    )
    final = await store.add_message(
        NewMessage(conversation_id, "synthesis", "S", turn_id=question, agent="claude", final=True)
    )
    history = await store.get_history(conversation_id)
    assert [m.id for m in history.messages] == [question, final]
    assert history.messages[0].turn_id == question  # the question is its own turn
    assert history.messages[1].turn_id == question
    assert history.messages[0].created_at == NOW
    assert draft not in [m.id for m in history.messages]

    await store.set_summary(conversation_id, "Resum", question)
    history = await store.get_history(conversation_id)
    assert (history.summary, history.summary_upto_id) == ("Resum", question)
    assert [m.id for m in history.messages] == [final]


async def test_store_rejects_unknown_conversations_and_bad_turns() -> None:
    store = InMemoryStore()
    with pytest.raises(KeyError):
        await store.add_message(NewMessage(conversation_id=9, kind="question", content="Q"))
    conversation_id = await store.create_conversation("T")
    with pytest.raises(ValueError):
        await store.add_message(NewMessage(conversation_id, "question", "Q", turn_id=1))
    with pytest.raises(ValueError):
        await store.add_message(NewMessage(conversation_id, "answer", "A", agent="claude"))
    history = await store.get_history(9)
    assert history.messages == () and history.summary is None


async def test_store_writes_the_turn_outcome_on_its_question() -> None:
    store: Store = InMemoryStore(clock=lambda: NOW)
    conversation_id = await store.create_conversation("T")
    meta: dict[str, JsonValue] = {"mode": "solo", "outcome": None}
    question = await store.add_message(NewMessage(conversation_id, "question", "Q", meta=meta))
    answer = await store.add_message(
        NewMessage(conversation_id, "answer", "A", turn_id=question, agent="claude", final=True)
    )
    outcome = TurnOutcome(
        status="failed",
        usage=Usage(input_tokens=10, cost_usd=0.5),
        savings=Savings(),
        error=ErrorInfo("invalid", "Claude no ha pogut respondre."),
        failures=(TurnFailure("claude", "invalid", "Claude ha declinat.", 0),),
        final_message_ids=(answer,),
    )
    await store.set_turn_outcome(question, outcome)
    await store.set_turn_outcome(answer, outcome)  # not a question: nothing happens
    await store.set_turn_outcome(999, outcome)  # unknown (a deleted conversation): the same
    assert isinstance(store, InMemoryStore)
    stored = {m.id: m for m in store.messages}
    assert stored[question].meta == {"mode": "solo", "outcome": outcome.to_wire()}
    assert "outcome" not in stored[answer].meta
    assert stored[question].created_at == NOW
    assert meta["outcome"] is None  # the caller's meta is not touched


async def test_store_cache_expiry() -> None:
    store = InMemoryStore()
    value = CachedTurn(mode="solo", messages=(), tokens=10)
    await store.cache_put("k", value, NOW + timedelta(seconds=10))
    assert await store.cache_get("k", NOW) == value
    assert await store.cache_get("k", NOW + timedelta(seconds=10)) is None
    assert await store.cache_get("k", NOW) is None  # expired entries are dropped


# -- FakeProvider -------------------------------------------------------------------------


async def run_fake(
    provider: FakeProvider, request: GenerationRequest
) -> tuple[str, GenerationResult]:
    text = ""
    result: GenerationResult | None = None
    async for event in provider.stream(request):
        if isinstance(event, TextDelta):
            assert result is None, "no text after the result"
            text += event.text
        else:
            result = event
    assert result is not None
    return text, result


async def test_fake_answer_streams_markdown_in_small_chunks() -> None:
    provider = FakeProvider("claude", chunk_delay=0)
    assert isinstance(provider, Provider)
    request = GenerationRequest(system=system_prompt("claude"), prompt="Com aprenc Python?")
    chunks = [e async for e in provider.stream(request) if isinstance(e, TextDelta)]
    assert len(chunks) > 10 and max(len(c.text) for c in chunks) < 60
    text, result = await run_fake(provider, request)
    assert text == result.text and "Com aprenc Python?" in text
    assert result.model == "fake-claude"
    assert result.usage.output_tokens > 0 and result.usage.input_tokens > 0
    assert result.ttft_ms is not None
    # Deterministic.
    assert (await run_fake(FakeProvider("claude", chunk_delay=0), request))[0] == text


async def test_fake_revisions_follow_the_format_and_agreement_sequence() -> None:
    provider = FakeProvider("chatgpt", chunk_delay=0, agreements=[40, 95])
    request = GenerationRequest(
        system=system_prompt("chatgpt"),
        prompt=revision_prompt("chatgpt", "Q?", "La meva resposta", "La de Claude"),
        purpose="revision",
    )
    parsed = []
    for _ in range(3):
        text, _ = await run_fake(provider, request)
        parser = RevisionStreamParser()
        parser.feed(text)
        parsed.append(parser.final())
    assert [p.agreement for p in parsed] == [40, 95, 95]
    assert [p.unchanged for p in parsed] == [False, True, True]
    assert parsed[0].answer.startswith("La meva resposta")
    assert "Claude" in parsed[0].critique


async def test_fake_failures_status_and_prewarm() -> None:
    provider = FakeProvider("claude", chunk_delay=0, fail={"synthesis"})
    with pytest.raises(ProviderError) as error:
        await run_fake(provider, GenerationRequest(system="s", prompt="p", purpose="synthesis"))
    assert error.value.kind == "unavailable" and not error.value.retryable
    status = await provider.status()
    assert (status.mode, status.available, status.detail) == ("fake", True, "Mode demostració")
    request = GenerationRequest(system="s", prompt="")
    await provider.prewarm(request)
    assert provider.prewarmed == [request]
    await provider.aclose()
    assert provider.closed


async def test_fake_summary_and_chunk_delay() -> None:
    provider = FakeProvider("chatgpt", chunk_delay=0.001)
    started = time.monotonic()
    text, result = await run_fake(
        provider, GenerationRequest(system="s", prompt="Resumeix", purpose="summary", fast=True)
    )
    assert time.monotonic() - started >= 0.001
    assert text.startswith("Resum") and result.usage != Usage()
    assert result.model == "fake-chatgpt-mini"


async def test_fake_echoes_the_requested_model_and_lists_two() -> None:
    provider = FakeProvider("claude", chunk_delay=0)
    _, result = await run_fake(provider, GenerationRequest(system="s", prompt="p", model="x-1"))
    assert result.model == "x-1" and result.usage.cost_usd is None
    models = await provider.list_models()
    assert [(m.id, m.is_default) for m in models] == [
        ("fake-claude", True),
        ("fake-claude-mini", False),
    ]
    assert all(m.label and m.description for m in models)
    assert provider.models_live


async def test_fake_agreement_sequence_restarts_for_a_new_question() -> None:
    provider = FakeProvider("claude", chunk_delay=0, agreements=[30, 90])

    async def agreement(question: str) -> int | None:
        request = GenerationRequest(
            system="s", prompt=revision_prompt("claude", question, "a", "b"), purpose="revision"
        )
        parser = RevisionStreamParser()
        parser.feed((await run_fake(provider, request))[0])
        return parser.final().agreement

    assert [await agreement("Q1"), await agreement("Q1"), await agreement("Q2")] == [30, 90, 30]


async def test_fake_honours_the_output_budget() -> None:
    provider = FakeProvider("claude", chunk_delay=0)
    request = GenerationRequest(system="s", prompt="Com aprenc Python?", max_output_tokens=10)
    text, result = await run_fake(provider, request)
    assert result.truncated is True and result.finish_reason == "max_tokens"
    assert text == result.text and 0 < len(text) <= 40
    assert result.usage.output_tokens <= 10
    whole, complete = await run_fake(provider, GenerationRequest(system="s", prompt="Com?"))
    assert not complete.truncated and complete.finish_reason is None and len(whole) > 40


async def test_fake_truncates_and_refuses_on_demand() -> None:
    provider = FakeProvider("chatgpt", chunk_delay=0, truncate={"answer"}, refuse={"synthesis"})
    full, _ = await run_fake(FakeProvider("chatgpt", chunk_delay=0), GenerationRequest("s", "Q"))
    text, result = await run_fake(provider, GenerationRequest("s", "Q"))
    assert result.truncated and result.finish_reason == "max_tokens"
    assert full.startswith(text) and len(text) < len(full)

    with pytest.raises(RefusalError) as refused:
        await run_fake(provider, GenerationRequest("s", "Q", purpose="synthesis"))
    error = refused.value
    assert error.kind == "invalid" and not error.retryable
    assert error.model == "fake-chatgpt" and error.usage.input_tokens > 0
    assert "declinat" in error.message and error.refusal


async def test_fake_can_refuse_after_streaming_part_of_the_answer() -> None:
    provider = FakeProvider("claude", chunk_delay=0, refuse={"answer"}, refuse_after=2)
    deltas: list[str] = []
    with pytest.raises(RefusalError) as refused:
        async for event in provider.stream(GenerationRequest("s", "Q")):
            assert isinstance(event, TextDelta)
            deltas.append(event.text)
    assert len(deltas) == 2
    assert refused.value.usage.output_tokens > 0
