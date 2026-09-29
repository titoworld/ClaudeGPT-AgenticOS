"""Records returned by :class:`agentic_os.storage.SqliteStore` and the runtime settings.

Timestamps are timezone-aware UTC datetimes in Python and UTC ISO 8601 strings with
millisecond precision and a ``Z`` suffix in the database and on the wire (fixed
width, so they sort lexicographically).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal, TypeGuard

from agentic_os.attachments import attachment_wire
from agentic_os.domain import AGENTS, AgentName, DebateOptions, TurnMode, TurnOptions
from agentic_os.fx import DEFAULT_EUR_PER_USD, FxRate, manual_rate
from agentic_os.orchestrator.store import JsonValue, StoredMessage
from agentic_os.pdf_facts import PdfNotes
from agentic_os.pricing import ModelPrice, normalize_model
from agentic_os.providers.base import MODEL_ID_PATTERN, AttachmentKind, AttachmentMode

TURN_MODES: Final[tuple[TurnMode, ...]] = ("solo", "duel", "debate")

ROUNDS_RANGE: Final = (0, 4)
CONSENSUS_THRESHOLD_RANGE: Final = (50, 100)
COMPACTION_THRESHOLD_RANGE: Final = (1_000, 100_000)
EUR_PER_USD_RANGE: Final = (0.2, 5.0)
AMOUNT_EUR_RANGE: Final = (0.0, 100_000.0)
"""Monthly budgets and plan prices, in euros."""
MAX_PRICE_PER_MTOK: Final = 100_000.0
MAX_CUSTOM_PRICES: Final = 200
FX_MAX_AGE: Final = timedelta(days=10)
"""An ECB rate fetched longer ago than this is not used (the manual rate is)."""

FxMode = Literal["auto", "manual"]
FX_MODES: Final[tuple[FxMode, ...]] = ("auto", "manual")
PDF_IN_REVISIONS: Final[tuple[AttachmentMode, ...]] = ("full", "text")
ATTACHMENT_KINDS: Final[tuple[AttachmentKind, ...]] = ("image", "pdf", "text")

_MODEL_ID: Final = re.compile(MODEL_ID_PATTERN)

Wire = dict[str, JsonValue]


def utc_now() -> datetime:
    """Current time as an aware UTC datetime (the default clock)."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Convert to aware UTC; naive datetimes are taken to be UTC already."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_ts(value: datetime) -> str:
    """Format as ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (UTC; naive input is taken as UTC)."""
    return as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    """Parse a timestamp written by :func:`format_ts` into an aware UTC datetime."""
    return as_utc(datetime.fromisoformat(value))


# --------------------------------------------------------------------------
# Runtime settings (changed by the owner from the dashboard)
# --------------------------------------------------------------------------


def _int_in_range(value: object, name: str, bounds: tuple[int, int]) -> int:
    low, high = bounds
    # bool is a subclass of int: reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"«{name}» ha de ser un enter entre {low} i {high}.")
    return value


def _mode(value: object, name: str) -> TurnMode:
    for mode in TURN_MODES:
        if value == mode:
            return mode
    raise ValueError(f"«{name}» ha de ser «solo», «duel» o «debate».")


def _agent(value: object, name: str) -> AgentName:
    for agent in AGENTS:
        if value == agent:
            return agent
    raise ValueError(f"«{name}» ha de ser «claude» o «chatgpt».")


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"«{name}» ha de ser un booleà (true o false).")
    return value


def _pdf_in_revisions(value: object) -> AttachmentMode:
    for mode in PDF_IN_REVISIONS:
        if value == mode:
            return mode
    raise ValueError("«pdf_in_revisions» ha de ser «full» o «text».")


def is_revision(value: object) -> TypeGuard[int]:
    """A revision of the settings: an integer from 0 (a ``bool`` is not one)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _revision(value: object) -> int:
    if is_revision(value):
        return value
    raise ValueError("«revision» ha de ser un enter igual o més gran que 0.")


def _format_number(value: float) -> str:
    """Catalan notation for error messages (``0,2``, ``100.000``)."""
    if value == int(value):
        return f"{int(value):,}".replace(",", ".")
    return f"{value:g}".replace(".", ",")


def _number_in_range(value: object, name: str, bounds: tuple[float, float]) -> float:
    low, high = bounds
    # An int is compared exactly (no float conversion, which overflows on the huge
    # integers JSON allows); a float must also be finite.
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or (isinstance(value, float) and not math.isfinite(value))
        or not low <= value <= high
    ):
        raise ValueError(
            f"«{name}» ha de ser un nombre entre {_format_number(low)} i {_format_number(high)}."
        )
    return float(value)


def model_id(value: object, name: str) -> str:
    """A model id typed by the owner (any id of :data:`MODEL_ID_PATTERN`)."""
    if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
        raise ValueError(
            f"«{name}» ha de ser un identificador de model vàlid: fins a 100 lletres, xifres "
            "o els signes . _ : / @ [ ] -, sense espais."
        )
    return value


def optional_model_id(value: object, name: str) -> str | None:
    """``null`` or an empty text mean "the provider's default"; ids are trimmed."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return model_id(value.strip() if isinstance(value, str) else value, name)


def _exact_model(value: object, name: str) -> str | None:
    return None if value is None else model_id(value, name)


def _optional_amount(value: object, name: str) -> float | None:
    if value is None:
        return None
    return _number_in_range(value, name, AMOUNT_EUR_RANGE)


def _agent_map[T](
    value: object, name: str, item: Callable[[object, str], T | None]
) -> dict[AgentName, T | None]:
    """``Record<Agent, T | null>``: missing agents are ``None``; unknown ones are rejected."""
    if not isinstance(value, Mapping):
        raise ValueError(f"«{name}» ha de ser un objecte amb les claus «claude» i «chatgpt».")
    for key in value:
        if key not in AGENTS:
            raise ValueError(f"«{name}» només admet les claus «claude» i «chatgpt».")
    return {agent: item(value.get(agent), f"{name}.{agent}") for agent in AGENTS}


def _no_models() -> dict[AgentName, str | None]:
    return dict.fromkeys(AGENTS)


def _no_amounts() -> dict[AgentName, float | None]:
    return dict.fromkeys(AGENTS)


def _price(value: object, name: str) -> ModelPrice:
    if not isinstance(value, Mapping):
        raise ValueError(
            f"«{name}» ha de ser un objecte amb «input», «output», «cache_read» i «cache_write»."
        )
    try:
        price = ModelPrice.from_wire(value)
    except ValueError as exc:
        raise ValueError(f"«{name}»: {exc}") from None
    for key, amount in price.to_wire().items():
        if not math.isfinite(amount) or amount > MAX_PRICE_PER_MTOK:
            limit = _format_number(MAX_PRICE_PER_MTOK)
            raise ValueError(f"«{name}.{key}» ha de ser un preu entre 0 i {limit} $.")
    return price


def _prices(value: object) -> dict[str, ModelPrice]:
    """Owner prices by model id. Ids are compared as the price table does
    (:func:`~agentic_os.pricing.normalize_model`): an id that normalizes to nothing
    (``openai/``, ``x/[1m]``...) would match, and reprice, every model, and two ids
    of the same model would leave only one of them in effect, so both are refused."""
    if not isinstance(value, Mapping):
        raise ValueError("«prices» ha de ser un objecte (model → preus).")
    if len(value) > MAX_CUSTOM_PRICES:
        raise ValueError(f"«prices» admet com a màxim {MAX_CUSTOM_PRICES} models.")
    prices: dict[str, ModelPrice] = {}
    seen: dict[str, str] = {}
    for model, price in value.items():
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
            raise ValueError(
                f"«prices»: «{str(model)[:100]}» no és un identificador de model vàlid."
            )
        normalized = normalize_model(model)
        if not normalized:
            raise ValueError(
                f"«prices»: «{model}» no identifica cap model (sense el prefix del proveïdor, "
                "la data o el context no en queda res)."
            )
        if normalized in seen:
            raise ValueError(
                f"«prices»: «{seen[normalized]}» i «{model}» són el mateix model "
                f"(«{normalized}»). Deixa'n només un."
            )
        seen[normalized] = model
        prices[model] = _price(price, f"prices.{model}")
    return prices


@dataclass(frozen=True, slots=True)
class FxSettings:
    """How costs are converted to euros: the ECB daily rate (``auto``, with the
    manual rate as fallback) or always the manual rate."""

    mode: FxMode = "auto"
    eur_per_usd: float = DEFAULT_EUR_PER_USD
    """Manual rate (euros per dollar)."""

    def __post_init__(self) -> None:
        _fx_mode(self.mode)
        _number_in_range(self.eur_per_usd, "fx.eur_per_usd", EUR_PER_USD_RANGE)

    @classmethod
    def from_wire(cls, data: object) -> FxSettings:
        if not isinstance(data, Mapping):
            raise ValueError("«fx» ha de ser un objecte.")
        defaults = cls()
        return cls(
            mode=_fx_mode(data.get("mode", defaults.mode)),
            eur_per_usd=_number_in_range(
                data.get("eur_per_usd", defaults.eur_per_usd), "fx.eur_per_usd", EUR_PER_USD_RANGE
            ),
        )


def _fx_mode(value: object) -> FxMode:
    for mode in FX_MODES:
        if value == mode:
            return mode
    raise ValueError("«fx.mode» ha de ser «auto» o «manual».")


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Owner preferences stored in the database (``RuntimeSettings`` in PROTOCOL.md).

    Construction validates every field and raises :class:`ValueError` with a
    Catalan message that can be shown to the owner as is.
    """

    default_mode: TurnMode = "debate"
    default_target: AgentName = "claude"
    """Agent that answers in solo mode."""
    debate: DebateOptions = field(default_factory=DebateOptions)
    use_cache: bool = True
    compaction_threshold_tokens: int = 6000
    models: Mapping[AgentName, str | None] = field(default_factory=_no_models)
    """Default model per agent; ``None`` uses the provider's configured one."""
    fast_models: Mapping[AgentName, str | None] = field(default_factory=_no_models)
    """Model of the cheap internal calls (summaries); ``None`` uses the provider's."""
    prices: Mapping[str, ModelPrice] = field(default_factory=dict)
    """Owner prices (USD per million tokens) over ``pricing.DEFAULT_PRICES``."""
    fx: FxSettings = field(default_factory=FxSettings)
    budgets_eur: Mapping[AgentName, float | None] = field(default_factory=_no_amounts)
    """Monthly budget of the api-mode usage."""
    plans_eur: Mapping[AgentName, float | None] = field(default_factory=_no_amounts)
    """Monthly price of the subscription (cli mode)."""
    pdf_in_revisions: AttachmentMode = "text"
    """How the debate revisions get the attached PDFs: ``"text"`` their extracted text
    (far fewer tokens), ``"full"`` the document itself. The answers and the synthesis
    always get the whole document (docs/adr/0009-adjunts.md)."""
    revision: int = 0
    """How many times the settings have been saved: 0 until the first save, and 1 for
    settings saved before revisions existed. The store sets it; in a ``PUT
    /api/settings`` it is the revision the edit is based on (ADR 0006)."""

    def __post_init__(self) -> None:
        _revision(self.revision)
        _mode(self.default_mode, "default_mode")
        _agent(self.default_target, "default_target")
        if not isinstance(self.debate, DebateOptions):
            raise ValueError("«debate» ha de ser un objecte.")
        _int_in_range(self.debate.rounds, "debate.rounds", ROUNDS_RANGE)
        _int_in_range(
            self.debate.consensus_threshold,
            "debate.consensus_threshold",
            CONSENSUS_THRESHOLD_RANGE,
        )
        _agent(self.debate.synthesizer, "debate.synthesizer")
        _bool(self.use_cache, "use_cache")
        _int_in_range(
            self.compaction_threshold_tokens,
            "compaction_threshold_tokens",
            COMPACTION_THRESHOLD_RANGE,
        )
        _agent_map(self.models, "models", _exact_model)
        _agent_map(self.fast_models, "fast_models", _exact_model)
        if not isinstance(self.prices, Mapping):
            raise ValueError("«prices» ha de ser un objecte (model → preus).")
        _prices(
            {
                model: price.to_wire() if isinstance(price, ModelPrice) else price
                for model, price in self.prices.items()
            }
        )
        if not isinstance(self.fx, FxSettings):
            raise ValueError("«fx» ha de ser un objecte.")
        _agent_map(self.budgets_eur, "budgets_eur", _optional_amount)
        _agent_map(self.plans_eur, "plans_eur", _optional_amount)
        _pdf_in_revisions(self.pdf_in_revisions)

    @classmethod
    def from_wire(cls, data: object) -> RuntimeSettings:
        """Build from decoded JSON. Missing keys take their defaults (``revision``: 0;
        the store reads settings saved before revisions existed as revision 1); unknown
        keys are ignored. Raises :class:`ValueError` (Catalan message) on invalid
        values, the revision first."""
        if not isinstance(data, Mapping):
            raise ValueError("La configuració ha de ser un objecte JSON.")
        revision = _revision(data.get("revision", 0))
        defaults = cls()
        debate_raw = data.get("debate", {})
        if not isinstance(debate_raw, Mapping):
            raise ValueError("«debate» ha de ser un objecte.")
        debate = DebateOptions(
            rounds=_int_in_range(
                debate_raw.get("rounds", defaults.debate.rounds), "debate.rounds", ROUNDS_RANGE
            ),
            consensus_threshold=_int_in_range(
                debate_raw.get("consensus_threshold", defaults.debate.consensus_threshold),
                "debate.consensus_threshold",
                CONSENSUS_THRESHOLD_RANGE,
            ),
            synthesizer=_agent(
                debate_raw.get("synthesizer", defaults.debate.synthesizer), "debate.synthesizer"
            ),
        )
        return cls(
            default_mode=_mode(data.get("default_mode", defaults.default_mode), "default_mode"),
            default_target=_agent(
                data.get("default_target", defaults.default_target), "default_target"
            ),
            debate=debate,
            use_cache=_bool(data.get("use_cache", defaults.use_cache), "use_cache"),
            compaction_threshold_tokens=_int_in_range(
                data.get("compaction_threshold_tokens", defaults.compaction_threshold_tokens),
                "compaction_threshold_tokens",
                COMPACTION_THRESHOLD_RANGE,
            ),
            models=_agent_map(data.get("models", {}), "models", optional_model_id),
            fast_models=_agent_map(data.get("fast_models", {}), "fast_models", optional_model_id),
            prices=_prices(data.get("prices", {})),
            fx=FxSettings.from_wire(data.get("fx", {})),
            budgets_eur=_agent_map(data.get("budgets_eur", {}), "budgets_eur", _optional_amount),
            plans_eur=_agent_map(data.get("plans_eur", {}), "plans_eur", _optional_amount),
            pdf_in_revisions=_pdf_in_revisions(
                data.get("pdf_in_revisions", defaults.pdf_in_revisions)
            ),
            revision=revision,
        )

    def to_wire(self) -> Wire:
        prices: dict[str, JsonValue] = {}
        for model in sorted(self.prices):
            prices[model] = {key: value for key, value in self.prices[model].to_wire().items()}
        return {
            "revision": self.revision,
            "default_mode": self.default_mode,
            "default_target": self.default_target,
            "debate": {
                "rounds": self.debate.rounds,
                "consensus_threshold": self.debate.consensus_threshold,
                "synthesizer": self.debate.synthesizer,
            },
            "use_cache": self.use_cache,
            "compaction_threshold_tokens": self.compaction_threshold_tokens,
            "models": {agent: self.models.get(agent) for agent in AGENTS},
            "fast_models": {agent: self.fast_models.get(agent) for agent in AGENTS},
            "prices": prices,
            "fx": {"mode": self.fx.mode, "eur_per_usd": self.fx.eur_per_usd},
            "budgets_eur": {agent: self.budgets_eur.get(agent) for agent in AGENTS},
            "plans_eur": {agent: self.plans_eur.get(agent) for agent in AGENTS},
            "pdf_in_revisions": self.pdf_in_revisions,
        }

    def to_turn_options(self) -> TurnOptions:
        """Default options for a turn that does not specify its own."""
        return TurnOptions(debate=self.debate, use_cache=self.use_cache)

    def chosen_models(self) -> dict[AgentName, str]:
        """``TurnRequest.models``: the agents with a default model chosen by the owner."""
        return {agent: model for agent in AGENTS if (model := self.models.get(agent))}

    def chosen_fast_models(self) -> dict[AgentName, str]:
        """``TurnRequest.fast_models``: the agents with a chosen summary model."""
        return {agent: model for agent in AGENTS if (model := self.fast_models.get(agent))}


# --------------------------------------------------------------------------
# Exchange rate
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredFxRate:
    """The last ECB rate fetched and when it was fetched."""

    rate: FxRate
    fetched_at: datetime

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "eur_per_usd": self.rate.eur_per_usd,
            "as_of": self.rate.as_of.isoformat() if self.rate.as_of else None,
            "fetched_at": format_ts(self.fetched_at),
        }

    @classmethod
    def from_json(cls, data: object) -> StoredFxRate:
        """Raises :class:`ValueError` if ``data`` is not what :meth:`to_json` wrote."""
        if not isinstance(data, Mapping):
            raise ValueError("invalid stored exchange rate")
        as_of, fetched_at = data.get("as_of"), data.get("fetched_at")
        if not isinstance(fetched_at, str) or not (as_of is None or isinstance(as_of, str)):
            raise ValueError("invalid stored exchange rate")
        eur_per_usd = _number_in_range(data.get("eur_per_usd"), "eur_per_usd", EUR_PER_USD_RANGE)
        return cls(
            rate=FxRate(
                eur_per_usd=eur_per_usd,
                as_of=date.fromisoformat(as_of) if as_of else None,
                source="ecb",
            ),
            fetched_at=parse_ts(fetched_at),
        )


def effective_fx(settings: RuntimeSettings, ecb: StoredFxRate | None, now: datetime) -> FxRate:
    """The rate costs are converted with: the ECB one in ``auto`` mode when it was
    fetched in the last :data:`FX_MAX_AGE`, else the owner's manual rate."""
    if (
        settings.fx.mode == "auto"
        and ecb is not None
        and as_utc(now) - ecb.fetched_at <= FX_MAX_AGE
    ):
        return ecb.rate
    return manual_rate(settings.fx.eur_per_usd)


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------


def message_to_wire(message: StoredMessage) -> Wire:
    """``Message`` of PROTOCOL.md."""
    return {
        "id": message.id,
        "turn_id": message.turn_id,
        "kind": message.kind,
        "content": message.content,
        "agent": message.agent,
        "round": message.round,
        "final": message.final,
        "meta": dict(message.meta),
        "created_at": format_ts(message.created_at),
    }


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """``ConversationSummary`` of PROTOCOL.md (one row of the conversation list)."""

    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    """Time of the last message added (renaming does not change it)."""
    last_mode: TurnMode | None
    message_count: int
    """All stored messages of the conversation (every kind)."""

    def to_wire(self) -> Wire:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": format_ts(self.created_at),
            "updated_at": format_ts(self.updated_at),
            "last_mode": self.last_mode,
            "message_count": self.message_count,
        }


@dataclass(frozen=True, slots=True)
class ConversationDetail:
    """``ConversationDetail`` of PROTOCOL.md: the summary fields plus the compaction
    summary and every message, oldest first."""

    conversation: ConversationSummary
    summary: str | None
    messages: tuple[StoredMessage, ...]

    def to_wire(self) -> Wire:
        wire = self.conversation.to_wire()
        wire["summary"] = self.summary
        wire["messages"] = [message_to_wire(m) for m in self.messages]
        return wire


# --------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    """An uploaded attachment as stored (``Attachment`` of PROTOCOL.md): its file is at
    :meth:`~agentic_os.storage.SqliteStore.content_path` of ``sha256``."""

    id: int
    sha256: str
    kind: AttachmentKind
    mime: str
    name: str
    """Display name, sanitized (:func:`agentic_os.attachments.display_name`)."""
    size: int
    pages: int | None
    width: int | None
    height: int | None
    text_chars: int | None
    """Characters of the stored text (a text file's content, a PDF's extracted text);
    ``None`` without one."""
    has_thumbnail: bool
    created_at: datetime
    pdf_notes: PdfNotes | None = None
    """The warnings of an analysed PDF's pages (``pdf_facts.pdf_notes``); ``None`` for
    anything else."""

    def to_wire(self) -> Wire:
        return attachment_wire(
            self.id,
            name=self.name,
            kind=self.kind,
            mime=self.mime,
            size=self.size,
            sha256=self.sha256,
            pages=self.pages,
            width=self.width,
            height=self.height,
            created_at=self.created_at,
            has_thumbnail=self.has_thumbnail,
            text_chars=self.text_chars,
            pdf_notes=self.pdf_notes,
        )


# --------------------------------------------------------------------------
# Owner, sessions and login throttling
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerRecord:
    """The single owner account. Secrets are excluded from ``repr`` so they never
    end up in logs by accident."""

    password_hash: str = field(repr=False)
    totp_secret: str = field(repr=False)
    totp_last_step: int
    """Last accepted TOTP time step (replay protection)."""
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A server-side login session. Only the SHA-256 of the cookie token is stored."""

    token_hash: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    """Absolute expiry (the idle timeout is applied on top of ``last_seen_at``)."""
    ip: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """A known owner device. Only the SHA-256 of the device cookie is stored."""

    token_hash: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ThrottleState:
    """Failed login attempts for one throttle key ("global" or "ip:<addr>")."""

    key: str
    failures: int
    locked_until: datetime | None
    last_failure_at: datetime
