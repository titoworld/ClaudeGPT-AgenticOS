import re
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from agentic_os.domain import DebateOptions, RefineOptions, TurnOptions
from agentic_os.fx import FxRate
from agentic_os.pricing import ModelPrice
from agentic_os.storage.models import (
    TURN_MODES,
    FxSettings,
    RuntimeSettings,
    StoredFxRate,
    effective_fx,
    format_ts,
    parse_ts,
)

VALID: dict[str, Any] = {
    "revision": 3,
    "default_mode": "duel",
    "default_target": "chatgpt",
    "debate": {"rounds": 0, "consensus_threshold": 100, "synthesizer": "chatgpt"},
    "refine": {
        "max_rounds": 50,
        "budget_eur": 0.1,
        "max_words": 20_000,
        "stop_on_convergence": False,
        "convergence_threshold": 50,
        "editor": "chatgpt",
    },
    "use_cache": False,
    "compaction_threshold_tokens": 100_000,
    "models": {"claude": "opus", "chatgpt": None},
    "fast_models": {"claude": None, "chatgpt": "gpt-6-luna"},
    "prices": {
        "my-new-model": {"input": 1.5, "output": 6.0, "cache_read": 0.15, "cache_write": 0.0},
        "claude-opus-5": {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write": 5.0},
    },
    "fx": {"mode": "manual", "eur_per_usd": 0.91},
    "budgets_eur": {"claude": 25.0, "chatgpt": None},
    "plans_eur": {"claude": 100.0, "chatgpt": 0.0},
    "pdf_in_revisions": "full",
}


def test_defaults_match_the_protocol() -> None:
    settings = RuntimeSettings()
    assert settings.to_wire() == {
        "revision": 0,
        "default_mode": "debate",
        "default_target": "claude",
        "debate": {"rounds": 2, "consensus_threshold": 85, "synthesizer": "claude"},
        "refine": {
            "max_rounds": 12,
            "budget_eur": 3.0,
            "max_words": None,
            "stop_on_convergence": True,
            "convergence_threshold": 90,
            "editor": "claude",
        },
        "use_cache": True,
        "compaction_threshold_tokens": 6000,
        "models": {"claude": None, "chatgpt": None},
        "fast_models": {"claude": None, "chatgpt": None},
        "prices": {},
        "fx": {"mode": "auto", "eur_per_usd": 0.86},
        "budgets_eur": {"claude": None, "chatgpt": None},
        "plans_eur": {"claude": None, "chatgpt": None},
        "pdf_in_revisions": "text",
    }
    assert settings.to_turn_options() == TurnOptions()
    assert settings.chosen_models() == {}
    assert settings.chosen_fast_models() == {}


def test_wire_roundtrip_and_turn_options() -> None:
    settings = RuntimeSettings.from_wire(VALID)
    assert settings.to_wire() == VALID
    assert RuntimeSettings.from_wire(settings.to_wire()) == settings
    assert settings.to_turn_options() == TurnOptions(
        debate=DebateOptions(rounds=0, consensus_threshold=100, synthesizer="chatgpt"),
        refine=RefineOptions(
            max_rounds=50,
            budget_eur=0.1,
            max_words=20_000,
            stop_on_convergence=False,
            convergence_threshold=50,
            editor="chatgpt",
        ),
        use_cache=False,
    )
    assert settings.chosen_models() == {"claude": "opus"}
    assert settings.chosen_fast_models() == {"chatgpt": "gpt-6-luna"}
    assert settings.prices["my-new-model"] == ModelPrice(1.5, 6.0, 0.15, 0.0)
    assert settings.fx == FxSettings(mode="manual", eur_per_usd=0.91)
    prices = settings.to_wire()["prices"]
    assert isinstance(prices, dict)
    assert list(prices) == ["claude-opus-5", "my-new-model"]


def test_missing_keys_take_defaults_and_unknown_keys_are_ignored() -> None:
    settings = RuntimeSettings.from_wire({"debate": {"rounds": 3}, "extra": 1})
    assert settings.debate == DebateOptions(rounds=3)
    assert settings.default_mode == "debate"
    assert RuntimeSettings.from_wire({}) == RuntimeSettings()


def test_settings_saved_by_an_older_version_still_load() -> None:
    old = {k: VALID[k] for k in ("default_mode", "debate", "compaction_threshold_tokens")}
    settings = RuntimeSettings.from_wire(old)
    assert settings.models == {"claude": None, "chatgpt": None}
    assert settings.prices == {}
    assert settings.fx == FxSettings()
    # Partial objects: missing agents and keys take their defaults too.
    partial = RuntimeSettings.from_wire(
        {"models": {"chatgpt": "gpt-6-sol"}, "fx": {"mode": "manual"}, "plans_eur": {}}
    )
    assert partial.models == {"claude": None, "chatgpt": "gpt-6-sol"}
    assert partial.fx == FxSettings(mode="manual", eur_per_usd=0.86)
    assert partial.plans_eur == {"claude": None, "chatgpt": None}


def test_model_ids_are_trimmed_and_empty_means_the_default() -> None:
    settings = RuntimeSettings.from_wire(
        {"models": {"claude": "  claude-opus-5[1m] ", "chatgpt": ""}, "fast_models": {}}
    )
    assert settings.models == {"claude": "claude-opus-5[1m]", "chatgpt": None}
    for model in ("anthropic/claude-sonnet-5@20260101", "gpt-6-sol:latest", "A" * 100):
        assert RuntimeSettings.from_wire({"models": {"claude": model}}).models["claude"] == model


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
        ({"refine": {"max_rounds": 1}}, "refine.max_rounds"),
        ({"refine": {"max_rounds": 51}}, "refine.max_rounds"),
        ({"refine": {"max_rounds": True}}, "refine.max_rounds"),
        ({"refine": {"max_rounds": 12.0}}, "refine.max_rounds"),
        ({"refine": {"budget_eur": 0.09}}, "refine.budget_eur"),
        ({"refine": {"budget_eur": 100.01}}, "refine.budget_eur"),
        ({"refine": {"budget_eur": "3"}}, "refine.budget_eur"),
        ({"refine": {"budget_eur": float("nan")}}, "refine.budget_eur"),
        ({"refine": {"budget_eur": 10**400}}, "refine.budget_eur"),
        ({"refine": {"budget_eur": None}}, "refine.budget_eur"),
        ({"refine": {"max_words": 99}}, "refine.max_words"),
        ({"refine": {"max_words": 20_001}}, "refine.max_words"),
        ({"refine": {"max_words": 500.0}}, "refine.max_words"),
        ({"refine": {"max_words": False}}, "refine.max_words"),
        ({"refine": {"max_words": "auto"}}, "refine.max_words"),
        ({"refine": {"stop_on_convergence": 1}}, "refine.stop_on_convergence"),
        ({"refine": {"stop_on_convergence": None}}, "refine.stop_on_convergence"),
        ({"refine": {"convergence_threshold": 49}}, "refine.convergence_threshold"),
        ({"refine": {"convergence_threshold": 101}}, "refine.convergence_threshold"),
        ({"refine": {"editor": "user"}}, "refine.editor"),
        ({"refine": []}, "refine"),
        ({"refine": None}, "refine"),
        ({"use_cache": 1}, "use_cache"),
        ({"compaction_threshold_tokens": 999}, "compaction_threshold_tokens"),
        ({"compaction_threshold_tokens": 100_001}, "compaction_threshold_tokens"),
        ({"compaction_threshold_tokens": "6000"}, "compaction_threshold_tokens"),
        ({"models": {"claude": "opus 5"}}, "models.claude"),
        ({"models": {"claude": "-opus"}}, "models.claude"),
        ({"models": {"claude": "x" * 101}}, "models.claude"),
        ({"models": {"claude": "op\nus"}}, "models.claude"),
        ({"models": {"claude": 5}}, "models.claude"),
        ({"fast_models": {"chatgpt": "gpt 6"}}, "fast_models.chatgpt"),
        ({"models": ["opus"]}, "models"),
        ({"prices": []}, "prices"),
        ({"prices": {"m": 3}}, "prices.m"),
        ({"prices": {"m": {**VALID["prices"]["my-new-model"], "input": 1e9}}}, "prices.m.input"),
        ({"fx": {"mode": "ecb"}}, "fx.mode"),
        ({"fx": {"eur_per_usd": 0.1}}, "fx.eur_per_usd"),
        ({"fx": {"eur_per_usd": 5.5}}, "fx.eur_per_usd"),
        ({"fx": {"eur_per_usd": "0.9"}}, "fx.eur_per_usd"),
        ({"fx": 0.9}, "fx"),
        ({"budgets_eur": {"claude": -1}}, "budgets_eur.claude"),
        ({"budgets_eur": {"claude": 100_001}}, "budgets_eur.claude"),
        ({"plans_eur": {"chatgpt": True}}, "plans_eur.chatgpt"),
        ({"plans_eur": {"chatgpt": float("inf")}}, "plans_eur.chatgpt"),
        ({"plans_eur": None}, "plans_eur"),
        ({"pdf_in_revisions": "document"}, "pdf_in_revisions"),
        ({"pdf_in_revisions": None}, "pdf_in_revisions"),
        ({"pdf_in_revisions": True}, "pdf_in_revisions"),
        ({"revision": -1}, "revision"),
        ({"revision": 1.0}, "revision"),
        ({"revision": "1"}, "revision"),
        ({"revision": False}, "revision"),
        ({"revision": None}, "revision"),
    ],
)
def test_invalid_values_raise_catalan_errors(patch: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(f"«{message}» ha de ser")) as exc_info:
        RuntimeSettings.from_wire({**VALID, **patch})
    assert "ha de ser" in str(exc_info.value)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"models": {"gemini": "x"}}, "«models» només admet les claus «claude» i «chatgpt»."),
        ({"prices": {"bad id": {}}}, "«prices»: «bad id» no és un identificador de model vàlid."),
        (
            {"prices": {"m": {"input": -1, "output": 1, "cache_read": 0, "cache_write": 0}}},
            "«prices.m»: Preu invàlid per a «input»: ha de ser un nombre ≥ 0.",
        ),
        ({"prices": {f"m{i}": VALID["prices"]["my-new-model"] for i in range(201)}}, "màxim 200"),
    ],
)
def test_other_invalid_values(patch: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        RuntimeSettings.from_wire({**VALID, **patch})


@pytest.mark.parametrize(
    "key", ["openai/", "x/[1m]", "a/-latest", "anthropic/anthropic.", "Z/@20260101"]
)
def test_a_price_key_that_names_no_model_is_refused(key: str) -> None:
    # Normalized to "", it would be a prefix of every model and reprice them all.
    price = VALID["prices"]["my-new-model"]
    with pytest.raises(ValueError, match=re.escape(f"«prices»: «{key}» no identifica cap model")):
        RuntimeSettings.from_wire({**VALID, "prices": {key: price}})
    with pytest.raises(ValueError, match="no identifica cap model"):
        RuntimeSettings(prices={key: ModelPrice(0, 0, 0, 0)})


def test_two_price_keys_of_the_same_model_are_refused() -> None:
    price = VALID["prices"]["my-new-model"]
    for first, second in (
        ("Claude-Opus-5", "claude-opus-5"),
        ("claude-opus-5", "anthropic/claude-opus-5-20260101"),
        ("gpt-7-nova", "openai/gpt-7-nova[1m]"),
    ):
        message = f"«prices»: «{first}» i «{second}» són el mateix model"
        with pytest.raises(ValueError, match=re.escape(message)):
            RuntimeSettings.from_wire({"prices": {first: price, second: price}})
    # Different models, even when one id prefixes the other, are fine.
    settings = RuntimeSettings.from_wire({"prices": {"claude-opus-5": price, "claude-opus": price}})
    assert set(settings.prices) == {"claude-opus-5", "claude-opus"}


@pytest.mark.parametrize(
    ("patch", "name"),
    [
        ({"budgets_eur": {"claude": 10**400}}, "budgets_eur.claude"),
        ({"plans_eur": {"chatgpt": -(10**400)}}, "plans_eur.chatgpt"),
        ({"fx": {"eur_per_usd": 10**400}}, "fx.eur_per_usd"),
    ],
)
def test_huge_integers_are_a_validation_error(patch: dict[str, object], name: str) -> None:
    # A valid JSON integer too large for a float: ValueError (422), not OverflowError.
    with pytest.raises(ValueError, match=re.escape(f"«{name}» ha de ser un nombre entre")):
        RuntimeSettings.from_wire({**VALID, **patch})
    huge_price = {"input": 10**400, "output": 1, "cache_read": 0, "cache_write": 0}
    with pytest.raises(ValueError, match=re.escape("«prices.m»: Preu invàlid per a «input»")):
        RuntimeSettings.from_wire({"prices": {"m": huge_price}})
    # Integers within the range are still accepted, and stored as floats.
    settings = RuntimeSettings.from_wire(
        {"budgets_eur": {"claude": 100_000}, "fx": {"eur_per_usd": 1}}
    )
    assert settings.budgets_eur["claude"] == 100_000.0
    assert settings.fx.eur_per_usd == 1.0


def test_limits_are_formatted_the_catalan_way() -> None:
    with pytest.raises(ValueError, match=re.escape("entre 0,2 i 5.")):
        RuntimeSettings.from_wire({"fx": {"eur_per_usd": 9}})
    with pytest.raises(ValueError, match=re.escape("entre 0 i 100.000.")):
        RuntimeSettings.from_wire({"budgets_eur": {"claude": -5}})


def test_non_object_is_rejected() -> None:
    with pytest.raises(ValueError, match="objecte JSON"):
        RuntimeSettings.from_wire(["debate"])


def test_direct_construction_is_validated() -> None:
    with pytest.raises(ValueError, match=r"debate\.consensus_threshold"):
        RuntimeSettings(debate=DebateOptions(consensus_threshold=10))
    with pytest.raises(ValueError, match=r"models\.claude"):
        RuntimeSettings(models={"claude": "no vàlid"})
    with pytest.raises(ValueError, match=r"fx\.eur_per_usd"):
        FxSettings(eur_per_usd=0)
    with pytest.raises(ValueError, match=r"prices\.m\.output"):
        RuntimeSettings(prices={"m": ModelPrice(1, float("nan"), 0, 0)})
    with pytest.raises(ValueError, match=r"budgets_eur\.chatgpt"):
        RuntimeSettings(budgets_eur={"chatgpt": -1.0})
    with pytest.raises(
        ValueError, match=re.escape("«pdf_in_revisions» ha de ser «full» o «text».")
    ):
        RuntimeSettings(pdf_in_revisions="pdf")  # type: ignore[arg-type]


def test_the_four_turn_modes() -> None:
    assert TURN_MODES == ("solo", "duel", "debate", "refine")
    with pytest.raises(
        ValueError,
        match=re.escape("«default_mode» ha de ser «solo», «duel», «debate» o «refine»."),
    ):
        RuntimeSettings.from_wire({"default_mode": "trio"})


def test_the_default_mode_is_never_refine() -> None:
    """A refine turn runs until the owner stops it: never chosen without asking."""
    message = "El mode per defecte no pot ser «refine»."
    with pytest.raises(ValueError, match=re.escape(message)):
        RuntimeSettings.from_wire({**VALID, "default_mode": "refine"})
    with pytest.raises(ValueError, match=re.escape(message)):
        RuntimeSettings(default_mode="refine")


def test_the_refine_options() -> None:
    assert RuntimeSettings().refine == RefineOptions()
    # Settings saved before the refine mode existed take its defaults.
    assert RuntimeSettings.from_wire({"default_mode": "solo"}).refine == RefineOptions()
    # Partial objects: the missing keys take their defaults too.
    partial = RuntimeSettings.from_wire({"refine": {"max_rounds": 20, "editor": "chatgpt"}})
    assert partial.refine == RefineOptions(max_rounds=20, editor="chatgpt")
    # No word limit (null): 1.2 times the first version's words, as the engine decides.
    assert RuntimeSettings.from_wire({"refine": {"max_words": None}}).refine.max_words is None
    for words in (100, 20_000):
        settings = RuntimeSettings.from_wire({"refine": {"max_words": words}})
        assert settings.refine.max_words == words
    # A budget in whole euros is stored as a float.
    budget = RuntimeSettings.from_wire({"refine": {"budget_eur": 100}}).refine.budget_eur
    assert budget == 100.0 and isinstance(budget, float)
    assert RuntimeSettings.from_wire({"refine": {"max_rounds": 2}}).refine.max_rounds == 2


@pytest.mark.parametrize(
    ("refine", "message"),
    [
        ({"max_rounds": 51}, "«refine.max_rounds» ha de ser un enter entre 2 i 50."),
        ({"budget_eur": 0}, "«refine.budget_eur» ha de ser un nombre entre 0,1 i 100."),
        (
            {"max_words": 50},
            "«refine.max_words» ha de ser null (automàtic) o un enter entre 100 i 20000.",
        ),
        (
            {"convergence_threshold": 30},
            "«refine.convergence_threshold» ha de ser un enter entre 50 i 100.",
        ),
        ({"stop_on_convergence": "sí"}, "«refine.stop_on_convergence» ha de ser un booleà"),
        ({"editor": "gemini"}, "«refine.editor» ha de ser «claude» o «chatgpt»."),
    ],
)
def test_the_refine_options_are_refused_in_catalan(refine: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        RuntimeSettings.from_wire({"refine": refine})
    with pytest.raises(ValueError, match=re.escape("«refine» ha de ser un objecte.")):
        RuntimeSettings.from_wire({"refine": "12 rondes"})


def test_direct_construction_validates_the_refine_options() -> None:
    with pytest.raises(ValueError, match=r"refine\.max_rounds"):
        RuntimeSettings(refine=RefineOptions(max_rounds=1))
    with pytest.raises(ValueError, match=r"refine\.budget_eur"):
        RuntimeSettings(refine=RefineOptions(budget_eur=250.0))
    with pytest.raises(ValueError, match=r"refine\.max_words"):
        RuntimeSettings(refine=RefineOptions(max_words=20))
    with pytest.raises(ValueError, match=r"refine\.convergence_threshold"):
        RuntimeSettings(refine=RefineOptions(convergence_threshold=101))
    with pytest.raises(ValueError, match=r"refine\.editor"):
        RuntimeSettings(refine=RefineOptions(editor="gemini"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=re.escape("«refine» ha de ser un objecte.")):
        RuntimeSettings(refine={"max_rounds": 3})  # type: ignore[arg-type]


def test_revisions_get_the_pdfs_text_by_default() -> None:
    assert RuntimeSettings().pdf_in_revisions == "text"
    assert RuntimeSettings.from_wire({}).pdf_in_revisions == "text"  # settings saved before
    assert RuntimeSettings.from_wire({"pdf_in_revisions": "full"}).pdf_in_revisions == "full"


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
ECB = StoredFxRate(
    rate=FxRate(eur_per_usd=0.8547, as_of=date(2026, 9, 25), source="ecb"),
    fetched_at=datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
)


def test_effective_fx_prefers_a_recent_ecb_rate_in_auto_mode() -> None:
    auto = RuntimeSettings(fx=FxSettings(mode="auto", eur_per_usd=0.9))
    assert effective_fx(auto, ECB, NOW) == ECB.rate
    manual = FxRate(eur_per_usd=0.9, as_of=None, source="manual")
    assert effective_fx(auto, None, NOW) == manual
    # Fetched more than 10 days ago: too old.
    assert effective_fx(auto, ECB, ECB.fetched_at + timedelta(days=10)) == ECB.rate
    assert effective_fx(auto, ECB, ECB.fetched_at + timedelta(days=10, seconds=1)) == manual
    # Manual mode always uses the owner's rate.
    assert effective_fx(RuntimeSettings(fx=FxSettings(mode="manual")), ECB, NOW) == FxRate(
        eur_per_usd=0.86, as_of=None, source="manual"
    )


def test_stored_fx_rate_json_roundtrip() -> None:
    data = ECB.to_json()
    assert data == {
        "eur_per_usd": 0.8547,
        "as_of": "2026-09-25",
        "fetched_at": "2026-09-25T16:00:00.000Z",
    }
    assert StoredFxRate.from_json(data) == ECB
    bad_values: list[object] = [
        [],
        {**data, "eur_per_usd": 9},
        {**data, "fetched_at": None},
        {**data, "as_of": 1},
    ]
    for bad in bad_values:
        with pytest.raises(ValueError):
            StoredFxRate.from_json(bad)


def test_timestamps_are_fixed_width_utc() -> None:
    aware = datetime(2026, 1, 2, 3, 4, 5, 678_901, tzinfo=timezone(timedelta(hours=2)))
    assert format_ts(aware) == "2026-01-02T01:04:05.678Z"
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert format_ts(naive) == "2026-01-02T03:04:05.000Z"
    parsed = parse_ts("2026-01-02T01:04:05.678Z")
    assert parsed == datetime(2026, 1, 2, 1, 4, 5, 678_000, tzinfo=UTC)
    assert parsed.tzinfo is UTC
