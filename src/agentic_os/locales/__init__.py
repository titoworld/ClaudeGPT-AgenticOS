"""The catalogs of :mod:`agentic_os.i18n`: every text the server writes for people to
read, by key, in English, Spanish and Catalan. One module per area; a key starts with its
area (``server.``, ``engine.``...) so two areas never share one."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from agentic_os.i18n import Text
from agentic_os.locales import attachments, cli, demo, engine, providers, server, storage

AREAS: Final[Mapping[str, Mapping[str, Text]]] = {
    "attachments": attachments.MESSAGES,
    "cli": cli.MESSAGES,
    "demo": demo.MESSAGES,
    "engine": engine.MESSAGES,
    "providers": providers.MESSAGES,
    "server": server.MESSAGES,
    "storage": storage.MESSAGES,
}

MESSAGES: Final[Mapping[str, Text]] = {
    key: text for area in AREAS.values() for key, text in area.items()
}
