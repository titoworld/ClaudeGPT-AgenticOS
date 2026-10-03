"""Process configuration, read from environment variables (prefix ``AOS_``) and ``.env``.

Runtime preferences that the owner can change from the dashboard (default mode,
debate rounds...) live in the database instead (see storage).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentic_os.domain import ProviderMode
from agentic_os.i18n import t

LOG_LEVELS: Final = ("critical", "error", "warning", "info", "debug", "trace")
"""uvicorn's log levels."""


class Settings(BaseSettings):
    """Every value is checked when it is read, with bounds wherever a value out of
    them would stop the server (or make it useless), so ``agentic-os doctor`` and the
    other commands report it instead of ``serve`` failing at startup."""

    model_config = SettingsConfigDict(
        env_prefix="AOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Path("data")
    """SQLite database and other state."""
    web_dist: Path | None = None
    """Built frontend (web/dist). None: autodetect next to the project."""
    public_origin: str = "http://localhost:8000"
    """Exact origin the browser uses (scheme://host[:port]); checked on every
    state-changing request and WebSocket handshake."""
    extra_origins: list[str] = Field(default_factory=list)
    """Additional allowed origins, e.g. the Vite dev server http://localhost:5173."""
    secure_cookies: bool = True
    """Set to false only for local development over plain http."""
    trusted_proxies: str = "127.0.0.1"
    """Comma-separated IPs/CIDRs whose X-Forwarded-* headers are trusted (the reverse proxy)."""
    log_level: str = "info"
    """One of :data:`LOG_LEVELS`, in any case (stored in lowercase)."""

    # --- Sessions and login -----------------------------------------------
    session_idle_hours: int = Field(default=72, ge=1, le=24 * 365)
    session_max_days: int = Field(default=30, ge=1, le=3650)
    login_max_failures: int = Field(default=5, ge=1, le=1000)
    """Failures allowed before the exponential lockout starts."""

    # --- Providers ----------------------------------------------------------
    claude_mode: ProviderMode = "cli"
    chatgpt_mode: ProviderMode = "cli"
    claude_model: str | None = None
    """None: the provider's default for its mode."""
    claude_fast_model: str | None = None
    chatgpt_model: str | None = None
    chatgpt_fast_model: str | None = None
    claude_cli_path: str = "claude"
    codex_cli_path: str = "codex"
    codex_state_dir: Path | None = None
    """Where Codex keeps its SQLite state and logs (they contain full prompts). None:
    ``<data_dir>/sandbox/codex-state``. The Docker image points it to a tmpfs."""
    provider_timeout_seconds: float = Field(default=600.0, gt=0, le=24 * 3600)
    """Longest a model call may take (also refuses NaN and infinity)."""
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AOS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    )
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AOS_OPENAI_API_KEY", "OPENAI_API_KEY")
    )

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        level = value.strip().lower()
        if level not in LOG_LEVELS:
            raise ValueError(t("cli.settings.one_of", values=", ".join(LOG_LEVELS)))
        return level

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agentic_os.sqlite3"

    @property
    def allowed_origins(self) -> frozenset[str]:
        origins = [self.public_origin, *self.extra_origins]
        return frozenset(o.rstrip("/") for o in origins if o)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
