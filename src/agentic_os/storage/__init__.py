"""SQLite persistence (WAL): conversations, messages, attachments (their files next to
the database), usage, savings, turn cache, runtime settings, the last ECB exchange rate,
the owner account, sessions, known devices and login throttling.

Entry point: ``store = await SqliteStore.open(settings.db_path)``.
"""

from agentic_os.storage.db import SCHEMA_VERSION, SchemaVersionError
from agentic_os.storage.files import IncomingFile
from agentic_os.storage.models import (
    AttachmentRecord,
    ConversationDetail,
    ConversationSummary,
    DeviceRecord,
    FxSettings,
    OwnerRecord,
    RuntimeSettings,
    SessionRecord,
    StoredFxRate,
    ThrottleState,
    effective_fx,
    format_ts,
    message_to_wire,
    parse_ts,
    utc_now,
)
from agentic_os.storage.search import MAX_SEARCH_LENGTH
from agentic_os.storage.stats import MonthSpend, Stats
from agentic_os.storage.store import (
    DEFAULT_TITLE,
    MAX_LIST_LIMIT,
    AttachmentInUseError,
    ConversationNotFoundError,
    SettingsConflictError,
    SqliteStore,
)

__all__ = [
    "DEFAULT_TITLE",
    "MAX_LIST_LIMIT",
    "MAX_SEARCH_LENGTH",
    "SCHEMA_VERSION",
    "AttachmentInUseError",
    "AttachmentRecord",
    "ConversationDetail",
    "ConversationNotFoundError",
    "ConversationSummary",
    "DeviceRecord",
    "FxSettings",
    "IncomingFile",
    "MonthSpend",
    "OwnerRecord",
    "RuntimeSettings",
    "SchemaVersionError",
    "SessionRecord",
    "SettingsConflictError",
    "SqliteStore",
    "Stats",
    "StoredFxRate",
    "ThrottleState",
    "effective_fx",
    "format_ts",
    "message_to_wire",
    "parse_ts",
    "utc_now",
]
