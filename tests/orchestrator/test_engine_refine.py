"""Refine turns («Perfecciona», docs/adr/0010-mode-perfecciona.md) end to end, with fake
providers and the in-memory store: both agents answer, the editor merges the answers into
version 1, and round after round both review the current version and the editor writes the
next one, until the owner stops the turn, nobody finds anything left to change, both score
it above the threshold, or a limit (rounds, budget) is reached. The last version is the
turn's final answer, also when the owner cancels the turn."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime
from typing import Any

import pytest

from agentic_os.domain import AGENTS, AgentName, Purpose, RefineOptions, TurnOptions, Usage, words
from agentic_os.orchestrator import engine as engine_module
from agentic_os.orchestrator import prompts
from agentic_os.orchestrator.engine import (
    REFINE_FAILED_ROUND,
    REFINE_IDENTICAL,
    REFINE_INCOMPLETE,
    REFINE_NO_CHANGES,
    REFINE_NOTHING_TO_CHANGE,
    REFINE_OVER_BUDGET,
    Engine,
)
from agentic_os.orchestrator.events import (
    PhaseChanged,
    RefineChange,
    RefineRound,
    ServerEvent,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    TurnCompleted,
    TurnFailed,
    TurnOutcome,
    TurnStopping,
)
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.store import CachedTurn, JsonValue, NewMessage, StoredMessage
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import GenerationRequest, ProviderError, ProviderEvent, TextDelta
from agentic_os.providers.fake import REFINE_PROPOSALS, FakeProvider
from agentic_os.storage import models
from orchestrator.attachment_fixtures import AttachmentFiles
from orchestrator.pdf_check_fixtures import COSTS, END, SALES, TABLE, analysed, finding, reply

BRIEF = "Pla de llançament de la botiga en línia"
PRICE = ModelPrice(input=4.0, output=20.0, cache_read=0.2, cache_write=5.0)
PRICES = {
    model: PRICE
    for model in ("fake-claude", "fake-chatgpt", "fake-claude-mini", "fake-chatgpt-mini")
}
V1_WORDS = 90
"""Words of the fake's merge: the budget is then REFINE_MIN_BUDGET_WORDS (300)."""
ALWAYS_A_DEFECT = (
    "<changes>\n- [defect] Pas 2: el total no quadra — 3 + 4 no fa 8.\n</changes>\n"
    "<score>50</score>"
)
UNCHANGED = "<changes>\nUNCHANGED\n</changes>\n<score>95</score>"


def refine(
    *,
    request_id: str = "r",
    text: str = BRIEF,
    budget_usd: float | None = None,
    conversation_id: int | None = None,
    use_cache: bool = True,
    attachments: tuple[int, ...] = (),
    **options: Any,
) -> TurnRequest:
    return TurnRequest(
        request_id,
        text,
        "refine",
        conversation_id=conversation_id,
        options=TurnOptions(refine=RefineOptions(**options), use_cache=use_cache),
        attachments=attachments,
        refine_budget_usd=budget_usd,
    )


async def collect(events: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    return [event async for event in events]


def of_type[E](events: Sequence[ServerEvent], kind: type[E]) -> list[E]:
    return [event for event in events if isinstance(event, kind)]


def phases(events: Sequence[ServerEvent]) -> list[tuple[str, int]]:
    return [(event.phase, event.round) for event in of_type(events, PhaseChanged)]


def rounds(events: Sequence[ServerEvent]) -> list[RefineRound]:
    return of_type(events, RefineRound)


def completed(events: Sequence[ServerEvent]) -> TurnCompleted:
    done = events[-1]
    assert isinstance(done, TurnCompleted), done
    return done


def messages(store: InMemoryStore, kind: str | None = None) -> list[StoredMessage]:
    return [m for m in store.messages if kind is None or m.kind == kind]


def refine_meta(message: StoredMessage) -> dict[str, JsonValue]:
    meta = message.meta.get("refine")
    assert isinstance(meta, dict), message
    return meta


def role(message: StoredMessage) -> JsonValue:
    return refine_meta(message).get("role")


def versions(store: InMemoryStore) -> list[StoredMessage]:
    return [m for m in messages(store, "revision") if role(m) == "version"]


def reviews(store: InMemoryStore) -> list[StoredMessage]:
    return [m for m in messages(store, "revision") if role(m) == "review"]


def final_of(store: InMemoryStore, done: TurnCompleted | TurnOutcome) -> StoredMessage:
    ids = done.final_message_ids
    (final,) = [m for m in store.messages if m.id in ids]
    return final


def outcome_of(store: InMemoryStore, turn_id: int) -> dict[str, JsonValue]:
    (question,) = [m for m in store.messages if m.id == turn_id]
    outcome = question.meta["outcome"]
    assert isinstance(outcome, dict)
    return outcome


def records_usage(store: InMemoryStore, turn_id: int) -> Usage:
    return sum((u.usage for u in store.usage if u.turn_id == turn_id), Usage())


def streams_in(events: Sequence[ServerEvent], phase: str) -> list[tuple[AgentName, int]]:
    """The agent and round of every review or version that started streaming during a
    phase of this name (the final message is no call of any phase)."""
    current: str | None = None
    found: list[tuple[AgentName, int]] = []
    for event in events:
        if isinstance(event, PhaseChanged):
            current = event.phase
        elif isinstance(event, StreamStarted) and current == phase and event.kind == "revision":
            found.append((event.agent, event.round))
    return found


def failures_of(outcome: dict[str, JsonValue]) -> list[tuple[JsonValue, JsonValue]]:
    failures = outcome["failures"]
    assert isinstance(failures, list)
    return [(f["agent"], f["round"]) for f in failures if isinstance(f, dict)]


def streamed(events: Sequence[ServerEvent], stream_id: str, section: str) -> str:
    return "".join(
        e.text
        for e in of_type(events, StreamDelta)
        if e.stream_id == stream_id and e.section == section
    )


def other_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


def current_version(prompt: str) -> str:
    return prompt.split("<current_version>\n", 1)[1].split("\n</current_version>", 1)[0]


def version_reply(text: str, *changes: str) -> str:
    changelog = "\n".join(changes) or "- [clarity] Un canvi."
    return f"<version>\n{text}\n</version>\n<changelog>\n{changelog}\n</changelog>"


def is_edit(request: GenerationRequest) -> bool:
    """An edit or a shortening of a refine turn (not the merge)."""
    return request.purpose == "synthesis" and (
        "<current_version>" in request.prompt or "<draft>" in request.prompt
    )


Edit = str | Callable[[str], str]


class Editor(FakeProvider):
    """A fake whose edits and shortenings are the test's, in turn: a reply, or a function
    of the current version (the draft's, for a shortening); past them, the fake's own."""

    def __init__(self, agent: AgentName, edits: Sequence[Edit] = (), **kwargs: Any) -> None:
        super().__init__(agent, chunk_delay=0, **kwargs)
        self.edits = list(edits)

    def _compose(self, request: GenerationRequest) -> str:
        if is_edit(request) and self.edits:
            edit = self.edits.pop(0)
            if isinstance(edit, str):
                return edit
            if "<draft>" in request.prompt:
                return edit(request.prompt.split("<draft>\n", 1)[1].split("\n</draft>", 1)[0])
            return edit(current_version(request.prompt))
        return super()._compose(request)


class Hooked(FakeProvider):
    """A fake that calls ``hook`` with each request, before it answers."""

    def __init__(
        self, agent: AgentName, hook: Callable[[GenerationRequest], None], **kwargs: Any
    ) -> None:
        super().__init__(agent, chunk_delay=0, **kwargs)
        self.hook = hook

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.hook(request)
        async for event in super().stream(request):
            yield event


class Stalls(FakeProvider):
    """Answers like the fake until the call after the first ``skip`` calls that ``when``
    picks: that one streams a few words and then thinks until it is cancelled."""

    def __init__(
        self, agent: AgentName, when: Callable[[GenerationRequest], bool], skip: int = 0
    ) -> None:
        super().__init__(agent, chunk_delay=0)
        self.when = when
        self.skip = skip
        self.stalled = asyncio.Event()
        self.cancelled = 0

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        if not self.when(request) or self.skip > 0:
            if self.when(request):
                self.skip -= 1
            async for event in super().stream(request):
                yield event
            return
        self.requests.append(request)
        yield TextDelta("<version>\nUna versió que no ")
        self.stalled.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


def fakes(**kwargs: Any) -> dict[AgentName, FakeProvider]:
    return {agent: FakeProvider(agent, chunk_delay=0, **kwargs) for agent in AGENTS}


async def run(
    request: TurnRequest,
    providers: dict[AgentName, FakeProvider] | None = None,
    store: InMemoryStore | None = None,
    **kwargs: Any,
) -> list[ServerEvent]:
    engine = Engine(providers or fakes(), store or InMemoryStore(), retry_delay=0)
    return await collect(engine.run(request, **kwargs))


# -- the whole loop --------------------------------------------------------------------------


async def test_a_refine_turn_end_to_end() -> None:
    store = InMemoryStore()
    providers = fakes()
    outcomes: list[TurnOutcome] = []
    events = await run(refine(), providers, store, on_outcome=outcomes.append)
    done = completed(events)

    # Round 0 answers, round 1 merges, then review and edit until both reviews find
    # nothing to change (round 4), after two rounds scored above 90 without a defect.
    assert phases(events) == [
        ("answer", 0),
        ("edit", 1),
        ("review", 2),
        ("edit", 2),
        ("review", 3),
        ("edit", 3),
        ("review", 4),
    ]
    assert done.stop_reason == "converged" and done.consensus is None and not done.cached
    assert done.to_wire()["stop_reason"] == "converged"
    assert not of_type(events, TurnStopping) and not of_type(events, StreamFailed)

    streams = of_type(events, StreamStarted)
    assert [(s.agent, s.kind, s.round) for s in streams] == [
        ("claude", "answer", 0),
        ("chatgpt", "answer", 0),
        ("claude", "revision", 1),
        ("claude", "revision", 2),
        ("chatgpt", "revision", 2),
        ("claude", "revision", 2),
        ("claude", "revision", 3),
        ("chatgpt", "revision", 3),
        ("claude", "revision", 3),
        ("claude", "revision", 4),
        ("chatgpt", "revision", 4),
        ("claude", "synthesis", 4),
    ]

    v1, v2, v3 = versions(store)
    assert [refine_meta(v)["version"] for v in (v1, v2, v3)] == [1, 2, 3]
    assert all(refine_meta(v)["accepted"] is True for v in (v1, v2, v3))
    assert v2.content.startswith(v1.content) and v3.content.startswith(v2.content)
    assert refine_meta(v1) == {
        "role": "version",
        "version": 1,
        "words": V1_WORDS,
        "budget_words": 300,
        "accepted": True,
        "reason": None,
        "changelog": [
            {"kind": "merge", "text": "Els passos numerats vénen de la resposta de Claude."},
            {"kind": "merge", "text": "La taula de comprovació ve de la resposta de ChatGPT."},
        ],
    }
    claude_defect, chatgpt_defect = (REFINE_PROPOSALS[a][0][len("- [defect] ") :] for a in AGENTS)
    assert refine_meta(v2)["changelog"] == [
        {"kind": "defect", "text": claude_defect},
        {"kind": "defect", "text": chatgpt_defect},
    ]

    first_review = reviews(store)[0]
    assert first_review.round == 2 and first_review.agent == "claude"
    assert first_review.content == REFINE_PROPOSALS["claude"][0]
    assert refine_meta(first_review) == {
        "role": "review",
        "score": 78,
        "unchanged": False,
        "changes": [{"kind": "defect", "text": claude_defect}],
    }
    last_review = reviews(store)[-1]
    assert last_review.content == "UNCHANGED"
    assert refine_meta(last_review) == {
        "role": "review",
        "score": 95,
        "unchanged": True,
        "changes": [],
    }

    final = final_of(store, done)
    assert (final.kind, final.agent, final.round, final.final) == ("synthesis", "claude", 4, True)
    assert final.content == v3.content
    assert final.meta["copied_from"] == v3.id
    assert final.meta["usage"] == Usage().to_dict()
    assert refine_meta(final) == {
        "role": "final",
        "version": 3,
        "words": words(v3.content),
        "budget_words": 300,
        "stop_reason": "converged",
    }
    # Only the question and the final version are final messages (the history).
    assert [m.kind for m in store.messages if m.final] == ["question", "synthesis"]
    assert len(store.messages) == 13

    (reported,) = outcomes
    assert reported.status == "completed" and reported.stop_reason == "converged"
    assert reported.final_message_ids == (final.id,)
    assert outcome_of(store, done.turn_id) == reported.to_wire()
    assert outcome_of(store, done.turn_id)["stop_reason"] == "converged"

    assert [r.purpose for r in providers["claude"].requests] == [
        "answer",
        "synthesis",
        "revision",
        "synthesis",
        "revision",
        "synthesis",
        "revision",
    ]
    assert [r.purpose for r in providers["chatgpt"].requests] == ["answer", *["revision"] * 3]
    assert [(u.agent, u.ok) for u in store.usage].count(("claude", True)) == 7
    assert all(u.ok for u in store.usage) and len(store.usage) == 11


async def test_the_rounds_on_the_wire() -> None:
    store = InMemoryStore()
    events = await run(refine(), fakes(), store, price_overrides=PRICES)
    first, second, third, fourth = rounds(events)
    assert first.to_wire() == {
        "type": "refine.round",
        "request_id": "r",
        "round": 1,
        "version": 1,
        "accepted": True,
        "reason": None,
        "words": V1_WORDS,
        "budget_words": 300,
        "changes": refine_meta(versions(store)[0])["changelog"],
        "proposals": {"claude": None, "chatgpt": None},
        "scores": {"claude": None, "chatgpt": None},
        "converged": False,
        "usage": first.usage.to_dict(),
        "total": first.total.to_dict(),
    }
    assert (second.version, second.accepted, second.reason) == (2, True, None)
    assert second.proposals == {"claude": 1, "chatgpt": 1}
    assert second.scores == {"claude": 78, "chatgpt": 78} and not second.converged
    assert [c.kind for c in second.changes] == ["defect", "defect"]
    assert second.words == words(versions(store)[1].content)
    assert (third.version, third.scores, third.converged) == (
        3,
        {"claude": 92, "chatgpt": 92},
        False,
    )
    assert fourth.to_wire()["changes"] == [] and fourth.version == 3
    assert (fourth.accepted, fourth.reason) == (False, REFINE_NOTHING_TO_CHANGE)
    assert fourth.proposals == {"claude": 0, "chatgpt": 0} and fourth.converged

    # Each round's usage is what its calls billed; the total, the turn's so far.
    done = completed(events)
    answers = [e.usage for e in of_type(events, StreamCompleted)][:2]
    spent = sum(answers, Usage())
    for event in rounds(events):
        spent += event.usage
        assert event.total.processed_tokens == spent.processed_tokens
        assert event.total.cost_usd == pytest.approx(spent.cost_usd)
    assert fourth.total.processed_tokens == done.usage.processed_tokens
    assert done.usage == records_usage(store, done.turn_id)
    # No savings of a debate: nothing skipped, no answer kept.
    assert (done.savings.early_stop, done.savings.unchanged, done.savings.total) == (0, 0, 0)


async def test_the_events_in_order() -> None:
    events = await run(refine(max_rounds=2))
    names = [
        f"{event.phase}:{event.round}" if isinstance(event, PhaseChanged) else type(event).__name__
        for event in events
        if not isinstance(event, StreamDelta)
    ]
    assert names == [
        "TurnStarted",
        "answer:0",
        "StreamStarted",
        "StreamStarted",
        "StreamCompleted",
        "StreamCompleted",
        "edit:1",
        "StreamStarted",
        "StreamCompleted",
        "RefineRound",
        "review:2",
        "StreamStarted",
        "StreamStarted",
        "StreamCompleted",
        "StreamCompleted",
        "edit:2",
        "StreamStarted",
        "StreamCompleted",
        "RefineRound",
        "StreamStarted",
        "StreamCompleted",
        "TurnCompleted",
    ]
    done = completed(events)
    assert done.stop_reason == "max_rounds"
    assert [r.round for r in rounds(events)] == [1, 2]


async def test_every_message_carries_its_refine_meta_live_and_stored() -> None:
    store = InMemoryStore()
    events = await run(refine(), fakes(), store)
    assert completed(events).stop_reason == "converged" and len(store.messages) == 13
    started = {s.stream_id: s for s in of_type(events, StreamStarted)}
    by_message = {e.message_id: e for e in of_type(events, StreamCompleted)}
    for message in messages(store):
        if message.kind == "question":
            assert message.meta["refine"] == {
                "max_rounds": 12,
                "budget_eur": 3.0,
                "max_words": None,
                "stop_on_convergence": True,
                "convergence_threshold": 90,
                "editor": "claude",
            }
            assert message.meta["mode"] == "refine"
            continue
        live = by_message[message.id]
        stream = started[live.stream_id]
        if message.kind == "answer":
            assert "refine" not in message.meta and live.refine is None
            assert streamed(events, live.stream_id, "text") == message.content
            continue
        assert live.refine == message.meta["refine"]
        assert live.to_wire()["refine"] == message.meta["refine"]
        kind = role(message)
        if kind == "review":
            assert streamed(events, live.stream_id, "critique") == message.content
            assert streamed(events, live.stream_id, "answer") == ""
        elif kind == "version":
            # The version streams as the answer, its changelog as the critique.
            assert streamed(events, live.stream_id, "answer") == message.content
            changelog = streamed(events, live.stream_id, "critique")
            assert changelog.startswith("- [") and "<" not in changelog
        else:
            assert kind == "final" and stream.kind == "synthesis"
            assert streamed(events, live.stream_id, "text") == message.content
        for delta in of_type(events, StreamDelta):
            assert "<version>" not in delta.text and "<changes>" not in delta.text


# -- stopping ---------------------------------------------------------------------------------


async def test_turn_stop_ends_the_turn_after_the_round_in_course() -> None:
    stop = asyncio.Event()

    def during_review(request: GenerationRequest) -> None:
        if request.purpose == "revision" and "**Canvi " not in request.prompt:
            stop.set()  # the owner presses «Atura» while round 2 reviews version 1

    providers: dict[AgentName, FakeProvider] = {
        "claude": Hooked("claude", during_review),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(), providers, store, stop=stop)
    done = completed(events)
    assert done.stop_reason == "owner"
    (stopping,) = of_type(events, TurnStopping)
    assert stopping.to_wire() == {"type": "turn.stopping", "request_id": "r", "round": 2}
    # The round in course ends: its edit still runs, then the last version is final.
    position = events.index(stopping)
    assert phases(events[:position]) == [("answer", 0), ("edit", 1), ("review", 2)]
    assert phases(events[position:]) == [("edit", 2)]
    assert [r.round for r in rounds(events)] == [1, 2]
    final = final_of(store, done)
    assert refine_meta(final)["version"] == 2 and refine_meta(final)["stop_reason"] == "owner"
    assert final.round == 2 and final.content == versions(store)[-1].content
    assert outcome_of(store, done.turn_id)["stop_reason"] == "owner"


async def test_a_stop_during_the_answers_ends_after_the_first_version() -> None:
    stop = asyncio.Event()
    stop.set()  # even before the turn starts
    store = InMemoryStore()
    events = await run(refine(), fakes(), store, stop=stop)
    done = completed(events)
    assert done.stop_reason == "owner"
    assert phases(events) == [("answer", 0), ("edit", 1)]
    (stopping,) = of_type(events, TurnStopping)
    assert stopping.round == 1
    assert events.index(stopping) < events.index(rounds(events)[0])
    assert refine_meta(final_of(store, done))["version"] == 1


async def test_the_other_modes_ignore_the_stop_signal() -> None:
    stop = asyncio.Event()
    stop.set()
    events = await run(TurnRequest("r", BRIEF, "debate"), stop=stop)
    done = completed(events)
    assert done.stop_reason is None and "stop_reason" not in done.to_wire()
    assert not of_type(events, TurnStopping)


@pytest.mark.parametrize("convergence", [False, True])
async def test_two_rounds_without_changes_stop_the_turn_whatever_the_options(
    convergence: bool,
) -> None:
    providers = {
        agent: FakeProvider(agent, chunk_delay=0, refine_reviews=(UNCHANGED,)) for agent in AGENTS
    }
    store = InMemoryStore()
    # Scored 95 without a defect: with convergence on, both rules end round 3, and
    # nothing left to change is what the turn says.
    events = await run(refine(stop_on_convergence=convergence), providers, store)
    done = completed(events)
    assert done.stop_reason == "unchanged"
    assert phases(events) == [("answer", 0), ("edit", 1), ("review", 2), ("review", 3)]
    second, third = rounds(events)[1:]
    for event in (second, third):
        assert (event.version, event.accepted, event.reason) == (1, False, REFINE_NOTHING_TO_CHANGE)
        assert not event.converged
    assert len(versions(store)) == 1  # no edit at all
    assert refine_meta(final_of(store, done))["version"] == 1


async def test_without_convergence_the_turn_goes_on_until_nothing_is_left() -> None:
    store = InMemoryStore()
    events = await run(refine(stop_on_convergence=False), fakes(), store)
    done = completed(events)
    # Rounds 3 and 4 scored 92 and 95 without a defect: it would have converged at 4.
    assert done.stop_reason == "unchanged"
    assert [r.round for r in rounds(events)] == [1, 2, 3, 4, 5]
    assert not any(r.converged for r in rounds(events))
    assert refine_meta(final_of(store, done))["version"] == 3


async def test_a_round_with_a_defect_or_below_the_threshold_does_not_converge() -> None:
    providers = {
        agent: FakeProvider(
            agent,
            chunk_delay=0,
            refine_reviews=(
                "<changes>\n- [clarity] A: b — c.\n</changes>\n<score>96</score>",
                "<changes>\n- [defect] A: b — c.\n</changes>\n<score>96</score>",
                "<changes>\n- [clarity] A: b — c.\n</changes>\n<score>96</score>",
                "<changes>\n- [clarity] A: b — c.\n</changes>\n<score>70</score>",
                "<changes>\n- [clarity] A: b — c.\n</changes>\n<score>96</score>",
            ),
        )
        for agent in AGENTS
    }
    events = await run(refine(max_rounds=12), providers)
    done = completed(events)
    # 96 (2), defect (3), 96 (4), 70 (5), then 96 in rounds 6 and 7: converged at 7.
    assert done.stop_reason == "converged"
    assert [r.round for r in rounds(events) if r.converged] == [7]
    assert [r.round for r in rounds(events)][-1] == 7


async def test_the_rounds_run_out() -> None:
    providers = {
        agent: FakeProvider(agent, chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,))
        for agent in AGENTS
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=3), providers, store)
    done = completed(events)
    assert done.stop_reason == "max_rounds"
    assert [r.round for r in rounds(events)] == [1, 2, 3]
    final = final_of(store, done)
    assert final.round == 3 and refine_meta(final)["version"] == 3


async def test_the_budget_runs_out() -> None:
    providers = {
        agent: FakeProvider(agent, chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,))
        for agent in AGENTS
    }
    reference = await run(refine(max_rounds=6), providers, price_overrides=PRICES)
    after_round_3 = rounds(reference)[2].total.cost_usd
    assert after_round_3 is not None and after_round_3 > 0

    store = InMemoryStore()
    providers = {
        agent: FakeProvider(agent, chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,))
        for agent in AGENTS
    }
    events = await run(
        refine(max_rounds=6, budget_usd=after_round_3 - 1e-12),
        providers,
        store,
        price_overrides=PRICES,
    )
    done = completed(events)
    # The total reaches the budget with round 3: round 4 never starts.
    assert done.stop_reason == "budget"
    assert [r.round for r in rounds(events)] == [1, 2, 3]
    assert refine_meta(final_of(store, done))["stop_reason"] == "budget"


async def test_without_a_known_cost_or_budget_only_the_rounds_limit_the_turn() -> None:
    def defects() -> dict[AgentName, FakeProvider]:
        return {
            agent: FakeProvider(agent, chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,))
            for agent in AGENTS
        }

    unpriced = await run(refine(max_rounds=3, budget_usd=0.000001), defects())
    assert completed(unpriced).stop_reason == "max_rounds"  # the fake models have no price
    unlimited = await run(refine(max_rounds=3), defects(), price_overrides=PRICES)
    assert completed(unlimited).stop_reason == "max_rounds"


# -- the editor's guards ------------------------------------------------------------------------


def longer(text: str) -> str:
    """``text`` past a budget of 100 words."""
    return text + "\n\n" + " ".join(["paraula"] * 120)


async def test_a_version_over_the_budget_is_shortened_once() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Editor(
            "claude",
            [
                lambda v: version_reply(longer(v), "- [defect] Afegeix la data."),
                lambda draft: version_reply(
                    draft[: draft.index("paraula")] + "Data: 3 de novembre.",
                    "- [defect] Afegeix la data.",
                ),
            ],
            refine_reviews=(ALWAYS_A_DEFECT,),
        ),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,)),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=2, max_words=100), providers, store)
    done = completed(events)
    v1, too_long, shortened = versions(store)
    assert refine_meta(v1)["budget_words"] == 100
    assert (too_long.round, shortened.round) == (2, 2)
    assert refine_meta(too_long) == {
        "role": "version",
        "version": 2,
        "words": words(too_long.content),
        "budget_words": 100,
        "accepted": False,
        "reason": REFINE_OVER_BUDGET,
        "changelog": [{"kind": "defect", "text": "Afegeix la data."}],
    }
    assert refine_meta(shortened)["accepted"] is True and refine_meta(shortened)["version"] == 2
    assert words(shortened.content) <= 100
    second = rounds(events)[1]
    assert (second.version, second.accepted, second.reason) == (2, True, None)
    assert second.changes == (RefineChange("defect", "Afegeix la data."),)
    assert final_of(store, done).content == shortened.content

    request = next(r for r in providers["claude"].requests if "<draft>" in r.prompt)
    assert request.purpose == "synthesis" and request.history == ()
    assert "- [defect] Afegeix la data." in request.prompt
    assert request.prompt.endswith(
        f"The new version has {words(too_long.content)} words; the shortened one must have "
        "at most 100 words."
    )


class Merges(Editor):
    """An :class:`Editor` whose merges are the test's, in turn (past them, the fake's)."""

    def __init__(
        self, agent: AgentName, merges: Sequence[str], edits: Sequence[Edit] = (), **kwargs: Any
    ) -> None:
        super().__init__(agent, edits, **kwargs)
        self.merges = list(merges)

    def _compose(self, request: GenerationRequest) -> str:
        merge = request.purpose == "synthesis" and not is_edit(request)
        if merge and self.merges and "<brief>" in request.prompt:
            return self.merges.pop(0)
        return super()._compose(request)


def merged(count: int) -> str:
    """A merge that writes a version of ``count`` words."""
    text = " ".join(f"paraula{n}" for n in range(count))
    return f"<version>\n{text}\n</version>\n<changelog>\n- [merge] Tot ve de Claude.\n</changelog>"


async def test_a_first_version_over_the_owners_limit_is_shortened_once() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Merges("claude", [merged(900)], refine_reviews=(UNCHANGED,)),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, refine_reviews=(UNCHANGED,)),
    }
    store = InMemoryStore()
    events = await run(refine(max_words=200), providers, store)
    done = completed(events)
    too_long, v1 = versions(store)
    assert refine_meta(too_long) == {
        "role": "version",
        "version": 1,
        "words": 900,
        "budget_words": 200,
        "accepted": False,
        "reason": REFINE_OVER_BUDGET,
        "changelog": [{"kind": "merge", "text": "Tot ve de Claude."}],
    }
    assert (v1.round, v1.agent) == (1, "claude") and words(v1.content) <= 200
    assert refine_meta(v1)["accepted"] is True and refine_meta(v1)["version"] == 1
    assert refine_meta(v1)["changelog"] == [{"kind": "merge", "text": "Tot ve de Claude."}]
    first = rounds(events)[0]
    assert (first.round, first.version, first.accepted, first.reason) == (1, 1, True, None)
    assert (first.words, first.budget_words) == (words(v1.content), 200)
    assert first.changes == (RefineChange("merge", "Tot ve de Claude."),)
    # One retry by the same agent, in round 1, self-contained like a round's shortening.
    assert streams_in(events, "edit") == [("claude", 1), ("claude", 1)]
    (shorten,) = [r for r in providers["claude"].requests if "<draft>" in r.prompt]
    assert shorten.history == () and "- [merge] Tot ve de Claude." in shorten.prompt
    assert shorten.prompt.endswith(
        "The new version has 900 words; the shortened one must have at most 200 words."
    )
    final = final_of(store, done)
    assert final.content == v1.content and refine_meta(final)["words"] == words(v1.content)


async def test_a_first_version_still_over_the_limit_is_kept() -> None:
    """There is no earlier version to keep: the shortening is version 1 all the same, and
    the later rounds' edits must bring it within the limit."""
    providers: dict[AgentName, FakeProvider] = {
        "claude": Merges(
            "claude",
            [merged(900)],
            [lambda draft: version_reply(draft, "- [simplification] Treu l'annex.")],
            refine_reviews=(UNCHANGED,),
        ),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, refine_reviews=(UNCHANGED,)),
    }
    store = InMemoryStore()
    events = await run(refine(max_words=200), providers, store)
    done = completed(events)
    too_long, v1 = versions(store)
    assert refine_meta(too_long)["accepted"] is False
    meta = refine_meta(v1)
    assert (meta["version"], meta["words"], meta["budget_words"]) == (1, 900, 200)
    assert (meta["accepted"], meta["reason"]) == (True, None)
    assert meta["changelog"] == [{"kind": "simplification", "text": "Treu l'annex."}]
    assert final_of(store, done).content == v1.content and done.stop_reason == "unchanged"


class ShorteningFails(Merges):
    """Its shortenings fail, as a provider that is down."""

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        if "<draft>" in request.prompt:
            raise ProviderError("Ha fallat a propòsit.", kind="unavailable", retryable=False)
        async for event in super().stream(request):
            yield event


async def test_a_failed_shortening_of_the_first_version_hands_the_merge_to_the_other() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": ShorteningFails("claude", [merged(900)]),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=2, max_words=200), providers, store)
    done = completed(events)
    too_long, v1 = versions(store)[:2]
    assert (too_long.agent, refine_meta(too_long)["accepted"]) == ("claude", False)
    assert (v1.agent, v1.round, refine_meta(v1)["accepted"]) == ("chatgpt", 1, True)
    assert words(v1.content) <= 200
    assert streams_in(events, "edit")[:3] == [("claude", 1), ("claude", 1), ("chatgpt", 1)]
    assert failures_of(outcome_of(store, done.turn_id)) == [("claude", 1)]


class StallsShortening(Merges):
    """Its shortenings stream a few words and then think until they are cancelled."""

    def __init__(self, agent: AgentName, merges: Sequence[str]) -> None:
        super().__init__(agent, merges)
        self.stalled = asyncio.Event()

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        if "<draft>" in request.prompt:
            self.requests.append(request)
            yield TextDelta("<version>\nUna versió que no ")
            self.stalled.set()
            await asyncio.sleep(3600)
        async for event in super().stream(request):
            yield event


async def test_cancelling_while_the_first_version_is_shortened_keeps_the_merge() -> None:
    """The merge over the limit is the last complete version while it is shortened: a
    turn cancelled meanwhile keeps it as its final answer, as it keeps any version."""
    store = InMemoryStore()
    claude = StallsShortening("claude", [merged(900)])
    providers: dict[AgentName, FakeProvider] = {
        "claude": claude,
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    outcomes: list[TurnOutcome] = []

    async def consume() -> None:
        engine = Engine(providers, store, retry_delay=0)
        async for _event in engine.run(refine(max_words=200), on_outcome=outcomes.append):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(claude.stalled.wait(), 5)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    (too_long,) = versions(store)
    (reported,) = outcomes
    assert reported.status == "cancelled" and reported.stop_reason == "owner"
    final = final_of(store, reported)
    assert final.content == too_long.content and final.meta["copied_from"] == too_long.id
    assert refine_meta(final) == {
        "role": "final",
        "version": 1,
        "words": 900,
        "budget_words": 200,
        "stop_reason": "owner",
    }
    assert other_tasks() == set()


class LongAnswers(FakeProvider):
    """Its answers have 300 words."""

    def _compose(self, request: GenerationRequest) -> str:
        if request.purpose == "answer":
            return " ".join(f"paraula{n}" for n in range(300))
        return super()._compose(request)


async def test_a_copied_first_version_over_the_limit_is_kept() -> None:
    """Version 1 copies an answer when nobody could merge them: it has no earlier version
    either, and nobody to shorten it."""
    store = InMemoryStore()
    providers: dict[AgentName, FakeProvider] = {
        agent: LongAnswers(agent, chunk_delay=0, fail={"synthesis"}) for agent in AGENTS
    }
    events = await run(refine(max_rounds=2, max_words=100), providers, store)
    v1 = versions(store)[0]
    meta = refine_meta(v1)
    count = words(v1.content)
    assert "copied_from" in meta and meta["accepted"] is True and count > 100
    assert (meta["words"], meta["budget_words"]) == (count, 100)
    assert not any("<draft>" in r.prompt for p in providers.values() for r in p.requests)
    assert completed(events).stop_reason == "failed"


async def test_a_version_still_over_the_budget_is_rejected() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Editor(
            "claude",
            [lambda v: version_reply(longer(v)), lambda draft: version_reply(draft)],
            refine_reviews=(ALWAYS_A_DEFECT,),
        ),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,)),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=2, max_words=100), providers, store)
    done = completed(events)
    v1, too_long, still = versions(store)
    for rejected in (too_long, still):
        meta = refine_meta(rejected)
        assert (meta["accepted"], meta["reason"], meta["version"]) == (False, REFINE_OVER_BUDGET, 2)
    second = rounds(events)[1]
    assert (second.version, second.accepted, second.reason) == (1, False, REFINE_OVER_BUDGET)
    assert second.changes == () and second.words == words(v1.content)
    final = final_of(store, done)
    assert final.content == v1.content and refine_meta(final)["version"] == 1


async def test_a_version_identical_to_the_current_one_is_not_accepted() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Editor("claude", [lambda v: version_reply(f"\n  {v}  \n")]),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=2), providers, store)
    second = rounds(events)[1]
    assert (second.version, second.accepted, second.reason) == (1, False, REFINE_IDENTICAL)
    same = versions(store)[1]
    assert (
        refine_meta(same)["accepted"] is False and refine_meta(same)["reason"] == REFINE_IDENTICAL
    )
    # No retry: one edit call in round 2.
    assert sum(is_edit(r) for r in providers["claude"].requests) == 1


async def test_a_reply_without_a_complete_version_rejects_the_round() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Editor("claude", ["Aquí tens la versió nova, però sense etiquetes."]),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=2), providers, store)
    second = rounds(events)[1]
    assert (second.version, second.accepted, second.reason) == (1, False, REFINE_INCOMPLETE)
    incomplete = versions(store)[1]
    assert incomplete.content == "Aquí tens la versió nova, però sense etiquetes."
    assert refine_meta(incomplete)["reason"] == REFINE_INCOMPLETE
    assert refine_meta(incomplete)["accepted"] is False
    # A rejection is no failure: nobody else edits.
    assert not of_type(events, StreamFailed)
    assert not any(is_edit(r) for r in providers["chatgpt"].requests)


async def test_a_document_that_quotes_the_tags_is_accepted_whole() -> None:
    quoting = (
        "# Format\n\n```xml\n<version>\nText.\n</version>\n<changelog>\n- [defect] x\n"
        "</changelog>\n```\n\nFi del document."
    )
    providers: dict[AgentName, FakeProvider] = {
        "claude": Editor("claude", [version_reply(quoting, "- [clarity] Explica el format.")]),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=3), providers, store)
    v2 = versions(store)[1]
    assert v2.content == quoting and refine_meta(v2)["accepted"] is True
    assert completed(events).stop_reason == "max_rounds"
    # Round 3 reviews it with the quoted tags escaped: they cannot close a section.
    review = [r for r in providers["chatgpt"].requests if r.purpose == "revision"][-1]
    assert "```xml\n&lt;version>\nText.\n&lt;/version>\n&lt;changelog>" in review.prompt


CODE = (
    "# Fitxers\n\n```xml\n<project>\n  <version>1.2.0</version>\n</project>\n```\n\n"
    '```jsx\nreturn (<Review score={5}><Message text="hi" /></Review>);\n```'
)
"""A document with code that writes tags of the refine prompts: a pom.xml and JSX."""


async def test_code_that_writes_the_prompts_tags_is_reviewed_with_the_escapes_explained() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Editor("claude", [version_reply(CODE, "- [requirement] Afegeix el codi.")]),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=3), providers, store)
    done = completed(events)
    _v1, v2, v3 = versions(store)
    assert v2.content == CODE and refine_meta(v2)["accepted"] is True
    # Round 3 reviews it with the tags escaped, so that they cannot close a section, and
    # every reviewer is told that «&lt;» is how the prompt quotes them, not the document.
    for agent in AGENTS:
        review = [r for r in providers[agent].requests if r.purpose == "revision"][-1]
        assert "&lt;version>1.2.0&lt;/version>" in review.prompt
        assert '(&lt;Review score={5}>&lt;Message text="hi" />&lt;/Review>)' in review.prompt
        assert prompts.REFINE_REVIEW_ESCAPES_NOTE in review.prompt
    # The editor is told to write «<» back, and does: the next version and the final
    # answer keep the code as it was written.
    edit = [r for r in providers["claude"].requests if is_edit(r)][-1]
    assert prompts.REFINE_ESCAPES_NOTE in edit.prompt and "&lt;version>" in edit.prompt
    assert refine_meta(v3)["accepted"] is True and v3.content.startswith(CODE)
    final = final_of(store, done)
    assert final.content == v3.content and "&lt;" not in final.content
    answers = [r for r in providers["chatgpt"].requests if r.purpose == "answer"]
    assert prompts.REFINE_ANSWER_ESCAPES_NOTE in answers[0].prompt


# -- failures -------------------------------------------------------------------------------------


async def test_one_agent_failing_its_answer_leaves_the_other_alone() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"answer"}),
    }
    store = InMemoryStore()
    events = await run(refine(), providers, store)
    done = completed(events)
    assert done.stop_reason == "converged"
    assert [r.purpose for r in providers["chatgpt"].requests] == ["answer"]
    merge = providers["claude"].requests[1]
    assert '<answer from="Claude">' in merge.prompt
    assert '<answer from="ChatGPT">' not in merge.prompt
    for event in rounds(events)[1:]:
        # No review of ChatGPT: missing from the round, null on the wire.
        assert "chatgpt" not in event.proposals and "chatgpt" not in event.scores
        wire = event.to_wire()
        assert wire["proposals"] == {"claude": event.proposals["claude"], "chatgpt": None}
        assert wire["scores"] == {"claude": event.scores["claude"], "chatgpt": None}
    assert {m.agent for m in reviews(store)} == {"claude"}
    failures = outcome_of(store, done.turn_id)["failures"]
    assert isinstance(failures, list)
    (failure,) = failures
    assert failure == {
        "agent": "chatgpt",
        "kind": "unavailable",
        "message": "ChatGPT (demostració) ha fallat a propòsit.",
        "round": 0,
    }


async def test_the_editor_failing_its_answer_hands_the_edits_to_the_other() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, fail={"answer"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(), providers, store)
    done = completed(events)
    assert [r.purpose for r in providers["claude"].requests] == ["answer"]
    assert {v.agent for v in versions(store)} == {"chatgpt"}
    assert final_of(store, done).agent == "chatgpt"


async def test_the_other_agent_edits_when_the_editor_fails() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, fail={"synthesis"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=3), providers, store)
    done = completed(events)
    # Claude merges first and fails; ChatGPT writes version 1, and from then on edits
    # first (an editor that failed is not tried again before the other one).
    assert streams_in(events, "edit") == [
        ("claude", 1),
        ("chatgpt", 1),
        ("chatgpt", 2),
        ("chatgpt", 3),
    ]
    assert [v.agent for v in versions(store)] == ["chatgpt", "chatgpt", "chatgpt"]
    assert all(refine_meta(v)["accepted"] is True for v in versions(store))
    assert done.stop_reason == "max_rounds"
    assert final_of(store, done).agent == "chatgpt"
    assert failures_of(outcome_of(store, done.turn_id)) == [("claude", 1)]


async def test_both_merges_failing_copy_the_editors_answer() -> None:
    store = InMemoryStore()
    events = await run(refine(max_rounds=2), fakes(fail={"synthesis"}), store)
    done = completed(events)
    claude_answer = next(m for m in messages(store, "answer") if m.agent == "claude")
    v1 = versions(store)[0]
    assert (v1.agent, v1.round, v1.content) == ("claude", 1, claude_answer.content)
    assert v1.meta["usage"] == Usage().to_dict()
    assert refine_meta(v1)["copied_from"] == claude_answer.id
    assert refine_meta(v1)["accepted"] is True and refine_meta(v1)["changelog"] == []
    first = rounds(events)[0]
    assert first.usage == Usage() and first.accepted and first.changes == ()
    # Round 2 cannot edit either: both agents are down, the turn ends with version 1.
    assert done.stop_reason == "failed"
    final = final_of(store, done)
    assert final.content == claude_answer.content and refine_meta(final)["version"] == 1
    assert outcome_of(store, done.turn_id)["status"] == "completed"


async def test_both_reviews_failing_after_version_1_complete_the_turn_with_it() -> None:
    store = InMemoryStore()
    events = await run(refine(), fakes(fail={"revision"}), store)
    done = completed(events)
    assert done.stop_reason == "failed"
    assert phases(events)[-1] == ("review", 2)
    last = rounds(events)[-1]
    assert (last.round, last.version, last.accepted) == (2, 1, False)
    assert last.reason == REFINE_FAILED_ROUND
    assert last.to_wire()["proposals"] == {"claude": None, "chatgpt": None}
    assert last.to_wire()["scores"] == {"claude": None, "chatgpt": None}
    outcome = outcome_of(store, done.turn_id)
    assert outcome["status"] == "completed" and outcome["stop_reason"] == "failed"
    assert sorted(failures_of(outcome), key=str) == [("chatgpt", 2), ("claude", 2)]
    assert refine_meta(final_of(store, done))["version"] == 1


async def test_a_review_without_its_changes_sits_out_the_round() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, refine_reviews=("Sense format.",)),
    }
    store = InMemoryStore()
    events = await run(refine(), providers, store, price_overrides=PRICES)
    done = completed(events)
    failed = of_type(events, StreamFailed)
    assert failed and all(f.error.kind == "invalid" for f in failed)
    assert all(f.usage is not None and f.usage.cost_usd for f in failed)  # billed all the same
    assert {m.agent for m in reviews(store)} == {"claude"}
    edit = next(r for r in providers["claude"].requests if is_edit(r))
    assert '<review from="Claude">' in edit.prompt and '<review from="ChatGPT">' not in edit.prompt
    # Claude alone finds nothing left in rounds 4 and 5; without ChatGPT's scores the
    # rounds never converge.
    assert done.stop_reason == "unchanged"
    assert [r.round for r in rounds(events)] == [1, 2, 3, 4, 5]
    assert done.usage == records_usage(store, done.turn_id)


CATALAN_KINDS = (
    "<changes>\n- [defecte] Pas 2: el total no quadra — 3 + 4 no fa 8.\n"
    "- [claredat] Pas 1: massa llarg — escurça'l.\n</changes>\n<score>40</score>"
)
PROSE = (
    "<changes>\nEl pas 2 té un error greu: el total no quadra. Cal corregir-lo.\n</changes>\n"
    "<score>30</score>"
)


async def test_reviews_that_write_the_kinds_in_catalan_propose_their_changes() -> None:
    providers = {
        agent: FakeProvider(agent, chunk_delay=0, refine_reviews=(CATALAN_KINDS,))
        for agent in AGENTS
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=3), providers, store)
    done = completed(events)
    # Not «nothing to change»: both reviews propose two changes every round.
    assert done.stop_reason == "max_rounds"
    for event in rounds(events)[1:]:
        assert event.proposals == {"claude": 2, "chatgpt": 2}
        assert (event.accepted, event.reason) == (True, None)
    first = reviews(store)[0]
    assert refine_meta(first)["unchanged"] is False
    assert refine_meta(first)["changes"] == [
        {"kind": "defect", "text": "Pas 2: el total no quadra — 3 + 4 no fa 8."},
        {"kind": "clarity", "text": "Pas 1: massa llarg — escurça'l."},
    ]
    assert [refine_meta(v)["version"] for v in versions(store)] == [1, 2, 3]


async def test_a_review_in_prose_fails_and_sits_out_the_round() -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,)),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, refine_reviews=(PROSE,)),
    }
    store = InMemoryStore()
    events = await run(refine(max_rounds=3), providers, store)
    done = completed(events)
    failed = of_type(events, StreamFailed)
    assert [(f.error.kind, f.error.message) for f in failed] == [("invalid", REFINE_NO_CHANGES)] * 2
    assert {m.agent for m in reviews(store)} == {"claude"}
    for event in rounds(events)[1:]:
        assert event.to_wire()["proposals"] == {"claude": 1, "chatgpt": None}
        assert event.accepted
    assert done.stop_reason == "max_rounds"
    assert failures_of(outcome_of(store, done.turn_id)) == [("chatgpt", 2), ("chatgpt", 3)]


async def test_two_reviews_in_prose_are_no_round_without_changes() -> None:
    providers = {
        agent: FakeProvider(agent, chunk_delay=0, refine_reviews=(PROSE,)) for agent in AGENTS
    }
    store = InMemoryStore()
    events = await run(refine(), providers, store)
    done = completed(events)
    # Neither review said there was nothing to change: no review came back in the format
    # asked for, and the turn ends with the current version, as when both fail.
    assert done.stop_reason == "failed"
    last = rounds(events)[-1]
    assert (last.round, last.version, last.accepted) == (2, 1, False)
    assert last.reason == REFINE_FAILED_ROUND
    assert reviews(store) == []
    assert refine_meta(final_of(store, done))["stop_reason"] == "failed"


async def test_both_answers_failing_fail_the_turn() -> None:
    store = InMemoryStore()
    events = await run(refine(), fakes(fail={"answer"}), store)
    failed = events[-1]
    assert isinstance(failed, TurnFailed)
    assert failed.error.message == "Cap dels dos agents ha pogut respondre."
    (question,) = messages(store, "question")
    outcome = question.meta["outcome"]
    assert isinstance(outcome, dict) and outcome["status"] == "failed"
    assert "stop_reason" not in outcome


# -- cancelling ----------------------------------------------------------------------------------


async def test_cancelling_during_an_edit_keeps_the_last_version_as_the_final() -> None:
    store = InMemoryStore()
    claude = Stalls("claude", is_edit, skip=1)  # round 3's edit
    providers: dict[AgentName, FakeProvider] = {
        "claude": claude,
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    outcomes: list[TurnOutcome] = []
    seen: list[ServerEvent] = []

    async def consume() -> None:
        async for event in engine.run(refine(), on_outcome=outcomes.append):
            seen.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(claude.stalled.wait(), 5)  # round 3's edit
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert claude.cancelled == 1
    assert other_tasks() == set()
    assert not of_type(seen, TurnCompleted) and not of_type(seen, TurnFailed)

    _v1, v2 = versions(store)
    (reported,) = outcomes
    assert reported.status == "cancelled" and reported.stop_reason == "owner"
    final = final_of(store, reported)
    assert (final.kind, final.final, final.content, final.agent) == (
        "synthesis",
        True,
        v2.content,
        "claude",
    )
    assert final.meta["copied_from"] == v2.id and final.meta["usage"] == Usage().to_dict()
    assert refine_meta(final) == {
        "role": "final",
        "version": 2,
        "words": words(v2.content),
        "budget_words": 300,
        "stop_reason": "owner",
    }
    (question,) = messages(store, "question")
    assert outcome_of(store, question.id) == reported.to_wire()
    assert outcome_of(store, question.id)["final_message_ids"] == [final.id]
    assert reported.usage == records_usage(store, question.id)


async def test_cancelling_before_the_first_version_stores_no_final() -> None:
    store = InMemoryStore()
    claude = Stalls("claude", lambda request: request.purpose == "synthesis")  # the merge
    providers: dict[AgentName, FakeProvider] = {
        "claude": claude,
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    outcomes: list[TurnOutcome] = []

    async def consume() -> None:
        engine = Engine(providers, store, retry_delay=0)
        async for _event in engine.run(refine(), on_outcome=outcomes.append):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(claude.stalled.wait(), 5)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    (reported,) = outcomes
    assert reported.status == "cancelled" and reported.stop_reason is None
    assert reported.final_message_ids == ()
    assert not [m for m in store.messages if m.final and m.kind != "question"]
    assert other_tasks() == set()


class BlockingFinal(InMemoryStore):
    """Its writes of a final version wait for :attr:`release`."""

    def __init__(self) -> None:
        super().__init__()
        self.writing = asyncio.Event()
        self.release = asyncio.Event()

    async def add_message(self, message: NewMessage) -> int:
        if message.kind == "synthesis":
            self.writing.set()
            await self.release.wait()
        return await super().add_message(message)


async def test_the_last_version_of_a_cancelled_turn_is_written_under_a_shield() -> None:
    store = BlockingFinal()
    claude = Stalls("claude", is_edit, skip=1)
    providers: dict[AgentName, FakeProvider] = {
        "claude": claude,
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    outcomes: list[TurnOutcome] = []

    async def consume() -> None:
        async for _event in engine.run(refine(request_id="r-final"), on_outcome=outcomes.append):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(claude.stalled.wait(), 5)
    consumer.cancel()
    await asyncio.wait_for(store.writing.wait(), 5)
    # The turn's own task cancelled again while the final is being written: the write and
    # the outcome after it still end, and the consumer's error waits for both.
    (turn_task,) = [task for task in other_tasks() if task.get_name() == "turn-r-final"]
    turn_task.cancel()
    await asyncio.wait((turn_task,), timeout=5)
    for _ in range(5):
        await asyncio.sleep(0)
    assert not consumer.done() and outcomes == []
    store.release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    (reported,) = outcomes
    assert reported.status == "cancelled" and reported.stop_reason == "owner"
    final = final_of(store, reported)
    assert final.content == versions(store)[-1].content
    (question,) = messages(store, "question")
    assert outcome_of(store, question.id) == reported.to_wire()
    for _ in range(5):
        await asyncio.sleep(0)
    assert other_tasks() == set()


# -- the cache, the history and the prompts ------------------------------------------------------


async def test_a_refine_turn_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_key(**kwargs: object) -> str:
        raise AssertionError("a refine turn has no cache key")

    monkeypatch.setattr(engine_module, "turn_cache_key", no_key)
    store = InMemoryStore()
    calls: list[str] = []
    original_get, original_put = store.cache_get, store.cache_put

    async def cache_get(key: str, now: datetime) -> CachedTurn | None:
        calls.append("get")
        return await original_get(key, now)

    async def cache_put(key: str, value: CachedTurn, expires_at: datetime) -> None:
        calls.append("put")
        await original_put(key, value, expires_at)

    store.cache_get = cache_get  # type: ignore[method-assign]
    store.cache_put = cache_put  # type: ignore[method-assign]
    providers = fakes()
    engine = Engine(providers, store, retry_delay=0)
    first = await collect(engine.run(refine(request_id="a")))
    calls_after_first = len(providers["claude"].requests)
    second = await collect(
        engine.run(refine(request_id="b", conversation_id=completed(first).conversation_id))
    )
    # The same question again is a new turn of its own: every call reaches a model.
    assert not completed(first).cached and not completed(second).cached
    assert len(providers["claude"].requests) > calls_after_first
    assert all(e.usage.processed_tokens > 0 for e in of_type(second, StreamCompleted)[:2])
    assert calls == [] and store.cache == {}


async def test_later_turns_see_the_question_and_the_final_version() -> None:
    store = InMemoryStore()
    providers = fakes()
    engine = Engine(providers, store, retry_delay=0)
    first = await collect(engine.run(refine(max_rounds=2)))
    done = completed(first)
    final = final_of(store, done)
    await collect(
        engine.run(TurnRequest("s", "I ara?", "solo", conversation_id=done.conversation_id))
    )
    history = providers["claude"].requests[-1].history
    assert [(t.role, t.agent, t.content) for t in history] == [
        ("user", None, BRIEF),
        ("assistant", "claude", final.content),
    ]


async def test_the_rounds_get_the_attachments_as_the_revisions_do(files: AttachmentFiles) -> None:
    store = InMemoryStore()
    pdf = store.add_attachment(files.pdf())
    image = store.add_attachment(files.image())
    providers = fakes()
    await run(refine(max_rounds=2, attachments=(pdf, image)), providers, store)
    claude = providers["claude"]
    modes = [
        (request.purpose, [attachment.mode for attachment in sent])
        for request, sent in zip(claude.requests, claude.attachments, strict=True)
    ]
    # The answers and the merge get them whole; every round, the PDF as its text.
    assert modes == [
        ("answer", ["full", "full"]),
        ("synthesis", ["full", "full"]),
        ("revision", ["text", "full"]),
        ("synthesis", ["text", "full"]),
    ]
    review = claude.requests[2]
    assert "informe.pdf (PDF, 2 pàgines; només el text extret)" in review.prompt


async def test_a_chatgpt_that_cannot_open_pdfs_reviews_them_through_claudes_check(
    files: AttachmentFiles,
) -> None:
    store = InMemoryStore()
    pdf = store.add_attachment(analysed(files, SALES, None, COSTS))
    check = reply(finding(2, "missing", text=TABLE), END)
    claude = FakeProvider("claude", chunk_delay=0, mode="cli", check_replies=[check])
    chatgpt = FakeProvider("chatgpt", chunk_delay=0, mode="cli")  # Codex
    events = await run(
        refine(max_rounds=2, attachments=(pdf,)), {"claude": claude, "chatgpt": chatgpt}, store
    )
    assert completed(events).stop_reason == "max_rounds"
    assert [r.purpose for r in chatgpt.requests] == ["answer", "revision"]
    assert all(sent.attachments[0].pdf_check is not None for sent in chatgpt.requests)
    # The prompts that weigh both agents' readings say which pages ChatGPT read through
    # Claude: the merge and the reviews.
    note = "Note: ChatGPT cannot open PDFs."
    merge = next(r for r in claude.requests if r.purpose == "synthesis")
    reviews_sent = [r for r in (*claude.requests, *chatgpt.requests) if r.purpose == "revision"]
    assert note in merge.prompt and len(reviews_sent) == 2
    assert all(note in sent.prompt for sent in reviews_sent)
    written = [m for m in store.messages if m.agent == "chatgpt"]
    assert [m.kind for m in written] == ["answer", "revision"]
    assert all(m.meta.get("pdf_reading") for m in written)


async def test_the_next_calls_are_prewarmed() -> None:
    providers = fakes()
    await run(refine(max_rounds=2), providers)
    # Claude edits: its merge and edits, and both agents' reviews.
    assert {r.purpose for r in providers["claude"].prewarmed} == {"synthesis", "revision"}
    assert {r.purpose for r in providers["chatgpt"].prewarmed} == {"revision"}


async def test_what_each_call_gets() -> None:
    providers = fakes()
    store = InMemoryStore()
    engine = Engine(providers, store, retry_delay=0)
    first = await collect(engine.run(TurnRequest("x", "Context previ", "solo")))
    conversation_id = completed(first).conversation_id
    await collect(engine.run(refine(conversation_id=conversation_id, max_rounds=3)))
    claude = providers["claude"].requests[1:]  # the refine turn's
    answer, merge, review, edit = claude[:4]
    # The answers and the merge carry the conversation; the rounds are self-contained.
    assert answer.history and merge.history
    assert review.history == () and edit.history == ()
    assert review.context_summary is None and edit.context_summary is None
    assert f"<brief>\n{BRIEF}\n</brief>" in review.prompt and BRIEF in edit.prompt
    assert '<answer from="ChatGPT">' in merge.prompt
    assert "<changelog_so_far>\n- v1 [merge] Els passos numerats" in review.prompt
    v1 = versions(store)[0]
    assert f"<current_version>\n{v1.content}\n</current_version>" in edit.prompt
    assert review.prompt.endswith(
        f"The current version has {V1_WORDS} words; every version must have at most 300 words."
    )
    last_review = [r for r in claude if r.purpose == "revision"][-1]
    assert "- v2 [defect]" in last_review.prompt
    assert (answer.purpose, merge.purpose, review.purpose, edit.purpose) == (
        "answer",
        "synthesis",
        "revision",
        "synthesis",
    )


async def test_the_changelog_the_prompts_get_is_its_tail() -> None:
    five = "\n".join(f"- [simplification] Treu el paràgraf {n}." for n in range(1, 6))

    def edit(number: int) -> Edit:
        return lambda version: version_reply(f"{version}\n\nRonda {number}.", five)

    providers: dict[AgentName, FakeProvider] = {
        "claude": Editor("claude", [edit(n) for n in range(10)], refine_reviews=(ALWAYS_A_DEFECT,)),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, refine_reviews=(ALWAYS_A_DEFECT,)),
    }
    await run(refine(max_rounds=9), providers)
    last = [r for r in providers["claude"].requests if r.purpose == "revision"][-1]
    listed = last.prompt.split("<changelog_so_far>\n", 1)[1].split("\n</changelog_so_far>")[0]
    lines = listed.splitlines()
    # 2 merge lines and 5 lines in each of 7 rounds: the latest 30.
    assert len(lines) == 30
    assert lines[0].startswith("- v3 ") and lines[-1].startswith("- v8 ")


# -- validation ------------------------------------------------------------------------------------


def test_the_engine_checks_the_ranges_the_server_validates() -> None:
    """The engine cannot import the server's settings (they depend on it), so it keeps
    its own copy of the ranges of the options it uses: the same ones."""
    assert engine_module.REFINE_ROUNDS_RANGE == models.REFINE_ROUNDS_RANGE
    assert engine_module.REFINE_WORDS_RANGE == models.REFINE_WORDS_RANGE
    assert engine_module.REFINE_THRESHOLD_RANGE == models.REFINE_THRESHOLD_RANGE


@pytest.mark.parametrize(
    ("request_", "message"),
    [
        (refine(max_rounds=1), "Les rondes de «Perfecciona» han de ser entre 2 i 50."),
        (refine(max_rounds=51), "Les rondes de «Perfecciona» han de ser entre 2 i 50."),
        (refine(max_words=99), "El límit de paraules ha de ser entre 100 i 20.000."),
        (refine(convergence_threshold=49), "El llindar de convergència ha de ser entre 50 i 100."),
        (refine(editor="gemini"), "Agent editor desconegut."),
        (refine(budget_usd=0.0), "El pressupost de «Perfecciona» ha de ser positiu."),
        (refine(budget_usd=float("nan")), "El pressupost de «Perfecciona» ha de ser positiu."),
    ],
)
async def test_invalid_refine_options(request_: TurnRequest, message: str) -> None:
    store = InMemoryStore()
    events = await run(request_, store=store)
    (failed,) = events
    assert isinstance(failed, TurnFailed)
    assert failed.error.kind == "invalid" and failed.error.message == message
    assert store.messages == []


async def test_every_purpose_of_a_refine_turn_is_a_billed_call() -> None:
    store = InMemoryStore()
    events = await run(refine(max_rounds=2), fakes(), store, price_overrides=PRICES)
    done = completed(events)
    purposes: list[Purpose] = [u.purpose for u in store.usage]
    assert purposes == ["answer", "answer", "synthesis", "revision", "revision", "synthesis"]
    assert done.usage == records_usage(store, done.turn_id) != Usage()
    assert done.usage.cost_usd is not None
    assert store.savings == []
