"""Unit tests of the engine's building blocks: tokens, prompts, memory, cache, accounting."""

from datetime import UTC, datetime

import pytest

from agentic_os.domain import DebateOptions, TurnOptions, Usage
from agentic_os.orchestrator.accounting import TurnAccounting
from agentic_os.orchestrator.cache import (
    context_fingerprint,
    normalize_question,
    replay_cost_usd,
    replayed_message,
    turn_cache_key,
)
from agentic_os.orchestrator.memory import build_context, canonical_messages, compaction_cut
from agentic_os.orchestrator.prompts import (
    SUMMARY_PROMPT,
    debate_answer_prompt,
    revision_prompt,
    synthesis_prompt,
    system_prompt,
)
from agentic_os.orchestrator.store import JsonValue, NewMessage, StoredMessage
from agentic_os.orchestrator.tokens import (
    estimate_context_tokens,
    estimate_tokens,
    estimate_turns_tokens,
)
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import ChatTurn

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def message(
    id_: int, kind: str, content: str, *, turn_id: int, final: bool = True
) -> StoredMessage:
    assert kind in ("question", "answer", "revision", "synthesis")
    return StoredMessage(
        id=id_,
        conversation_id=1,
        turn_id=turn_id,
        kind=kind,  # type: ignore[arg-type]
        content=content,
        agent=None if kind == "question" else "claude",
        round=0,
        final=final,
        meta={},
        created_at=NOW,
    )


# -- tokens ---------------------------------------------------------------------------


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens("x" * 401) == 101


def test_estimate_turns_and_context() -> None:
    turns = [ChatTurn("user", "x" * 40), ChatTurn("assistant", "y" * 80, agent="claude")]
    assert estimate_turns_tokens(turns) == 10 + 20 + 2 * 4
    assert estimate_context_tokens(None, turns) == estimate_turns_tokens(turns)
    assert estimate_context_tokens("z" * 40, turns) == estimate_turns_tokens(turns) + 14


# -- prompts --------------------------------------------------------------------------


def test_system_prompts_are_stable_and_ask_for_the_users_language() -> None:
    for agent, vendor in (("claude", "Anthropic"), ("chatgpt", "OpenAI")):
        prompt = system_prompt(agent)  # type: ignore[arg-type]
        assert prompt == system_prompt(agent)  # type: ignore[arg-type]
        assert vendor in prompt
        assert "language of the user's message" in prompt
        assert "Markdown" in prompt
    assert system_prompt("claude").startswith("You are Claude")
    assert system_prompt("chatgpt").startswith("You are ChatGPT")


def test_debate_and_revision_prompts() -> None:
    answer = debate_answer_prompt("claude", "Quina hora és?")
    assert "ChatGPT" in answer and "review" in answer
    assert answer.endswith("<user_message>\nQuina hora és?\n</user_message>")

    revision = revision_prompt("chatgpt", "Q?", "meva", "seva")
    for part in ("<critique>", "<answer>", "UNCHANGED", "<agreement>N</agreement>"):
        assert part in revision
    assert "<question>\nQ?\n</question>" in revision
    assert "<your_previous_answer>\nmeva\n</your_previous_answer>" in revision
    assert "<claude_answer>\nseva\n</claude_answer>" in revision
    assert "language of the user's question" in revision
    # The fixed instructions come first so the cacheable prefix is stable.
    assert revision.index("Reply with exactly") < revision.index("<question>")


def test_synthesis_and_summary_prompts() -> None:
    prompt = synthesis_prompt(
        "Q?", {"claude": "A1", "chatgpt": "A2"}, {"claude": "- error", "chatgpt": "- None."}
    )
    assert '<answer from="Claude">\nA1\n</answer>' in prompt
    assert '<answer from="ChatGPT">\nA2\n</answer>' in prompt
    assert '<critique from="Claude" about="ChatGPT">\n- error\n</critique>' in prompt
    assert 'critique from="ChatGPT"' not in prompt
    assert "language of the user's question" in prompt
    assert "300 words" in SUMMARY_PROMPT and "same language" in SUMMARY_PROMPT


# -- memory ---------------------------------------------------------------------------


def test_canonical_messages_skip_unanswered_questions() -> None:
    messages = [
        message(1, "question", "Q1", turn_id=1),
        message(2, "answer", "A1", turn_id=1),
        message(3, "question", "Q2 (fallida)", turn_id=3),
        message(4, "question", "Q3", turn_id=4),
        message(5, "revision", "R3", turn_id=4, final=False),
        message(6, "synthesis", "S3", turn_id=4),
    ]
    assert [m.id for m in canonical_messages(messages)] == [1, 2, 4, 6]
    context = build_context(None, messages)
    assert [t.role for t in context.history] == ["user", "assistant", "user", "assistant"]
    assert context.history[1].agent == "claude"


def test_compaction_cut_respects_turn_boundaries() -> None:
    messages = []
    for turn in range(4):  # question + two duel answers per turn
        base = turn * 3 + 1
        messages += [
            message(base, "question", "q" * 400, turn_id=base),
            message(base + 1, "answer", "a" * 400, turn_id=base),
            message(base + 2, "answer", "b" * 400, turn_id=base),
        ]
    context = build_context(None, messages)
    assert compaction_cut(context, threshold=context.tokens, keep_recent=2) is None
    # Keeping 2 would split the last turn: the cut moves back to its question.
    assert compaction_cut(context, threshold=10, keep_recent=2) == 9
    assert compaction_cut(context, threshold=10, keep_recent=6) == 6
    assert compaction_cut(context, threshold=10, keep_recent=0) == 12
    assert compaction_cut(context, threshold=10, keep_recent=50) is None


# -- cache ----------------------------------------------------------------------------


def test_cache_key_normalization_and_sensitivity() -> None:
    context = build_context(None, [])
    fingerprint = context_fingerprint(context)
    identities = {"claude": "fake:fake-claude", "chatgpt": "fake:fake-chatgpt"}

    def key(**overrides: object) -> str:
        args: dict[str, object] = {
            "mode": "duel",
            "target": "claude",
            "options": TurnOptions(),
            "question": "Hola  món",
            "context_fingerprint": fingerprint,
            "identities": identities,
        }
        args.update(overrides)
        return turn_cache_key(**args)  # type: ignore[arg-type]

    assert normalize_question("  Hola\n\t món ") == "Hola món"
    assert key() == key(question=" Hola món\n")
    assert key() == key(target="chatgpt")  # the target only matters in solo
    assert key() == key(options=TurnOptions(debate=DebateOptions(rounds=3)))
    assert key(mode="solo") != key(mode="solo", target="chatgpt")
    assert key(mode="debate") != key(
        mode="debate", options=TurnOptions(debate=DebateOptions(rounds=3))
    )
    assert key() != key(mode="debate")
    assert key() != key(question="Hola mon")
    assert key() != key(identities={**identities, "claude": "api:claude-opus-5"})
    other_context = build_context("resum", [])
    assert key() != key(context_fingerprint=context_fingerprint(other_context))


def test_replayed_message_is_flagged_as_cached_and_costs_nothing() -> None:
    meta: dict[str, JsonValue] = {
        "x": 1,
        "usage": dict(Usage(input_tokens=5, cost_usd=0.1).to_dict()),
        "cost_basis": "api",
        "savings": {"cache": 0},
    }
    original = NewMessage(
        conversation_id=1, kind="answer", content="A", turn_id=1, agent="claude", meta=meta
    )
    replayed = replayed_message(original, conversation_id=7, turn_id=9)
    assert (replayed.conversation_id, replayed.turn_id) == (7, 9)
    assert replayed.meta == {"x": 1, "cached": True, "usage": Usage().to_dict()}
    assert original.meta == meta


def test_replay_cost_is_recomputed_at_current_prices() -> None:
    def answer(model: JsonValue, usage: JsonValue) -> NewMessage:
        meta: dict[str, JsonValue] = {"model": model, "usage": usage}
        return NewMessage(1, "answer", "A", turn_id=1, agent="claude", meta=meta)

    tokens: JsonValue = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cost_usd": 99.0}
    messages = [
        answer("claude-opus-5", tokens),  # 5 + 25 at the default prices
        answer("mystery-model", tokens),  # no price: ignored
        answer(None, tokens),
        answer("claude-opus-5", "corrupt"),
    ]
    assert replay_cost_usd(messages) == pytest.approx(30.0)
    overrides = {"mystery-model": ModelPrice(1, 1, 0, 0)}
    assert replay_cost_usd(messages, overrides) == pytest.approx(32.0)
    assert replay_cost_usd(messages[1:]) is None


# -- accounting -----------------------------------------------------------------------


def test_accounting_savings() -> None:
    accounting = TurnAccounting()
    accounting.add_call(Usage(input_tokens=100, output_tokens=50), "answer")
    accounting.add_call(Usage(input_tokens=300, output_tokens=100), "revision")
    accounting.add_call(Usage(input_tokens=200, output_tokens=0), "revision")
    accounting.add_summary(Usage(input_tokens=10, output_tokens=5))
    accounting.add_early_stop(2)  # 2 rounds x (2 x 300 average per call)
    accounting.add_unchanged("x" * 40)
    accounting.compaction_per_request = 30
    accounting.add_context_request()
    accounting.add_context_request()
    savings = accounting.savings()
    assert (savings.early_stop, savings.unchanged, savings.compaction) == (1200, 10, 60)
    assert accounting.usage.total_tokens == 765
    assert accounting.turn_usage.total_tokens == 750
    records = accounting.saving_records(1, 2)
    assert {r.kind for r in records} == {"early_stop", "unchanged", "compaction"}
    assert all(r.turn_id == 2 and r.tokens_saved > 0 for r in records)


def test_savings_value_uses_the_average_price_of_priced_calls() -> None:
    accounting = TurnAccounting()
    assert accounting.savings().cost_usd is None
    accounting.add_call(Usage(input_tokens=100, output_tokens=0), "answer")  # no price
    assert accounting.savings().cost_usd is None
    accounting.add_call(Usage(input_tokens=300, output_tokens=100, cost_usd=0.002), "answer")
    assert accounting.savings().cost_usd == 0.0  # known price, nothing saved yet
    accounting.add_unchanged("x" * 400)  # 100 tokens at 0.002 / 400 per token
    accounting.compaction_per_request = 50
    accounting.add_context_request()
    assert accounting.savings().cost_usd == pytest.approx(150 * 0.002 / 400)

    cached = TurnAccounting()
    cached.cache, cached.cache_cost = 1234, 0.25
    assert cached.savings().cost_usd == 0.25
    cached.cache_cost = None
    assert cached.savings().cost_usd is None


def test_early_stop_without_revisions_is_zero() -> None:
    accounting = TurnAccounting()
    accounting.add_early_stop(3)
    assert accounting.savings().total == 0
    assert accounting.saving_records(1, 1) == []
