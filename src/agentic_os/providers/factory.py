"""Builds the provider of each agent from the process settings.

Implementations are imported lazily, so a mode that is not used never loads its
SDK (``anthropic``, ``openai``) or touches its CLI.
"""

from __future__ import annotations

from pathlib import Path

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode
from agentic_os.providers.base import Provider


def build_provider(agent: AgentName, mode: ProviderMode, settings: Settings) -> Provider:
    """The provider of ``agent`` in ``mode`` (``cli``, ``api`` or ``fake``)."""
    if mode == "fake":
        from agentic_os.providers.fake import FakeProvider

        return FakeProvider(agent)
    if agent == "claude":
        if mode == "cli":
            from agentic_os.providers.claude_cli import ClaudeCliProvider

            return ClaudeCliProvider(settings)
        from agentic_os.providers.claude_api import ClaudeApiProvider

        return ClaudeApiProvider(settings)
    if mode == "cli":
        from agentic_os.providers.codex_appserver import CodexAppServerProvider

        return CodexAppServerProvider(settings)
    from agentic_os.providers.openai_api import OpenAIApiProvider

    return OpenAIApiProvider(settings)


def build_providers(
    settings: Settings, *, codex_state_dir: Path | None = None
) -> dict[AgentName, Provider]:
    """One provider per agent, per ``settings.claude_mode`` and ``settings.chatgpt_mode``.

    Constructing a provider starts nothing (processes and HTTP clients are created on
    first use); the caller owns the result and must ``aclose()`` every provider.
    ``codex_state_dir`` replaces ``settings.codex_state_dir``: ``agentic-os doctor``
    gives its Codex a private one, because two app-servers must never share it.
    """
    if codex_state_dir is not None:
        settings = settings.model_copy(update={"codex_state_dir": codex_state_dir})
    return {
        "claude": build_provider("claude", settings.claude_mode, settings),
        "chatgpt": build_provider("chatgpt", settings.chatgpt_mode, settings),
    }
