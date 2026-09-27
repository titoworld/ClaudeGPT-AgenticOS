import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentic_os.domain import DebateOptions, TurnOptions
from agentic_os.storage.models import RuntimeSettings, format_ts, parse_ts

VALID = {
    "default_mode": "duel",
    "default_target": "chatgpt",
    "debate": {"rounds": 0, "consensus_threshold": 100, "synthesizer": "chatgpt"},
    "use_cache": False,
    "compaction_threshold_tokens": 100_000,
}


def test_defaults_match_the_protocol() -> None:
    settings = RuntimeSettings()
    assert settings.to_wire() == {
        "default_mode": "debate",
        "default_target": "claude",
        "debate": {"rounds": 2, "consensus_threshold": 85, "synthesizer": "claude"},
        "use_cache": True,
        "compaction_threshold_tokens": 6000,
    }
    assert settings.to_turn_options() == TurnOptions()


def test_wire_roundtrip_and_turn_options() -> None:
    settings = RuntimeSettings.from_wire(VALID)
    assert settings.to_wire() == VALID
    assert RuntimeSettings.from_wire(settings.to_wire()) == settings
    assert settings.to_turn_options() == TurnOptions(
        debate=DebateOptions(rounds=0, consensus_threshold=100, synthesizer="chatgpt"),
        use_cache=False,
    )


def test_missing_keys_take_defaults_and_unknown_keys_are_ignored() -> None:
    settings = RuntimeSettings.from_wire({"debate": {"rounds": 3}, "extra": 1})
    assert settings.debate == DebateOptions(rounds=3)
    assert settings.default_mode == "debate"
    assert RuntimeSettings.from_wire({}) == RuntimeSettings()


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"default_mode": "trio"}, "default_mode"),
        ({"default_target": "gemini"}, "default_target"),
        ({"debate": {"rounds": 5}}, "debate.rounds"),
        ({"debate": {"rounds": -1}}, "debate.rounds"),
        ({"debate": {"rounds": True}}, "debate.rounds"),
        ({"debate": {"rounds": 2.0}}, "debate.rounds"),
        ({"debate": {"consensus_threshold": 49}}, "debate.consensus_threshold"),
        ({"debate": {"consensus_threshold": 101}}, "debate.consensus_threshold"),
        ({"debate": {"synthesizer": "user"}}, "debate.synthesizer"),
        ({"debate": []}, "debate"),
        ({"use_cache": 1}, "use_cache"),
        ({"compaction_threshold_tokens": 999}, "compaction_threshold_tokens"),
        ({"compaction_threshold_tokens": 100_001}, "compaction_threshold_tokens"),
        ({"compaction_threshold_tokens": "6000"}, "compaction_threshold_tokens"),
    ],
)
def test_invalid_values_raise_catalan_errors(patch: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(f"«{message}» ha de ser")) as exc_info:
        RuntimeSettings.from_wire({**VALID, **patch})
    assert "ha de ser" in str(exc_info.value)


def test_non_object_is_rejected() -> None:
    with pytest.raises(ValueError, match="objecte JSON"):
        RuntimeSettings.from_wire(["debate"])


def test_direct_construction_is_validated() -> None:
    with pytest.raises(ValueError, match=r"debate\.consensus_threshold"):
        RuntimeSettings(debate=DebateOptions(consensus_threshold=10))


def test_timestamps_are_fixed_width_utc() -> None:
    aware = datetime(2026, 1, 2, 3, 4, 5, 678_901, tzinfo=timezone(timedelta(hours=2)))
    assert format_ts(aware) == "2026-01-02T01:04:05.678Z"
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert format_ts(naive) == "2026-01-02T03:04:05.000Z"
    parsed = parse_ts("2026-01-02T01:04:05.678Z")
    assert parsed == datetime(2026, 1, 2, 1, 4, 5, 678_000, tzinfo=UTC)
    assert parsed.tzinfo is UTC
