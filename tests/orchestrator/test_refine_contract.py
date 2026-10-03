"""The shared contract of the refine mode (docs/adr/0010-refine-mode.md): its options,
how words are counted and the wire of its events."""

from __future__ import annotations

from agentic_os.domain import (
    REFINE_BUDGET_FACTOR,
    REFINE_CHANGELOG_TAIL,
    REFINE_CONVERGENCE_ROUNDS,
    REFINE_MAX_CHANGES,
    REFINE_MIN_BUDGET_WORDS,
    RefineOptions,
    TurnOptions,
    Usage,
    words,
)
from agentic_os.orchestrator.events import (
    PhaseChanged,
    RefineChange,
    RefineRound,
    Savings,
    StreamCompleted,
    TurnCompleted,
    TurnOutcome,
    TurnStopping,
)

ROUND_USAGE = Usage(input_tokens=1200, output_tokens=300, cost_usd=0.01)
TOTAL = Usage(input_tokens=5000, output_tokens=900, cost_usd=0.04)


def test_the_options_of_a_refine_turn() -> None:
    assert TurnOptions().refine == RefineOptions(
        max_rounds=12,
        budget_eur=3.0,
        max_words=None,
        stop_on_convergence=True,
        convergence_threshold=90,
        editor="claude",
    )
    assert (REFINE_MAX_CHANGES, REFINE_CHANGELOG_TAIL, REFINE_CONVERGENCE_ROUNDS) == (5, 30, 2)
    assert (REFINE_MIN_BUDGET_WORDS, REFINE_BUDGET_FACTOR) == (300, 1.2)


def test_words_are_runs_of_non_space() -> None:
    assert words("") == 0
    assert words("  Un pla\nde  tres\tpassos. ") == 5
    assert words("**Objectiu:** reduir-ho a la meitat") == 5


def test_the_phases_of_a_refine_round() -> None:
    assert PhaseChanged("r", "review", 3).to_wire() == {
        "type": "phase",
        "request_id": "r",
        "phase": "review",
        "round": 3,
    }
    assert PhaseChanged("r", "edit", 3).to_wire()["phase"] == "edit"


def test_a_round_on_the_wire() -> None:
    event = RefineRound(
        "r",
        round=4,
        version=4,
        accepted=True,
        words=812,
        budget_words=960,
        usage=ROUND_USAGE,
        total=TOTAL,
        changes=(
            RefineChange("defect", "Corregeix el total del pressupost."),
            RefineChange("simplification", "Treu el paràgraf repetit."),
        ),
        proposals={"claude": 2, "chatgpt": 1},
        scores={"claude": 84, "chatgpt": 88},
    )
    assert event.to_wire() == {
        "type": "refine.round",
        "request_id": "r",
        "round": 4,
        "version": 4,
        "accepted": True,
        "reason": None,
        "reason_code": None,
        "words": 812,
        "budget_words": 960,
        "changes": [
            {"kind": "defect", "text": "Corregeix el total del pressupost."},
            {"kind": "simplification", "text": "Treu el paràgraf repetit."},
        ],
        "proposals": {"claude": 2, "chatgpt": 1},
        "scores": {"claude": 84, "chatgpt": 88},
        "converged": False,
        "usage": ROUND_USAGE.to_dict(),
        "total": TOTAL.to_dict(),
    }


def test_a_round_without_a_new_version_names_both_agents_all_the_same() -> None:
    wire = RefineRound(
        "r",
        round=6,
        version=5,
        accepted=False,
        words=900,
        budget_words=960,
        usage=ROUND_USAGE,
        total=TOTAL,
        reason="La nova versió passava del límit de paraules.",
        proposals={"claude": 1},
        scores={"claude": 91},
    ).to_wire()
    assert wire["changes"] == []
    assert wire["proposals"] == {"claude": 1, "chatgpt": None}
    assert wire["scores"] == {"claude": 91, "chatgpt": None}
    assert wire["reason"] == "La nova versió passava del límit de paraules."


def test_stopping_after_the_round() -> None:
    assert TurnStopping("r", 7).to_wire() == {
        "type": "turn.stopping",
        "request_id": "r",
        "round": 7,
    }


def test_the_refine_meta_of_a_message_goes_with_its_stream_completed() -> None:
    plain = StreamCompleted("r", "s", 12, ROUND_USAGE, 900, 120).to_wire()
    assert "refine" not in plain
    meta = {"role": "review", "score": 84, "unchanged": False, "changes": []}
    assert (
        StreamCompleted("r", "s", 12, ROUND_USAGE, 900, 120, refine=meta).to_wire()["refine"]
        == meta
    )


def test_the_stop_reason_is_on_the_wire_only_for_refine_turns() -> None:
    savings = Savings()
    completed = TurnCompleted("r", 3, 9, [41], TOTAL, savings)
    assert "stop_reason" not in completed.to_wire()
    refined = TurnCompleted("r", 3, 9, [41], TOTAL, savings, stop_reason="owner")
    assert refined.to_wire()["stop_reason"] == "owner"
    assert "stop_reason" not in TurnOutcome("completed", TOTAL, savings).to_wire()
    outcome = TurnOutcome("cancelled", TOTAL, savings, final_message_ids=[41], stop_reason="owner")
    assert outcome.to_wire()["stop_reason"] == "owner"
