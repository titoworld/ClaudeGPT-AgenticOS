"""Fixtures shared by the provider tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator.attachment_fixtures import AttachmentFiles


@pytest.fixture
def files(tmp_path: Path) -> AttachmentFiles:
    """Attachments stored under ``tmp_path`` (see ``AttachmentFiles``)."""
    return AttachmentFiles(tmp_path / "attachments")
