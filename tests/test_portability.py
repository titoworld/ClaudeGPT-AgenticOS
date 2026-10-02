"""The checks of tests/portability.py (audit point 24): the process checks of the
provider and deployment tests hold on Linux and on macOS, and where they cannot tell
(no /proc and no working ps) they skip the test instead of passing without checking."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import portability
import pytest
from portability import (
    alive,
    exited,
    group_alive,
    process_state,
    reaped,
    without_platform_variables,
)

TESTS = Path(__file__).resolve().parent


def wait_for(check: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


@contextmanager
def sleeper(*, new_session: bool = False) -> Iterator[subprocess.Popen[bytes]]:
    """A child that runs until its stdin is closed; closed and reaped at the end (also
    when the test is skipped or fails)."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        start_new_session=new_session,
    )
    try:
        yield child
    finally:
        finish(child)


def finish(child: subprocess.Popen[bytes]) -> None:
    """Let ``child`` exit (it reads its stdin until closed) and reap it."""
    assert child.stdin is not None
    child.stdin.close()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def test_a_child_runs_then_is_a_zombie_then_is_gone() -> None:
    with sleeper() as child:
        assert process_state(child.pid) == "running"
        assert alive(child.pid) and not exited(child.pid) and not reaped(child.pid)
        assert child.stdin is not None
        child.stdin.close()  # it exits, and nobody has waited for it yet
        wait_for(lambda: process_state(child.pid) == "zombie")
        assert not alive(child.pid) and exited(child.pid) and not reaped(child.pid)
    assert process_state(child.pid) == "gone"
    assert reaped(child.pid) and exited(child.pid) and not alive(child.pid)


def test_a_process_group_is_alive_while_one_of_its_processes_runs() -> None:
    with sleeper(new_session=True) as leader:
        assert group_alive(leader.pid)
        assert leader.stdin is not None
        leader.stdin.close()
        wait_for(lambda: not group_alive(leader.pid))  # a zombie leader does not count
    assert not group_alive(leader.pid)


def test_the_variables_the_system_adds_to_a_child_are_left_out() -> None:
    names = {
        "PATH",
        "HOME",
        "AOS_SECRET",
        "LC_CTYPE",  # Python's locale coercion (PEP 538)
        "__CF_USER_TEXT_ENCODING",  # macOS, even with an empty environment
        "VERSIONER_PYTHON_VERSION",  # macOS
    }
    assert without_platform_variables(names) == {"PATH", "HOME", "AOS_SECRET"}


HIDDEN_PROC_CHECK = textwrap.dedent(
    """
    import os, subprocess, sys
    import pytest
    from portability import exited, proc_available, process_state, reaped

    assert not proc_available()
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)  # exited, not reaped
    assert not reaped(child.pid)  # kill(pid, 0) still finds the zombie
    try:
        state = process_state(child.pid)
    except pytest.skip.Exception:
        state = "skipped"
    # Without /proc (and so without ps, on Linux) a zombie cannot be told from a live
    # process: the check skips its test rather than answer anything.
    assert state in ("skipped", "zombie"), state
    child.wait()
    assert reaped(child.pid) and exited(child.pid) and process_state(child.pid) == "gone"
    print("checked:", state)
    """
)


def hide_proc_command() -> list[str] | None:
    """A command prefix that runs a shell command with an empty /proc (a new mount
    namespace with a tmpfs over it), or ``None`` where that is not possible."""
    unshare = shutil.which("unshare")
    if sys.platform != "linux" or unshare is None:
        return None
    for flags in ("-m", "-rm"):  # as root, or in a user namespace
        command = [unshare, flags, "--propagation", "private", "sh", "-c"]
        probe = subprocess.run(
            [*command, "mount -t tmpfs none /proc"], capture_output=True, check=False
        )
        if probe.returncode == 0:
            return command
    return None


def test_without_proc_the_checks_skip_instead_of_guessing() -> None:
    """The audit's macOS simulation: /proc hidden by a tmpfs in a new mount namespace."""
    command = hide_proc_command()
    if command is None:
        pytest.skip("cannot hide /proc here (needs Linux, unshare and a mount namespace)")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(TESTS), *sys.path])}
    result = subprocess.run(
        [
            *command,
            'mount -t tmpfs none /proc && exec "$0" -c "$1"',
            sys.executable,
            HIDDEN_PROC_CHECK,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in ("checked: skipped", "checked: zombie")


@pytest.fixture
def ps_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The checks as on macOS: no /proc, only ps."""
    if portability._ps() is None:
        pytest.skip("no working ps here")
    monkeypatch.setattr(portability, "proc_available", lambda: False)


@pytest.mark.usefixtures("ps_only")
def test_the_checks_through_ps() -> None:
    with sleeper() as child, sleeper(new_session=True) as leader:
        assert process_state(child.pid) == "running"
        assert group_alive(leader.pid)
        assert child.stdin is not None and leader.stdin is not None
        child.stdin.close()
        leader.stdin.close()
        wait_for(lambda: process_state(child.pid) == "zombie")
        wait_for(lambda: not group_alive(leader.pid))
    assert process_state(child.pid) == "gone"
