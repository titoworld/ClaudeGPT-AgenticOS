"""Fixtures for every test."""

from __future__ import annotations

import pytest

from agentic_os import i18n


@pytest.fixture(autouse=True)
def catalan_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests check the texts in Catalan, the language they were written in: it is the
    language of every text nothing chose one for (``tests/test_i18n.py`` checks the others).
    The system locale chooses none either."""
    monkeypatch.setattr(i18n, "DEFAULT_LANG", "ca")
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
