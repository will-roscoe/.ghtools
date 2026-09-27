"""Tests for ghtools.config."""

from __future__ import annotations

import json
import tomllib

import pytest

from ghtools import config
from ghtools.cli import main
from ghtools.errors import ConfigError


def test_defaults_fill_every_key():
    cfg = config.from_dict({})
    assert cfg.get("branch") == "main"
    assert cfg.get("profile") == "python"
    assert cfg.get("version.source") == "tag"
    assert cfg.get("version.zero-major-breaking") == "minor"
    assert cfg.get("release.publish") == ["github"]
    assert cfg.get("ci.coverage.floor") == 0
    assert cfg.get("ci.gate") == []
    assert cfg.get("docs.enabled") is False


def test_values_override_defaults():
    cfg = config.from_dict(
        {"branch": "master", "ci": {"python": ["3.9", "3.13"], "coverage": {"floor": 80}}}
    )
    assert cfg.get("branch") == "master"
    assert cfg.get("ci.python") == ["3.9", "3.13"]
    assert cfg.get("ci.coverage.floor") == 80
    assert cfg.get("ci.install") == "-e .[dev]"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"ci": {"pythn": ["3.12"]}}, "unknown key 'ci.pythn'"),
        ({"bogus": {}}, "unknown key 'bogus'"),
        ({"profile": "django"}, "profile: 'django' is not one of"),
        ({"release": {"commit": "yes"}}, "release.commit: expected true or false"),
        ({"ci": {"coverage": {"floor": True}}}, "ci.coverage.floor: expected an integer"),
        ({"ci": {"python": "3.12"}}, "ci.python: expected a list of strings"),
        ({"release": {"publish": ["npm"]}}, "release.publish: 'npm' is not one of"),
        ({"ci": {"gates": ["pylint"]}}, "ci.gates: unknown gate 'pylint'"),
        ({"version": {"source": "manifest"}}, "version.manifest: required when"),
        ({"release": {"publish": ["github-zip"]}}, "release.zip: required when"),
        (
            {"version": {"source": "pyproject"}, "release": {"commit": False}},
            "release.commit = false needs",
        ),
        ({"ci": {"test": "tox", "coverage": {"package": "src/x"}}}, "ci.coverage.package needs"),
        ({"ci": {"gate": [{"name": "x"}]}}, "ci.gate[0].run: required"),
        ({"ci": {"gate": [{"name": "ruff", "run": "true"}]}}, "ci.gate[0].name: 'ruff' clashes"),
    ],
)
def test_invalid_config_names_the_key(raw, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        config.from_dict(raw)


def test_custom_gates_parse_with_default_stage():
    cfg = config.from_dict(
        {
            "ci": {
                "gate": [
                    {"name": "secrets", "run": "python check.py", "after": "docs"},
                    {"name": "x", "run": "true"},
                ]
            }
        }
    )
    assert cfg.get("ci.gate") == [
        {"name": "secrets", "run": "python check.py", "after": "docs"},
        {"name": "x", "run": "true", "after": "test"},
    ]


def test_load_missing_file_mentions_init(tmp_path):
    with pytest.raises(ConfigError, match="ghtools init"):
        config.load(tmp_path)


def test_load_reports_toml_syntax_errors(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text("branch = \n")
    with pytest.raises(ConfigError, match=".github/ghtools.toml"):
        config.load(tmp_path)


def test_export_builds_matrix_with_extra_runners_on_newest_python():
    cfg = config.from_dict(
        {"ci": {"python": ["3.9", "3.13"], "runners": ["ubuntu-latest", "macos-latest"]}}
    )
    out = config.export(cfg)
    assert out["matrix"] == {
        "include": [
            {"python": "3.9", "runner": "ubuntu-latest"},
            {"python": "3.13", "runner": "ubuntu-latest"},
            {"python": "3.13", "runner": "macos-latest"},
        ]
    }
    assert out["release"]["pypi"] is False
    assert out["docs"] == {"enabled": False, "pages": False}


def test_dump_toml_round_trips_with_comments():
    values = {
        "branch": "master",
        "version.source": "pyproject",
        "ci.python": ["3.12"],
        "ci.coverage.floor": 80,
        "release.commit": True,
        "ci.gate": [{"name": "secrets", "run": "python c.py", "after": "docs"}],
    }
    text = config.dump_toml(values, {"version.source": 'static version = "0.28.0" in [project]'})
    assert '# static version = "0.28.0" in [project]' in text
    cfg = config.from_dict(tomllib.loads(text))
    assert cfg.get("branch") == "master"
    assert cfg.get("version.source") == "pyproject"
    assert cfg.get("ci.coverage.floor") == 80
    assert cfg.get("ci.gate")[0]["name"] == "secrets"


def test_cli_config_get_and_export(tmp_path, capsys):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text(
        'branch = "master"\n[docs]\napt = ["graphviz", "latexmk"]\n'
    )
    assert main(["-C", str(tmp_path), "config", "get", "branch"]) == 0
    assert capsys.readouterr().out == "master\n"
    assert main(["-C", str(tmp_path), "config", "get", "docs.apt"]) == 0
    assert capsys.readouterr().out == "graphviz\nlatexmk\n"
    assert main(["-C", str(tmp_path), "config", "get", "release.commit"]) == 0
    assert capsys.readouterr().out == "true\n"
    assert main(["-C", str(tmp_path), "config", "export", "--json"]) == 0
    out = capsys.readouterr().out
    assert "\n" not in out.strip()
    assert json.loads(out)["branch"] == "master"
    assert main(["-C", str(tmp_path), "config", "check"]) == 0


def test_cli_config_get_unknown_key(tmp_path, capsys):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text("")
    assert main(["-C", str(tmp_path), "config", "get", "nope"]) == 1
    assert "unknown key 'nope'" in capsys.readouterr().err


def test_default_install_is_editable_so_path_coverage_collects_data():
    # A non-editable install makes tests import from site-packages, so --cov=src/<pkg>
    # (a source path) records nothing: the first CI run of .ghtools hit exactly this.
    assert config.from_dict({}).get("ci.install") == "-e .[dev]"


def test_export_has_the_newest_python_for_single_version_jobs():
    assert (
        config.export(config.from_dict({"ci": {"python": ["3.13", "3.14"]}}))["python_latest"]
        == "3.14"
    )


@pytest.mark.parametrize("key", ["python", "runners"])
def test_empty_matrix_lists_are_rejected(key):
    # Review I8: an empty list passed `config check` and crashed or emptied the matrix later.
    with pytest.raises(ConfigError, match=rf"ci\.{key}: must not be empty"):
        config.from_dict({"ci": {key: []}})


def test_check_paths_names_missing_files(tmp_path):
    cfg = config.from_dict(
        {
            "profile": "hacs",
            "version": {"source": "manifest", "manifest": "custom_components/x/manifest.json"},
            "release": {"publish": ["github-zip"], "zip": "custom_components/x"},
        }
    )
    with pytest.raises(
        ConfigError, match=r"version\.manifest: custom_components/x/manifest\.json does not exist"
    ):
        config.check_paths(cfg, tmp_path)
    (tmp_path / "custom_components/x").mkdir(parents=True)
    (tmp_path / "custom_components/x/manifest.json").write_text("{}")
    config.check_paths(cfg, tmp_path)
    (tmp_path / "custom_components/x/manifest.json").unlink()
    (tmp_path / "custom_components/x").rmdir()
    cfg2 = config.from_dict({"release": {"publish": ["github-zip"], "zip": "nope"}})
    with pytest.raises(ConfigError, match=r"release\.zip: nope is not a directory"):
        config.check_paths(cfg2, tmp_path)
    cfg3 = config.from_dict({"docs": {"enabled": True, "dir": "docs"}})
    with pytest.raises(ConfigError, match=r"docs\.dir: docs/conf\.py does not exist"):
        config.check_paths(cfg3, tmp_path)


def test_config_check_cli_checks_paths(tmp_path, capsys):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text('[version]\nsource = "pyproject"\n')
    assert main(["-C", str(tmp_path), "config", "check"]) == 1
    assert "pyproject.toml does not exist" in capsys.readouterr().err


def test_status_section_defaults_and_validation():
    cfg = config.from_dict({})
    assert cfg.get("status.enabled") is False
    assert cfg.get("status.branch") == "ghtools-status"
    assert cfg.get("status.card") == ["tests", "coverage", "lint", "docs"]
    assert config.export(cfg)["status"] == {"enabled": False}
    with pytest.raises(ConfigError, match=r"status\.card: 'stars' is not one of"):
        config.from_dict({"status": {"card": ["stars"]}})
    with pytest.raises(ConfigError, match=r"status\.badges: 'size' is not one of"):
        config.from_dict({"status": {"badges": ["size"]}})


def test_status_extra_fragment_names_are_validated():
    assert config.from_dict({"status": {"extra": ["proton-drive"]}}).get("status.extra") == [
        "proton-drive"
    ]
    with pytest.raises(
        ConfigError, match=r"status\.extra: 'Bad Name' is not a valid fragment name"
    ):
        config.from_dict({"status": {"extra": ["Bad Name"]}})
