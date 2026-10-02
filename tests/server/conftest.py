"""The server refreshes the ECB exchange rate in the background: never download it
in tests (no network, and it would make them slow and flaky)."""

from collections.abc import Iterator

import pytest

from agentic_os import fx


class BlockedEcb:
    """Replacement of ``fx.fetch_ecb_rate`` that always fails, counting the calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> fx.FxRate:
        self.calls += 1
        raise fx.FxError("Sense xarxa als tests.")


@pytest.fixture(autouse=True)
def blocked_ecb(monkeypatch: pytest.MonkeyPatch) -> Iterator[BlockedEcb]:
    blocked = BlockedEcb()
    monkeypatch.setattr(fx, "fetch_ecb_rate", blocked)
    yield blocked
