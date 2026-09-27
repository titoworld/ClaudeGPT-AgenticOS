"""providers.factory: one provider per agent according to the configured modes."""

from pathlib import Path
from typing import Any

import pytest

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode
from agentic_os.providers.claude_api import ClaudeApiProvider
from agentic_os.providers.claude_cli import ClaudeCliProvider
from agentic_os.providers.codex_appserver import CodexAppServerProvider
from agentic_os.providers.factory import build_provider, build_providers
from agentic_os.providers.fake import FakeProvider
from agentic_os.providers.openai_api import OpenAIApiProvider

EXPECTED: dict[tuple[AgentName, ProviderMode], type] = {
    ("claude", "cli"): ClaudeCliProvider,
    ("claude", "api"): ClaudeApiProvider,
    ("claude", "fake"): FakeProvider,
    ("chatgpt", "cli"): CodexAppServerProvider,
    ("chatgpt", "api"): OpenAIApiProvider,
    ("chatgpt", "fake"): FakeProvider,
}


def isolated_settings(**values: Any) -> Settings:
    """Settings from ``values`` only (no ``.env`` file)."""
    return Settings(**{"_env_file": None, **values})


@pytest.mark.parametrize(("agent", "mode"), list(EXPECTED))
async def test_build_provider(agent: AgentName, mode: ProviderMode, tmp_path: Path) -> None:
    settings = isolated_settings(data_dir=tmp_path)
    provider = build_provider(agent, mode, settings)
    try:
        assert isinstance(provider, EXPECTED[(agent, mode)])
        assert provider.agent == agent
        assert provider.mode == mode
    finally:
        await provider.aclose()


async def test_build_providers_follows_the_settings(tmp_path: Path) -> None:
    settings = isolated_settings(data_dir=tmp_path, claude_mode="fake", chatgpt_mode="api")
    providers = build_providers(settings)
    try:
        assert list(providers) == ["claude", "chatgpt"]
        assert isinstance(providers["claude"], FakeProvider)
        assert isinstance(providers["chatgpt"], OpenAIApiProvider)
    finally:
        for provider in providers.values():
            await provider.aclose()
