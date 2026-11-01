from __future__ import annotations

import pytest

from ghtools import ci, components, config
from ghtools.cli import main
from ghtools.errors import ConfigError


def _cfg(*rows):
    return config.from_dict({"subprojects": list(rows)})


def test_no_release_keys_no_components():
    cfg = _cfg({"name": "a", "path": "a"}, {"name": "b", "path": "b", "needs": ["a"]})
    assert components.released(cfg) == []
    assert components.released(config.from_dict({})) == []


def test_defaults_derive_from_path():
    (c,) = components.released(_cfg({"name": "cdn", "path": "src/cdn/", "release": True}))
    assert c == components.Component(
        "cdn", "src/cdn", "src/cdn/pyproject.toml", "src/cdn/CHANGELOG.md", "{name}/{version}", ()
    )
    assert c.tag_for("0.4.1") == "cdn/0.4.1"


def test_overrides_are_kept():
    (c,) = components.released(
        _cfg(
            {
                "name": "x",
                "path": "x",
                "release": True,
                "version-file": "x/package.json",
                "changelog": "x/HISTORY.md",
                "tag": "x-v{version}",
            }
        )
    )
    assert (c.version_file, c.changelog, c.tag_for("1.0.0")) == (
        "x/package.json",
        "x/HISTORY.md",
        "x-v1.0.0",
    )


def test_needs_paths_transitive():
    cfg = _cfg(
        {"name": "base", "path": "lib/base"},
        {"name": "mid", "path": "lib/mid", "needs": ["base"]},
        {"name": "top", "path": "app", "needs": ["mid", "base"], "release": True},
    )
    (c,) = components.released(cfg)
    assert c.needs_paths == ("lib/base", "lib/mid")


def test_submodule_component_excluded():
    cfg = _cfg(
        {"name": "vendored", "path": "vendor/v", "kind": "submodule", "release": True},
        {"name": "own", "path": "own", "release": True},
    )
    assert [c.name for c in components.released(cfg)] == ["own"]


@pytest.mark.parametrize(
    "tag", ["{name}", "v1", "{name}/{version}/{other}", "{version!r}", "{version:>3}", "{"]
)
def test_tag_template_validated(tag):
    with pytest.raises(ConfigError, match="tag"):
        _cfg({"name": "a", "path": "a", "release": True, "tag": tag})


def test_tag_template_accepts_version_only():
    _cfg({"name": "a", "path": "a", "tag": "release-{version}"})


def test_release_root_defaults_true():
    assert config.from_dict({}).get("release.root") is True
    assert config.from_dict({"release": {"root": False}}).get("release.root") is False


ROW = {"name": "ms", "path": "ms", "tests": "ms/tests", "test-command": "./run-tests.sh"}


def test_test_command_verbatim():
    cfg = _cfg(ROW, {"name": "plain", "path": "plain"})
    assert ci.test_command(cfg, "ms") == "./run-tests.sh"
    # Not pytest, so markers cannot apply: more tests run, never fewer.
    assert ci.test_command(cfg, "ms", ["tui"]) == "./run-tests.sh"
    assert ci.test_command(cfg, "plain", ["tui"]).endswith("-m 'not tui'")


def test_install_skipped_for_test_command():
    cfg = _cfg(ROW)
    assert ci.install_command(cfg, "ms") == []
    with_install = _cfg({**ROW, "install": ["-e ./ms[test]"]})
    assert ci.install_command(with_install, "ms")[-5:-2] == ["install", "-e", "./ms[test]"]
    needing = _cfg({"name": "lib", "path": "lib"}, {**ROW, "needs": ["lib"]})
    assert ci.install_command(needing, "ms")[-5:-2] == ["install", "-e", "./lib"]


def test_cli_install_with_nothing_to_do(tmp_path, monkeypatch):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text(
        '[[subprojects]]\nname = "ms"\npath = "ms"\ntest-command = "./run.sh"\n'
    )
    (tmp_path / "ms").mkdir()
    monkeypatch.chdir(tmp_path)
    assert main(["ci", "install", "--subproject", "ms"]) == 0
