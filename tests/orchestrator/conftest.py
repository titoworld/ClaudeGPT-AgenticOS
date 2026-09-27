"""Fixtures for the orchestrator tests (fake providers, in-memory store)."""

from __future__ import annotations

import pytest

from agentic_os.domain import AgentName
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.providers.fake import FakeProvider


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def fakes() -> dict[AgentName, FakeProvider]:
    return {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }


@pytest.fixture
def engine(fakes: dict[AgentName, FakeProvider], store: InMemoryStore) -> Engine:
    return Engine(fakes, store, retry_delay=0)
