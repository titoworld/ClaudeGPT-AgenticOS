"""Turn cache: an identical question (same mode, agents, options and context) is
answered by replaying the stored messages instead of calling any model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace

from agentic_os import i18n
from agentic_os.domain import AgentName, TurnMode, TurnOptions, Usage
from agentic_os.orchestrator.memory import TurnContext
from agentic_os.orchestrator.store import JsonValue, NewMessage
from agentic_os.pdf_facts import CHECK_VERSION
from agentic_os.pricing import ModelPrice, estimate_cost_usd
from agentic_os.providers.base import Attachment, AttachmentMode
from agentic_os.providers.prompt_format import has_text

CACHE_KEY_VERSION = 8
"""Bump when prompts, the replay format or the key itself change, to invalidate old
entries (2: the question keeps its inner whitespace; 3: earlier entries may hold replies
that were cut off, duplicated by a Codex retry or mangled by the revision parser, which
are no longer stored as complete or cached; 4: earlier entries count their tokens as
input + output only and hide the attempts declined before a fallback in the served
message's usage, so a hit would report a saving with the old token count and value
the declined tokens at the serving model's rates; 5: the key includes the attachments
and the system prompt says how to treat them; 6: a file's text is neutralized and
enclosed, and a PDF without text goes whole to the revisions; 7: ChatGPT with the
subscription reads a PDF's text as Claude checked it, the prompts warn of hidden text and
say which pages ChatGPT read through Claude, and the key has the check's version and what
the server's reader made of each PDF; 8: every text for the models is English, and the key
has the language of the turn, whose texts for people a hit replays: the demo's answers and
why a PDF's pages were left unchecked, ADR 0011)."""


def _digest(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def normalize_question(text: str) -> str:
    """Only outer whitespace and line endings (CRLF/CR become LF) are normalized: inner
    newlines and indentation carry meaning (code, lists, tables), so two questions that
    differ there must not share an answer."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def context_fingerprint(context: TurnContext) -> str:
    """Hash of the compacted summary and the ids and contents of the context messages."""
    return _digest(
        {
            "summary": context.summary,
            "messages": [[m.id, m.content] for m in context.messages],
        }
    )


def _attachment_key(attachment: Attachment) -> tuple[object, ...]:
    """An attachment in the key: its content and name (the prompts name it) and, for a
    PDF, what the server's reader made of it, which the models get: whether it has any
    text (ChatGPT with the subscription reads it; the revisions get a PDF without any
    whole) and the warnings of its pages (every model's label, ChatGPT's view and Claude's
    check), None when it was not analysed. Two uploads of the same file may differ there:
    one from before the page analysis existed, or whose reading timed out."""
    if attachment.kind != "pdf":
        return (attachment.sha256, attachment.name)
    notes = attachment.pdf_notes
    return (
        attachment.sha256,
        attachment.name,
        has_text(attachment),
        notes.to_wire() if notes is not None else None,
    )


def turn_cache_key(
    *,
    mode: TurnMode,
    target: AgentName,
    options: TurnOptions,
    question: str,
    context_fingerprint: str,
    identities: Mapping[AgentName, str],
    attachments: Sequence[Attachment] = (),
    pdf_in_revisions: AttachmentMode = "text",
) -> str:
    """Cache key of a turn.

    ``identities`` maps each agent taking part to its provider identity
    (``"<mode>:<model>"``, with the model requested for this turn). The attachments
    count by content and name (the prompts name them), in order, a PDF also by what the
    server's reader made of it (:func:`_attachment_key`), and with a PDF the version of
    Claude's check of its text (``pdf_facts.CHECK_VERSION``: a ChatGPT that cannot open
    PDFs reads what it found). The solo target, the debate options and
    ``pdf_in_revisions`` (how the revisions get the PDFs that have text: one without any
    goes whole) only count where they change what the models get, so irrelevant
    differences do not cause misses. The language in force (:func:`i18n.current`, the
    turn's) counts too: a hit replays the texts the stored turn wrote for people in its
    own language (the demo's answers, why a PDF's pages were left unchecked).
    """
    debate = options.debate
    revises_pdf = (
        mode == "debate"
        and debate.rounds > 0
        and any(attachment.kind == "pdf" and has_text(attachment) for attachment in attachments)
    )
    return _digest(
        {
            "v": CACHE_KEY_VERSION,
            "mode": mode,
            "target": target if mode == "solo" else None,
            "debate": (
                {
                    "rounds": debate.rounds,
                    "consensus_threshold": debate.consensus_threshold,
                    "synthesizer": debate.synthesizer,
                }
                if mode == "debate"
                else None
            ),
            "question": normalize_question(question),
            "attachments": [_attachment_key(attachment) for attachment in attachments],
            "pdf_check": (
                CHECK_VERSION
                if any(attachment.kind == "pdf" for attachment in attachments)
                else None
            ),
            "pdf_in_revisions": pdf_in_revisions if revises_pdf else None,
            "context": context_fingerprint,
            "providers": {agent: identities[agent] for agent in sorted(identities)},
            "lang": i18n.current(),
        }
    )


def replayed_message(message: NewMessage, *, conversation_id: int, turn_id: int) -> NewMessage:
    """A cached message moved to a new turn and flagged as ``cached``: a replay spends
    nothing, so its usage is zero (no cost basis) and the original turn's savings,
    unstored usage (its failed calls) and declined attempts go."""
    meta: dict[str, JsonValue] = dict(message.meta)
    meta["cached"] = True
    meta["usage"] = dict(Usage().to_dict())
    meta.pop("cost_basis", None)
    meta.pop("savings", None)
    meta.pop("unstored_usage", None)
    meta.pop("declined", None)
    return replace(message, conversation_id=conversation_id, turn_id=turn_id, meta=meta)


def _int(value: JsonValue) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _tokens(usage: JsonValue) -> Usage | None:
    """The token counts of a stored ``Usage`` (its cost is recomputed), None if invalid."""
    if not isinstance(usage, dict):
        return None
    return Usage(
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_tokens")),
        cache_write_tokens=_int(usage.get("cache_write_tokens")),
    )


def _replayed_calls(messages: Sequence[NewMessage]) -> list[tuple[JsonValue, Usage]]:
    """(model, tokens) of every billed call a replay of ``messages`` avoids: each
    message's own call and the attempts other models declined before it (``declined``
    in its meta: a server-side fallback), each with its own model."""
    calls: list[tuple[JsonValue, Usage]] = []
    for message in messages:
        declined = message.meta.get("declined")
        for attempt in declined if isinstance(declined, list) else []:
            if not isinstance(attempt, dict):
                continue
            if (tokens := _tokens(attempt.get("usage"))) is not None:
                calls.append((attempt.get("model"), tokens))
        if (tokens := _tokens(message.meta.get("usage"))) is not None:
            calls.append((message.meta.get("model"), tokens))
    return calls


def replay_tokens(messages: Sequence[NewMessage]) -> int:
    """Processed tokens of the calls a replay of ``messages`` avoids (``CachedTurn.tokens``):
    the same calls :func:`replay_cost_usd` values, so the saving and its value match."""
    return sum(tokens.processed_tokens for _model, tokens in _replayed_calls(messages))


def replay_cost_usd(
    messages: Sequence[NewMessage], price_overrides: Mapping[str, ModelPrice] | None = None
) -> float | None:
    """What the cached turn's calls cost, at current prices (None if none is priced):
    the whole original turn, every attempt at the rates of the model that ran it (an
    attempt declined before a fallback at the declining model's, never the served one's)."""
    total: float | None = None
    for model, tokens in _replayed_calls(messages):
        if not isinstance(model, str):
            continue
        cost = estimate_cost_usd(model, tokens, price_overrides)
        if cost is not None:
            total = (total or 0.0) + cost
    return total
