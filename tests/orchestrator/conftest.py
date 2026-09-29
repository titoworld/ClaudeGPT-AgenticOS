"""Fixtures for the orchestrator tests (fake providers, in-memory store)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_os.domain import AgentName
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.providers.fake import FakeProvider
from orchestrator.attachment_fixtures import AttachmentFiles


@pytest.fixture
def files(tmp_path: Path) -> AttachmentFiles:
    """Attachments stored under ``tmp_path`` (see ``AttachmentFiles``)."""
    return AttachmentFiles(tmp_path / "attachments")


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
