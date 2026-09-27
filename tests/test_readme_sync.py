"""README block sync tests."""

from __future__ import annotations

import pytest

from ghtools import config, readme
from ghtools.cli import main
from ghtools.errors import ConfigError

TOML = """\
[[readme.block]]
name = "overview"
source = "docs/_shared/overview.rst"

[[readme.block]]
name = "status"
kind = "status"
"""


def _repo(tmp_path, readme_text, toml=TOML):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text(toml)
    (tmp_path / "docs/_shared").mkdir(parents=True)
    (tmp_path / "docs/_shared/overview.rst").write_text("Hello ``world``.\n")
    (tmp_path / "README.md").write_bytes(readme_text.encode())
    return config.load(tmp_path)


def _blank(name, source):
    start, end = readme.markers(name, source)
    return f"{start}\n{end}"


def test_block_settings_validation():
    with pytest.raises(ConfigError, match=r"readme.block\[0\].source: required for kind = 'sync'"):
        config.from_dict({"readme": {"block": [{"name": "x"}]}})
    with pytest.raises(ConfigError, match=r"readme.block\[1\].name: duplicate block name 'x'"):
        config.from_dict(
            {
                "readme": {
                    "block": [{"name": "x", "source": "a.md"}, {"name": "x", "source": "b.md"}]
                }
            }
        )


def test_write_then_check(tmp_path, monkeypatch):
    text = f"# X\n\n{_blank('overview', 'docs/_shared/overview.rst')}\n\n{_blank('status', 'the status branch')}\n"
    cfg = _repo(tmp_path, text)
    monkeypatch.setattr(readme, "repo_slug", lambda _root: "o/r")
    assert readme.sync(tmp_path, cfg, write=False) == ["overview", "status"]
    assert readme.sync(tmp_path, cfg, write=True) == ["overview", "status"]
    out = (tmp_path / "README.md").read_text()
    assert "Hello `world`." in out
    assert '<img src="https://github.com/o/r/raw/ghtools-status/status.svg"' in out
    assert readme.sync(tmp_path, cfg, write=False) == []


def test_crlf_readme_stays_crlf(tmp_path, monkeypatch):
    text = f"# X\r\n\r\n{_blank('overview', 'docs/_shared/overview.rst')}\r\n".replace(
        "\n", "\r\n"
    ).replace("\r\r\n", "\r\n")
    cfg = _repo(tmp_path, text, toml=TOML.split('[[readme.block]]\nname = "status"')[0])
    readme.sync(tmp_path, cfg, write=True)
    raw = (tmp_path / "README.md").read_bytes().decode()
    assert "Hello `world`." in raw
    assert "\n" not in raw.replace("\r\n", "")
    assert readme.sync(tmp_path, cfg, write=False) == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# X\n", "README.md has no ghtools:sync overview markers"),
        (
            "<!-- ghtools:sync overview END -->\n<!-- ghtools:sync overview START — generated from x, do not edit here -->\n",
            "overview: END marker before START",
        ),
        (
            "<!-- ghtools:sync overview START — a -->\n<!-- ghtools:sync overview START — b -->\n<!-- ghtools:sync overview END -->\n",
            "overview: 2 START markers",
        ),
    ],
)
def test_bad_markers_are_clear_errors(tmp_path, text, message):
    cfg = _repo(tmp_path, text, toml=TOML.split('[[readme.block]]\nname = "status"')[0])
    with pytest.raises(readme.ReadmeError, match=message):
        readme.sync(tmp_path, cfg, write=True)
    assert (tmp_path / "README.md").read_text() == text


def test_status_block_without_github_origin(tmp_path, monkeypatch):
    cfg = _repo(
        tmp_path,
        _blank("status", "the status branch") + "\n",
        toml='[[readme.block]]\nname = "status"\nkind = "status"\n',
    )
    monkeypatch.setattr(readme, "repo_slug", lambda _root: None)
    with pytest.raises(readme.ReadmeError, match="status block needs a GitHub 'origin' remote"):
        readme.sync(tmp_path, cfg, write=True)


def test_cli_check_fails_when_stale(tmp_path, capsys):
    _repo(
        tmp_path,
        _blank("overview", "docs/_shared/overview.rst") + "\n",
        toml=TOML.split('[[readme.block]]\nname = "status"')[0],
    )
    assert main(["-C", str(tmp_path), "readme", "sync", "--check"]) == 2
    assert "out of date: overview" in capsys.readouterr().err
    assert main(["-C", str(tmp_path), "readme", "sync", "--write"]) == 0
    assert main(["-C", str(tmp_path), "readme", "sync", "--check"]) == 0


def test_readme_sync_gate_is_builtin():
    from ghtools.gates import BUILTIN

    assert BUILTIN["readme-sync"].run == "ghtools readme sync --check"
    config.from_dict({"ci": {"gates": ["readme-sync"]}})  # accepted
