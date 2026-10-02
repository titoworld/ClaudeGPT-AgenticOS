"""The providers' model identity in the turn cache key (review finding N16).

The engine asks each provider's ``status()`` which model answers, and the answer goes in
the cache key. A status slower than the engine waits used to be remembered as ``?`` for
the whole process: after a restart with another model and a slow status again, the key
matched and the new model replayed the old model's answers for up to 7 days. So did the
model an unavailable provider names without having read its configuration.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Callable, Mapping, Sequence

import pytest

import agentic_os.orchestrator.engine as engine_module
from agentic_os.domain import AgentName
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import ServerEvent, StreamStarted, TurnCompleted
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.providers.base import ProviderStatus
from agentic_os.providers.fake import FakeProvider

STATUS_WAIT = 0.05
"""STATUS_TIMEOUT_SECONDS in these tests (2 s in production)."""
GUESS = "catalog-default"
"""The model an unavailable status names (Codex's catalog default until it has read the
configured one)."""


class ConfiguredFake(FakeProvider):
    """Claude with ``model`` as its configured default. Its i-th ``status()`` takes
    ``delays[i]`` seconds (none once they run out), or fails when the delay is None. The
    first ``unavailable`` statuses that answer could not read the configuration: they
    are unavailable and name :data:`GUESS`. The first ``unknown`` ones are available but
    name no model (Codex when it cannot read which model a thread gets)."""

    def __init__(
        self,
        model: str,
        delays: Sequence[float | None] = (),
        *,
        unavailable: int = 0,
        unknown: int = 0,
    ) -> None:
        super().__init__("claude", chunk_delay=0)
        self._model = model
        self._delays = list(delays)
        self._unavailable = unavailable
        self._unknown = unknown
        self.asked = 0
        self.answered = 0
        self.cancelled = 0

    @property
    def model(self) -> str:
        return self._model

    async def status(self) -> ProviderStatus:
        self.asked += 1
        delay = self._delays.pop(0) if self._delays else 0.0
        if delay is None:
            raise RuntimeError("status unavailable")
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.answered += 1
        status = await super().status()
        if self._unavailable > 0:
            self._unavailable -= 1
            return dataclasses.replace(status, available=False, model=GUESS, detail="Sense sessió")
        if self._unknown > 0:
            self._unknown -= 1
            return dataclasses.replace(status, model="")
        return status


@pytest.fixture(autouse=True)
def short_status_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "STATUS_TIMEOUT_SECONDS", STATUS_WAIT)


async def ask(
    engine: Engine, request_id: str, models: Mapping[AgentName, str] | None = None
) -> list[ServerEvent]:
    request = TurnRequest(request_id, "Què és X?", "solo", models=models or {})
    return [event async for event in engine.run(request)]


def completed(events: Sequence[ServerEvent]) -> TurnCompleted:
    last = events[-1]
    assert isinstance(last, TurnCompleted), last
    return last


def answer_models(store: InMemoryStore) -> list[object]:
    return [message.meta["model"] for message in store.messages if message.kind == "answer"]


async def wait_until(condition: Callable[[], object], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_a_slow_status_never_replays_another_models_answer(store: InMemoryStore) -> None:
    """The review's scenario: a slow status, then a restart with another model whose
    status is slow again. The second model must answer, not replay the first one."""
    opus = Engine({"claude": ConfiguredFake("opus", [0.5])}, store, retry_delay=0)
    assert not completed(await ask(opus, "r1")).cached

    sonnet = Engine({"claude": ConfiguredFake("sonnet", [0.5])}, store, retry_delay=0)
    assert not completed(await ask(sonnet, "r2")).cached
    assert answer_models(store) == ["opus", "sonnet"]


async def test_a_slow_status_is_waited_for_in_the_background(store: InMemoryStore) -> None:
    """A turn waits STATUS_TIMEOUT_SECONDS at most, and a turn whose model it could not
    learn skips the turn cache. The status is not cancelled: its answer is remembered
    and the next turns use the cache as usual."""
    provider = ConfiguredFake("opus", [0.5])
    engine = Engine({"claude": provider}, store, retry_delay=0)

    first = await ask(engine, "r1")
    assert provider.answered == 0  # the turn did not wait for the status
    assert not completed(first).cached
    assert store.cache == {}  # a key without the model would match any model's turns

    await wait_until(lambda: provider.answered == 1)
    assert provider.cancelled == 0
    second = await ask(engine, "r2")
    assert not completed(second).cached  # nothing was cached by the first turn
    assert [event.model for event in second if isinstance(event, StreamStarted)] == ["opus"]
    assert len(store.cache) == 1
    assert completed(await ask(engine, "r3")).cached
    assert provider.asked == 1
    assert answer_models(store) == ["opus", "opus", "opus"]


async def test_a_failed_status_is_asked_again(store: InMemoryStore) -> None:
    provider = ConfiguredFake("opus", [None])
    engine = Engine({"claude": provider}, store, retry_delay=0)

    assert not completed(await ask(engine, "r1")).cached
    assert store.cache == {}
    assert not completed(await ask(engine, "r2")).cached
    assert completed(await ask(engine, "r3")).cached
    assert provider.asked == 2


async def test_an_unavailable_status_is_not_remembered(store: InMemoryStore) -> None:
    """An unavailable provider could not read its configuration, so the model it names
    may be a guess. The turn skips the turn cache and the next turn asks again: after a
    restart with another model and the same guess, nothing is replayed."""
    opus = ConfiguredFake("opus", unavailable=1)
    engine = Engine({"claude": opus}, store, retry_delay=0)

    assert not completed(await ask(engine, "r1")).cached
    assert store.cache == {}
    assert not completed(await ask(engine, "r2")).cached  # available now
    assert completed(await ask(engine, "r3")).cached
    assert opus.asked == 2

    sonnet = Engine({"claude": ConfiguredFake("sonnet", unavailable=1)}, store, retry_delay=0)
    assert not completed(await ask(sonnet, "r4")).cached
    assert answer_models(store) == ["opus", "opus", "opus", "sonnet"]


async def test_a_turn_that_names_its_model_does_not_ask_the_status(
    store: InMemoryStore,
) -> None:
    """The model the turn asks for is its identity: a slow status does not delay it and
    the turn uses the cache as usual."""
    provider = ConfiguredFake("opus", [0.5])
    engine = Engine({"claude": provider}, store, retry_delay=0)

    first = await ask(engine, "r1", models={"claude": "sonnet"})
    assert not completed(first).cached
    assert len(store.cache) == 1
    assert completed(await ask(engine, "r2", models={"claude": "sonnet"})).cached
    assert provider.asked == 0
    assert answer_models(store) == ["sonnet", "sonnet"]


async def test_a_status_that_names_no_model_is_not_remembered(store: InMemoryStore) -> None:
    """Available, but without the model a thread gets (Codex could not read its
    configuration): the turn skips the turn cache and the next turn asks again."""
    opus = ConfiguredFake("opus", unknown=1)
    engine = Engine({"claude": opus}, store, retry_delay=0)

    assert not completed(await ask(engine, "r1")).cached
    assert store.cache == {}
    assert not completed(await ask(engine, "r2")).cached  # the model is known now
    assert completed(await ask(engine, "r3")).cached
    assert opus.asked == 2


async def test_concurrent_turns_share_one_status_check(store: InMemoryStore) -> None:
    provider = ConfiguredFake("opus", [0.5])
    engine = Engine({"claude": provider}, store, retry_delay=0)

    turns = await asyncio.gather(*(ask(engine, f"c{i}") for i in range(10)))
    assert not any(completed(events).cached for events in turns)
    assert provider.asked == 1
    await wait_until(lambda: provider.answered == 1)


async def test_a_hung_status_check_is_abandoned_and_asked_again(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check still running after STATUS_CHECK_TIMEOUT_SECONDS is cancelled, so it
    never blocks the checks of later turns."""
    monkeypatch.setattr(engine_module, "STATUS_CHECK_TIMEOUT_SECONDS", 0.2)
    provider = ConfiguredFake("opus", [30.0])  # the first status never answers in time
    engine = Engine({"claude": provider}, store, retry_delay=0)

    assert not completed(await ask(engine, "r1")).cached
    await wait_until(lambda: provider.cancelled == 1)
    assert not completed(await ask(engine, "r2")).cached  # asked again, and it answers
    assert provider.asked == 2
    assert completed(await ask(engine, "r3")).cached
