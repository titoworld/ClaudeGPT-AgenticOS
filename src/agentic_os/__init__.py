"""ClaudeGPT OS: a private council of Claude and ChatGPT for a single owner.

See docs/ARQUITECTURA.md (modules and contracts), docs/PROTOCOL.md (client-server
protocol) and docs/adr/ (decisions).
"""

from importlib.metadata import version

__version__ = version("agentic-os")

__all__ = ["__version__"]
