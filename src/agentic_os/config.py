"""Process configuration, read from environment variables (prefix ``AOS_``) and ``.env``.

Runtime preferences that the owner can change from the dashboard (default mode,
debate rounds...) live in the database instead (see storage).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentic_os.domain import ProviderMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
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

    # --- Sessions and login -----------------------------------------------
    session_idle_hours: int = 72
    session_max_days: int = 30
    login_max_failures: int = 5
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
    provider_timeout_seconds: float = 600.0
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AOS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    )
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AOS_OPENAI_API_KEY", "OPENAI_API_KEY")
    )

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
