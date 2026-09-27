"""Command-line entry point (`agentic-os`)."""

import argparse
from collections.abc import Sequence

from agentic_os import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-os",
        description="ClaudeGPT Agentic OS: sistema operativo agéntico (fase 0).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print(f"Agentic OS {__version__}: fase 0, arquitectura por definir (ver docs/VISION.md).")
    return 0
