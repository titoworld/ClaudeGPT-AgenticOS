"""SQLite persistence (WAL): conversations, messages, usage, savings, turn cache,
runtime settings, the owner account, sessions and login throttling.

Entry point: ``store = await SqliteStore.open(settings.db_path)``.
"""

from agentic_os.storage.db import SCHEMA_VERSION, SchemaVersionError
from agentic_os.storage.models import (
    ConversationDetail,
    ConversationSummary,
    OwnerRecord,
    RuntimeSettings,
    SessionRecord,
    ThrottleState,
    format_ts,
    message_to_wire,
    parse_ts,
    utc_now,
)
from agentic_os.storage.stats import Stats
from agentic_os.storage.store import (
    DEFAULT_TITLE,
    MAX_LIST_LIMIT,
    ConversationNotFoundError,
    SqliteStore,
)

__all__ = [
    "DEFAULT_TITLE",
    "MAX_LIST_LIMIT",
    "SCHEMA_VERSION",
    "ConversationDetail",
    "ConversationNotFoundError",
    "ConversationSummary",
    "OwnerRecord",
    "RuntimeSettings",
    "SchemaVersionError",
    "SessionRecord",
    "SqliteStore",
    "Stats",
    "ThrottleState",
    "format_ts",
    "message_to_wire",
    "parse_ts",
    "utc_now",
]
