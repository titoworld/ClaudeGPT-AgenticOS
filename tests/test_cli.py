from importlib.metadata import version

import pytest

from agentic_os import __version__
from agentic_os.cli import main


def test_version_matches_package_metadata() -> None:
    assert __version__ == version("agentic-os")


def test_main_prints_status_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert __version__ in capsys.readouterr().out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"agentic-os {__version__}"
