from __future__ import annotations

import pytest

from ghtools.cli import main, write_github_output
from ghtools.errors import CheckFailed, ConfigError, GhtoolsError, PreconditionError


def test_version_flag_prints_name(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("ghtools ")


def test_no_command_prints_help_and_returns_1(capsys):
    assert main([]) == 1
    assert "usage: ghtools" in capsys.readouterr().out


def test_exit_codes_match_spec():
    assert GhtoolsError.exit_code == 1
    assert ConfigError.exit_code == 1
    assert CheckFailed.exit_code == 2
    assert PreconditionError.exit_code == 3


def test_write_github_output_appends_lines(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    write_github_output({"a": "1", "b": "two"})
    write_github_output({"c": "3"})
    assert out.read_text() == "a=1\nb=two\nc=3\n"


def test_write_github_output_outside_actions_is_an_error(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    with pytest.raises(GhtoolsError, match="GITHUB_OUTPUT"):
        write_github_output({"a": "1"})
