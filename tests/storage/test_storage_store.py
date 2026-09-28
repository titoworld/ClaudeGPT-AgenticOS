import dataclasses
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.domain import Usage
from agentic_os.fx import FxRate
from agentic_os.orchestrator.events import Savings, TurnFailure, TurnOutcome
from agentic_os.orchestrator.store import (
    CachedTurn,
    NewMessage,
    Store,
    UsageRecord,
)
from agentic_os.pricing import ModelPrice
from agentic_os.storage import (
    ConversationNotFoundError,
    DeviceRecord,
    FxSettings,
    RuntimeSettings,
    SessionRecord,
    SqliteStore,
    StoredFxRate,
)
from agentic_os.storage.models import parse_ts

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
async def store(tmp_path: Path, clock: FakeClock) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3", clock=clock) as store:
        yield store


async def test_sqlite_store_implements_the_engine_protocol(store: SqliteStore) -> None:
    engine_store: Store = store  # checked by mypy
    assert await engine_store.conversation_exists(1) is False


async def _question(
    store: SqliteStore, conversation_id: int, text: str = "Pregunta", mode: str = "debate"
) -> int:
    return await store.add_message(
        NewMessage(
            conversation_id=conversation_id,
            kind="question",
            content=text,
            final=True,
            meta={"mode": mode, "target": "claude"},
        )
    )


# --------------------------------------------------------------------------
# Engine contract
# --------------------------------------------------------------------------


async def test_question_turn_id_is_its_own_id_and_updates_conversation(
    store: SqliteStore, clock: FakeClock
) -> None:
    conversation_id = await store.create_conversation("  Una   conversa ")
    clock.advance(60)
    question_id = await _question(store, conversation_id, mode="duel")
    detail = await store.get_conversation(conversation_id)
    assert detail is not None
    assert detail.conversation.title == "Una conversa"
    assert detail.conversation.last_mode == "duel"
    assert detail.conversation.created_at == T0
    assert detail.conversation.updated_at == T0 + timedelta(seconds=60)
    assert detail.conversation.message_count == 1
    [message] = detail.messages
    assert message.id == question_id
    assert message.turn_id == question_id
    assert message.kind == "question"
    assert message.agent is None
    assert message.final is True
    assert message.meta == {"mode": "duel", "target": "claude"}
    assert message.created_at == T0 + timedelta(seconds=60)


async def test_empty_title_gets_default(store: SqliteStore) -> None:
    conversation_id = await store.create_conversation("   ")
    detail = await store.get_conversation(conversation_id)
    assert detail is not None
    assert detail.conversation.title == "Conversa nova"


async def test_add_message_validates_turns(store: SqliteStore) -> None:
    conversation_id = await store.create_conversation("A")
    other_id = await store.create_conversation("B")
    question_id = await _question(store, conversation_id)
    with pytest.raises(ValueError, match="turn_id must be None"):
        await store.add_message(
            NewMessage(conversation_id=conversation_id, kind="question", content="x", turn_id=1)
        )
    with pytest.raises(ValueError, match="needs the turn_id"):
        await store.add_message(
            NewMessage(conversation_id=conversation_id, kind="answer", content="x", agent="claude")
        )
    with pytest.raises(ValueError, match="is not a question"):
        await store.add_message(
            NewMessage(
                conversation_id=other_id,
                kind="answer",
                content="x",
                agent="claude",
                turn_id=question_id,
            )
        )
    with pytest.raises(ConversationNotFoundError):
        await store.add_message(NewMessage(conversation_id=999, kind="question", content="x"))
    # Nothing half-written after the failures.
    detail = await store.get_conversation(other_id)
    assert detail is not None and detail.messages == ()


async def test_the_turn_outcome_is_written_into_the_question_meta(
    store: SqliteStore, clock: FakeClock
) -> None:
    conversation_id = await store.create_conversation("Resultat")
    question_id = await store.add_message(
        NewMessage(
            conversation_id=conversation_id,
            kind="question",
            content="Pregunta",
            final=True,
            meta={"mode": "duel", "target": "claude", "outcome": None},
        )
    )
    answer_id = await store.add_message(
        NewMessage(
            conversation_id=conversation_id,
            kind="answer",
            content="A",
            turn_id=question_id,
            agent="chatgpt",
            final=True,
            meta={"usage": {"input_tokens": 1}},
        )
    )
    detail = await store.get_conversation(conversation_id)
    assert detail is not None and detail.messages[0].meta["outcome"] is None
    updated_at = detail.conversation.updated_at

    outcome = TurnOutcome(
        status="cancelled",
        usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.25),
        savings=Savings(cost_usd=0.0),
        failures=(TurnFailure("claude", "invalid", "Claude ha declinat «això».", 0),),
        final_message_ids=(answer_id,),
    )
    clock.advance(60)
    await store.set_turn_outcome(question_id, outcome)
    # Only a question of a turn takes it; an unknown id (a deleted conversation) is ignored.
    await store.set_turn_outcome(answer_id, outcome)
    await store.set_turn_outcome(question_id + 999, outcome)

    detail = await store.get_conversation(conversation_id)
    assert detail is not None
    question, answer = detail.messages
    # A JSON object (json_set with json(?)), not a string, next to the other keys.
    assert question.meta == {"mode": "duel", "target": "claude", "outcome": outcome.to_wire()}
    assert answer.meta == {"usage": {"input_tokens": 1}}
    assert detail.conversation.updated_at == updated_at
    async with store._db.transaction(write=False) as tx:
        row = await tx.fetchone(
            "SELECT json_type(meta, '$.outcome') AS kind, "
            "json_extract(meta, '$.outcome.usage.cost_usd') AS cost FROM messages WHERE id = ?",
            (question_id,),
        )
    assert row is not None and (row["kind"], row["cost"]) == ("object", 0.25)


async def test_history_contains_only_final_messages_after_the_summary(
    store: SqliteStore,
) -> None:
    conversation_id = await store.create_conversation("Debat")
    history = await store.get_history(conversation_id)
    assert history.summary is None
    assert history.summary_upto_id == 0
    assert history.messages == ()

    q1 = await _question(store, conversation_id, "Q1")
    for agent in ("claude", "chatgpt"):
        await store.add_message(
            NewMessage(
                conversation_id=conversation_id,
                kind="answer",
                content=f"answer {agent}",
                turn_id=q1,
                agent=agent,
            )
        )
    await store.add_message(
        NewMessage(
            conversation_id=conversation_id,
            kind="revision",
            content="rev",
            turn_id=q1,
            agent="claude",
            round=1,
            meta={"agreement": 90, "critique": "ok", "unchanged": False},
        )
    )
    s1 = await store.add_message(
        NewMessage(
            conversation_id=conversation_id,
            kind="synthesis",
            content="S1",
            turn_id=q1,
            agent="claude",
            round=1,
            final=True,
        )
    )
    q2 = await _question(store, conversation_id, "Q2", mode="solo")
    a2 = await store.add_message(
        NewMessage(
            conversation_id=conversation_id,
            kind="answer",
            content="A2",
            turn_id=q2,
            agent="chatgpt",
            final=True,
        )
    )

    history = await store.get_history(conversation_id)
    assert [m.id for m in history.messages] == [q1, s1, q2, a2]
    assert [m.content for m in history.messages] == ["Q1", "S1", "Q2", "A2"]
    assert all(m.final for m in history.messages)

    await store.set_summary(conversation_id, "Resum de Q1", s1)
    history = await store.get_history(conversation_id)
    assert history.summary == "Resum de Q1"
    assert history.summary_upto_id == s1
    assert [m.id for m in history.messages] == [q2, a2]

    detail = await store.get_conversation(conversation_id)
    assert detail is not None
    assert detail.summary == "Resum de Q1"
    assert detail.conversation.message_count == 7
    assert [m.id for m in detail.messages] == sorted(m.id for m in detail.messages)
    assert detail.conversation.last_mode == "solo"


async def test_history_and_summary_of_missing_conversation(store: SqliteStore) -> None:
    assert not await store.conversation_exists(42)
    with pytest.raises(ConversationNotFoundError, match="no existeix"):
        await store.get_history(42)
    with pytest.raises(ConversationNotFoundError):
        await store.set_summary(42, "x", 1)


# --------------------------------------------------------------------------
# Conversations for the web layer
# --------------------------------------------------------------------------


async def test_list_conversations_newest_activity_first_with_pagination(
    store: SqliteStore, clock: FakeClock
) -> None:
    ids = []
    for index in range(5):
        clock.advance(1)
        ids.append(await store.create_conversation(f"C{index}"))
    clock.advance(1)
    await _question(store, ids[1])  # C1 becomes the most recent

    listed = await store.list_conversations(limit=50)
    assert [c.title for c in listed] == ["C1", "C4", "C3", "C2", "C0"]
    assert listed[0].message_count == 1
    assert listed[1].message_count == 0

    first_page = await store.list_conversations(limit=2)
    assert [c.title for c in first_page] == ["C1", "C4"]
    second_page = await store.list_conversations(limit=2, before=first_page[-1].id)
    assert [c.title for c in second_page] == ["C3", "C2"]
    third_page = await store.list_conversations(limit=2, before=second_page[-1].id)
    assert [c.title for c in third_page] == ["C0"]
    assert await store.list_conversations(before=9999) == []
    with pytest.raises(ValueError):
        await store.list_conversations(limit=0)


async def test_same_timestamp_conversations_are_paginated_by_id(store: SqliteStore) -> None:
    ids = [await store.create_conversation(f"C{i}") for i in range(3)]
    page = await store.list_conversations(limit=2)
    assert [c.id for c in page] == [ids[2], ids[1]]
    rest = await store.list_conversations(limit=2, before=page[-1].id)
    assert [c.id for c in rest] == [ids[0]]


async def test_rename_conversation(store: SqliteStore, clock: FakeClock) -> None:
    conversation_id = await store.create_conversation("Vell")
    clock.advance(30)
    renamed = await store.rename_conversation(conversation_id, "  Nou  títol ")
    assert renamed is not None
    assert renamed.title == "Nou títol"
    assert renamed.updated_at == T0  # renaming is not activity
    assert await store.rename_conversation(999, "x") is None
    with pytest.raises(ValueError, match="buit"):
        await store.rename_conversation(conversation_id, "   ")
    with pytest.raises(ValueError, match="200"):
        await store.rename_conversation(conversation_id, "x" * 201)


async def test_delete_cascades_messages_and_cache_but_keeps_usage(
    store: SqliteStore, clock: FakeClock
) -> None:
    conversation_id = await store.create_conversation("Esborrar")
    keep_id = await store.create_conversation("Mantenir")
    question_id = await _question(store, conversation_id)
    answer = NewMessage(
        conversation_id=conversation_id,
        kind="answer",
        content="A",
        turn_id=question_id,
        agent="claude",
        final=True,
    )
    await store.add_message(answer)
    await store.record_usage(
        UsageRecord(
            conversation_id=conversation_id,
            turn_id=question_id,
            agent="claude",
            provider_mode="fake",
            model="fake",
            purpose="answer",
            usage=Usage(input_tokens=10, output_tokens=5),
            latency_ms=100,
            ttft_ms=10,
            ok=True,
        )
    )
    expires = T0 + timedelta(days=7)
    await store.cache_put(
        "k-delete", CachedTurn(mode="solo", messages=(answer,), tokens=15), expires
    )
    keep_answer = NewMessage(conversation_id=keep_id, kind="answer", content="B", turn_id=1)
    await store.cache_put(
        "k-keep", CachedTurn(mode="solo", messages=(keep_answer,), tokens=1), expires
    )

    assert await store.delete_conversation(conversation_id) is True
    assert await store.delete_conversation(conversation_id) is False
    assert await store.get_conversation(conversation_id) is None
    assert not await store.conversation_exists(conversation_id)
    assert await store.cache_get("k-delete", T0) is None
    assert await store.cache_get("k-keep", T0) is not None
    stats = await store.stats(1, T0)
    assert stats["totals"]["calls"] == 1

    # Messages are really gone (ON DELETE CASCADE), not just hidden.
    new_id = await store.create_conversation("Nova")
    assert new_id > keep_id  # ids are never reused
    detail = await store.get_conversation(new_id)
    assert detail is not None and detail.messages == ()


# --------------------------------------------------------------------------
# Turn cache
# --------------------------------------------------------------------------


async def test_cache_roundtrip_expiry_and_purge(store: SqliteStore) -> None:
    messages = (
        NewMessage(
            conversation_id=1,
            kind="answer",
            content="Hola",
            turn_id=7,
            agent="claude",
            final=True,
            meta={"model": "m", "usage": {"input_tokens": 1}, "cached": False},
        ),
        NewMessage(
            conversation_id=1,
            kind="revision",
            content="Rev",
            turn_id=7,
            agent="chatgpt",
            round=1,
            meta={"agreement": None},
        ),
    )
    value = CachedTurn(mode="debate", messages=messages, tokens=1234)
    await store.cache_put("key", value, T0 + timedelta(hours=1))

    cached = await store.cache_get("key", T0 + timedelta(minutes=59))
    assert cached is not None
    assert cached.mode == "debate"
    assert cached.tokens == 1234
    assert tuple(cached.messages) == messages

    assert await store.cache_get("key", T0 + timedelta(hours=1)) is None
    assert await store.cache_get("missing", T0) is None

    replacement = CachedTurn(mode="solo", messages=(), tokens=1)
    await store.cache_put("key", replacement, T0 + timedelta(hours=2))
    cached = await store.cache_get("key", T0 + timedelta(hours=1))
    assert cached is not None and cached.mode == "solo"

    await store.cache_put("old", replacement, T0)
    assert await store.purge_expired_cache(T0 + timedelta(hours=1)) == 1
    assert await store.purge_expired_cache(T0 + timedelta(hours=3)) == 1


async def test_undecodable_cache_entry_is_a_miss(store: SqliteStore) -> None:
    await store.cache_put("key", CachedTurn(mode="solo", messages=(), tokens=1), T0 + timedelta(1))
    async with store._db.transaction() as tx:
        await tx.execute("UPDATE turn_cache SET value = '{\"format\": 99}'")
    assert await store.cache_get("key", T0) is None
    assert await store.purge_expired_cache(T0 + timedelta(days=30)) == 0  # already deleted


# --------------------------------------------------------------------------
# Runtime settings
# --------------------------------------------------------------------------


async def test_runtime_settings_default_and_roundtrip(store: SqliteStore) -> None:
    assert await store.get_runtime_settings() == RuntimeSettings()
    custom = RuntimeSettings.from_wire(
        {
            "default_mode": "solo",
            "default_target": "chatgpt",
            "debate": {"rounds": 4, "consensus_threshold": 50, "synthesizer": "chatgpt"},
            "use_cache": False,
            "compaction_threshold_tokens": 1000,
        }
    )
    stored = await store.put_runtime_settings(custom)
    assert stored == dataclasses.replace(custom, revision=1)
    assert await store.get_runtime_settings() == stored


async def test_invalid_stored_runtime_settings_fall_back_to_defaults(store: SqliteStore) -> None:
    async with store._db.transaction() as tx:
        await tx.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime', '{\"default_mode\": \"x\"}')"
        )
    # Saved settings are at revision 1 at least, also when they load as the defaults.
    assert await store.get_runtime_settings() == RuntimeSettings(revision=1)


async def test_stored_prices_refused_by_the_new_rules_are_dropped_not_the_settings(
    store: SqliteStore,
) -> None:
    # Saved by an older version, which accepted a key naming no model and two keys of
    # one model (of which only the last one was applied).
    price = {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.25}
    applied = {"input": 3.0, "output": 4.0, "cache_read": 0.3, "cache_write": 3.75}
    old = {
        "default_mode": "solo",
        "budgets_eur": {"claude": 30.0, "chatgpt": None},
        "prices": {"Claude-Opus-5": price, "claude-opus-5": applied, "openai/": price, "m": price},
    }
    async with store._db.transaction() as tx:
        await tx.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime', ?)", (json.dumps(old),)
        )
    settings = await store.get_runtime_settings()
    assert settings.default_mode == "solo"
    assert settings.budgets_eur["claude"] == 30.0
    assert settings.prices == {
        "claude-opus-5": ModelPrice.from_wire(applied),
        "m": ModelPrice.from_wire(price),
    }


async def test_models_prices_and_money_settings_roundtrip(store: SqliteStore) -> None:
    custom = RuntimeSettings(
        models={"claude": "claude-opus-5", "chatgpt": None},
        fast_models={"claude": None, "chatgpt": "gpt-6-luna"},
        prices={"gpt-7-nova": ModelPrice(3.0, 12.0, 0.3, 0.0)},
        fx=FxSettings(mode="manual", eur_per_usd=0.92),
        budgets_eur={"claude": 20.0, "chatgpt": None},
        plans_eur={"claude": 90.0, "chatgpt": 23.0},
    )
    stored = await store.put_runtime_settings(custom)
    assert stored == dataclasses.replace(custom, revision=1)
    assert await store.get_runtime_settings() == stored


async def test_settings_stored_by_the_previous_version_get_the_new_defaults(
    store: SqliteStore,
) -> None:
    old = '{"default_mode": "solo", "default_target": "chatgpt", "use_cache": false}'
    async with store._db.transaction() as tx:
        await tx.execute("INSERT INTO settings (key, value) VALUES ('runtime', ?)", (old,))
    settings = await store.get_runtime_settings()
    assert settings == RuntimeSettings(
        default_mode="solo", default_target="chatgpt", use_cache=False, revision=1
    )
    assert settings.fx == FxSettings(mode="auto", eur_per_usd=0.86)


# --------------------------------------------------------------------------
# Exchange rate
# --------------------------------------------------------------------------

ECB = FxRate(eur_per_usd=0.8547, as_of=date(2026, 9, 25), source="ecb")


async def test_ecb_rate_storage_and_current_fx(store: SqliteStore) -> None:
    manual = FxRate(eur_per_usd=0.86, as_of=None, source="manual")
    assert await store.get_ecb_rate() is None
    assert await store.current_fx(T0) == manual

    await store.put_ecb_rate(ECB, T0)
    assert await store.get_ecb_rate() == StoredFxRate(rate=ECB, fetched_at=T0)
    assert await store.current_fx(T0 + timedelta(days=3)) == ECB
    # Too old: back to the manual rate (the owner's one).
    await store.put_runtime_settings(RuntimeSettings(fx=FxSettings(eur_per_usd=0.9)))
    assert await store.current_fx(T0 + timedelta(days=11)) == FxRate(0.9, None, "manual")
    # Manual mode ignores the ECB rate.
    await store.put_runtime_settings(RuntimeSettings(fx=FxSettings(mode="manual")))
    assert await store.current_fx(T0) == manual

    later = FxRate(eur_per_usd=0.85, as_of=date(2026, 9, 26), source="ecb")
    await store.put_ecb_rate(later, T0 + timedelta(days=1))
    assert await store.get_ecb_rate() == StoredFxRate(later, T0 + timedelta(days=1))


async def test_an_invalid_stored_ecb_rate_is_ignored(store: SqliteStore) -> None:
    async with store._db.transaction() as tx:
        await tx.execute("INSERT INTO settings (key, value) VALUES ('fx_ecb', '{\"x\": 1}')")
    assert await store.get_ecb_rate() is None
    assert (await store.current_fx(T0)).source == "manual"


# --------------------------------------------------------------------------
# Owner, sessions and throttle persistence
# --------------------------------------------------------------------------


async def test_owner_lifecycle(store: SqliteStore, clock: FakeClock) -> None:
    assert await store.get_owner() is None
    assert await store.consume_totp_step(10) is False  # no owner yet

    await store.create_session(
        "h1", created_at=T0, expires_at=T0 + timedelta(days=1), ip="1.2.3.4", user_agent="UA"
    )
    revoked = await store.set_owner(password_hash="hash1", totp_secret="SECRET1", totp_last_step=5)
    assert revoked == 1
    owner = await store.get_owner()
    assert owner is not None
    assert (owner.password_hash, owner.totp_secret, owner.totp_last_step) == ("hash1", "SECRET1", 5)
    assert "hash1" not in repr(owner) and "SECRET1" not in repr(owner)

    assert await store.consume_totp_step(5) is False  # replay
    assert await store.consume_totp_step(4) is False
    assert await store.consume_totp_step(6) is True
    assert await store.consume_totp_step(6) is False

    clock.advance(10)
    session = SessionRecord("s1", T0, T0, T0 + timedelta(days=1), None, None)
    device = DeviceRecord("d1", T0, T0 + timedelta(days=365))
    assert await store.complete_login(
        owner, step=7, session=session, device=device, password_hash="hash2"
    )
    owner = await store.get_owner()
    assert owner is not None
    assert owner.password_hash == "hash2"
    assert owner.totp_last_step == 7
    assert owner.updated_at == T0 + timedelta(seconds=10)

    # The session of that login is revoked.
    assert await store.set_owner(password_hash="h3", totp_secret="S3", totp_last_step=0) == 1
    owner = await store.get_owner()
    assert owner is not None
    assert (owner.password_hash, owner.totp_last_step, owner.created_at) == ("h3", 0, T0)


async def test_session_records(store: SqliteStore) -> None:
    await store.create_session(
        "h1",
        created_at=T0,
        expires_at=T0 + timedelta(days=30),
        ip="203.0.113.9",
        user_agent="x" * 1000,
    )
    record = await store.get_session("h1")
    assert record is not None
    assert record.created_at == record.last_seen_at == T0
    assert record.expires_at == T0 + timedelta(days=30)
    assert record.ip == "203.0.113.9"
    assert record.user_agent is not None and len(record.user_agent) == 256

    await store.touch_session("h1", T0 + timedelta(hours=1))
    record = await store.get_session("h1")
    assert record is not None and record.last_seen_at == T0 + timedelta(hours=1)

    await store.create_session(
        "h2", created_at=T0, expires_at=T0 + timedelta(days=30), ip=None, user_agent=None
    )
    await store.create_session(
        "h3", created_at=T0, expires_at=T0 + timedelta(hours=2), ip=None, user_agent=None
    )
    # At T0 + 3h with a 2h idle timeout: h2 idle (last seen T0), h3 expired, h1 alive.
    purged = await store.purge_expired_sessions(T0 + timedelta(hours=3), timedelta(hours=2.5))
    assert purged == 2
    assert await store.get_session("h1") is not None
    assert await store.delete_session("h1") is True
    assert await store.delete_session("h1") is False
    assert await store.delete_all_sessions() == 0


async def test_device_records(store: SqliteStore) -> None:
    await store.create_device("d1", created_at=T0, expires_at=T0 + timedelta(days=365))
    await store.create_device("d2", created_at=T0, expires_at=T0 + timedelta(days=1))
    record = await store.get_device("d1")
    assert record is not None
    assert (record.token_hash, record.created_at) == ("d1", T0)
    assert record.expires_at == T0 + timedelta(days=365)
    assert await store.get_device("missing") is None

    assert await store.purge_expired_devices(T0 + timedelta(days=2)) == 1
    assert await store.get_device("d2") is None
    assert await store.delete_device("d1") is True
    assert await store.delete_device("d1") is False
    await store.create_device("d3", created_at=T0, expires_at=T0 + timedelta(days=1))
    assert await store.delete_all_devices() == 1


async def test_set_owner_forgets_devices_and_clears_the_throttle(store: SqliteStore) -> None:
    def lock_for(failures: int) -> timedelta:
        return timedelta(minutes=15)

    await store.create_device("d1", created_at=T0, expires_at=T0 + timedelta(days=365))
    for key in ("global", "ip:203.0.113.9"):
        await store.record_throttle_failure(
            key, T0, reset_after=timedelta(hours=24), lock_for=lock_for
        )
    await store.set_owner(password_hash="h", totp_secret="S", totp_last_step=0)
    assert await store.get_device("d1") is None
    assert await store.get_throttle("global") is None
    assert await store.get_throttle("ip:203.0.113.9") is None


async def test_clear_throttle(store: SqliteStore) -> None:
    def lock_for(failures: int) -> timedelta:
        return timedelta(minutes=15)

    assert await store.clear_throttle() == 0
    for key in ("global", "ip:203.0.113.9", "device:abc"):
        await store.record_throttle_failure(
            key, T0, reset_after=timedelta(hours=24), lock_for=lock_for
        )
    assert await store.clear_throttle() == 3
    assert await store.get_throttle("global") is None


async def test_throttle_records(store: SqliteStore) -> None:
    def lock_for(failures: int) -> timedelta:
        return timedelta(seconds=10) if failures >= 2 else timedelta(0)

    reset = timedelta(hours=1)
    state = await store.record_throttle_failure("ip:a", T0, reset_after=reset, lock_for=lock_for)
    assert (state.failures, state.locked_until) == (1, None)
    state = await store.record_throttle_failure("ip:a", T0, reset_after=reset, lock_for=lock_for)
    assert state.failures == 2
    assert state.locked_until == T0 + timedelta(seconds=10)
    assert await store.get_throttle("ip:a") == state

    later = T0 + timedelta(hours=2)
    state = await store.record_throttle_failure("ip:a", later, reset_after=reset, lock_for=lock_for)
    assert state.failures == 1  # counter restarted after reset_after

    await store.record_throttle_failure("ip:b", T0, reset_after=reset, lock_for=lock_for)
    await store.reset_throttle("ip:b", "missing")
    assert await store.get_throttle("ip:b") is None

    assert await store.purge_throttle(later + timedelta(minutes=30), idle=reset) == 0
    assert await store.purge_throttle(later + timedelta(hours=1), idle=reset) == 1
    assert await store.get_throttle("ip:a") is None


async def test_timestamps_are_stored_as_utc_iso_strings(store: SqliteStore) -> None:
    conversation_id = await store.create_conversation("x")
    async with store._db.transaction(write=False) as tx:
        row = await tx.fetchone(
            "SELECT created_at FROM conversations WHERE id = ?", (conversation_id,)
        )
    assert row is not None
    assert row[0] == "2026-09-27T12:00:00.000Z"
    assert parse_ts(row[0]) == T0
