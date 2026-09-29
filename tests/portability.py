"""Checks that behave the same on Linux and on macOS (audit point 24).

The state of a process comes from ``/proc`` on Linux and from ``ps`` elsewhere. Where
neither works (Linux with ``/proc`` hidden, as the audit simulated macOS), a check that
needs it skips the test: it never fails for that reason, nor passes without checking
anything. Whether a process is gone needs neither: ``kill(pid, 0)`` tells.

The environment a child process sees may also hold variables the system adds by
itself: macOS adds ``__CF_USER_TEXT_ENCODING`` (and ``VERSIONER_*``) even to an empty
environment, and Python may add ``LC_CTYPE`` (PEP 538). CPython's own tests ignore
them too (``test_subprocess.test_empty_env``).

Importable from any test module (``pythonpath`` in pyproject.toml).
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import pytest

ProcessState = Literal["running", "zombie", "gone"]

_PROC = Path("/proc")
_DEAD_STATES = ("Z", "X")
"""``/proc`` and ``ps`` states of a process that has exited (zombie, dead)."""
PLATFORM_VARIABLE_PREFIXES = ("__CF_", "VERSIONER_")
PLATFORM_VARIABLES = frozenset({"LC_CTYPE"})


def proc_available() -> bool:
    """Whether ``/proc`` describes the processes (Linux, and not hidden)."""
    return (_PROC / "self" / "stat").is_file()


@functools.cache
def _ps() -> str | None:
    """Path of a working ``ps`` (it needs ``/proc`` on Linux), or ``None``."""
    ps = shutil.which("ps")
    if ps is None:
        return None
    probe = subprocess.run(
        [ps, "-o", "stat=", "-p", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    return ps if probe.returncode == 0 and probe.stdout.strip() else None


def _require_ps() -> str:
    ps = _ps()
    if ps is None:
        pytest.skip("no /proc and no working ps: cannot tell a zombie from a live process")
    return ps


def _exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it exists, but belongs to another user
        return True
    return True


def process_state(pid: int) -> ProcessState:
    """``running``, ``zombie`` (exited, not reaped yet) or ``gone`` (reaped).

    Telling a zombie from a live process needs ``/proc`` or ``ps``: without them the
    test is skipped (see the module docstring)."""
    if not _exists(pid):
        return "gone"
    if proc_available():
        try:
            stat = (_PROC / str(pid) / "stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            # ProcessLookupError (ESRCH): it exited between opening and reading the file.
            return "gone"
        return "zombie" if stat.rsplit(")", 1)[1].split()[0] in _DEAD_STATES else "running"
    result = subprocess.run(
        [_require_ps(), "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    state = result.stdout.strip()
    if not state:
        return "gone"
    return "zombie" if state[0] in _DEAD_STATES else "running"


def alive(pid: int) -> bool:
    """Whether ``pid`` still runs (a zombie counts as dead)."""
    return process_state(pid) == "running"


def exited(pid: int) -> bool:
    """Whether ``pid`` has exited: a zombie waiting to be reaped, or gone."""
    return process_state(pid) != "running"


def reaped(pid: int) -> bool:
    """Whether a direct child has exited and was waited for (no zombie left). Works
    everywhere: a zombie still exists for ``kill(pid, 0)``."""
    return not _exists(pid)


def group_alive(pgid: int) -> bool:
    """Whether a live (not zombie) process of the process group ``pgid`` is left."""
    if proc_available():
        for stat_file in _PROC.glob("[0-9]*/stat"):
            try:
                fields = stat_file.read_text().rsplit(")", 1)[1].split()
            except (OSError, IndexError):
                continue
            # state, ppid, pgrp, ...
            if int(fields[2]) == pgid and fields[0] not in _DEAD_STATES:
                return True
        return False
    result = subprocess.run(
        [_require_ps(), "-A", "-o", "pgid=,stat="],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == str(pgid) and fields[1][0] not in _DEAD_STATES:
            return True
    return False


def without_platform_variables(names: Iterable[str]) -> set[str]:
    """``names`` without the variables the system adds to any child process by itself
    (see the module docstring)."""
    return {
        name
        for name in names
        if name not in PLATFORM_VARIABLES and not name.startswith(PLATFORM_VARIABLE_PREFIXES)
    }
