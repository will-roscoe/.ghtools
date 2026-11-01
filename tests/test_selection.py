from __future__ import annotations

import json
import subprocess

import pytest

from ghtools import ci, config, selection
from ghtools.cli import main
from ghtools.errors import ConfigError

RULE = {"marker": "tui", "changed": ["src/sph/phantom_live/**"]}


def _cfg(**ci_keys):
    return config.from_dict({"ci": {"select": [RULE], **ci_keys}})


def test_rule_not_met_deselects():
    assert selection.deselected(_cfg(), ["README.md"], "pull_request") == ["tui"]


def test_rule_met_keeps():
    files = ["README.md", "src/sph/phantom_live/app.py"]
    assert selection.deselected(_cfg(), files, "pull_request") == []


def test_unknown_changes_deselect_nothing():
    assert selection.deselected(_cfg(), None, "pull_request") == []


def test_full_on_event():
    cfg = _cfg(**{"select-full-on": ["push"]})
    assert selection.deselected(cfg, ["README.md"], "push") == []
    assert selection.deselected(cfg, ["README.md"], "pull_request") == ["tui"]


def test_no_rules_deselect_nothing():
    assert selection.deselected(config.from_dict({}), ["README.md"], "push") == []


def test_markers_are_sorted_and_unique():
    rows = [{"marker": m, "changed": ["x/**"]} for m in ("b", "a", "b")]
    cfg = config.from_dict({"ci": {"select": rows}})
    assert selection.deselected(cfg, ["y"], "") == ["a", "b"]


def test_command_appends_marker():
    cfg = config.from_dict({})
    assert "-m 'not tui'" in ci.test_command(cfg, deselect=["tui"])
    assert ci.test_command(cfg, deselect=[]) == ci.test_command(cfg)


def test_command_joins_existing_marker():
    cfg = config.from_dict({"ci": {"test": "pytest -q -m slow"}})
    cmd = ci.test_command(cfg, deselect=["tui"])
    assert cmd.startswith("pytest -q -m slow ")
    assert "-m '(slow) and not tui'" in cmd
    for form in ("-mslow", "-m=slow"):
        cfg2 = config.from_dict({"ci": {"test": f"pytest -q {form}"}})
        assert "-m '(slow) and not tui'" in ci.test_command(cfg2, deselect=["tui"])


def test_last_existing_marker_is_used():
    cfg = config.from_dict({"ci": {"test": "pytest -m slow -m fast"}})
    cmd = ci.test_command(cfg, deselect=["tui"])
    assert cmd.startswith("pytest -m slow -m fast ")
    assert "-m '(fast) and not tui'" in cmd


def test_test_string_is_not_requoted():
    cfg = config.from_dict({"ci": {"test": "pytest tests/*.py --basetemp=$TMP"}})
    assert ci.test_command(cfg, deselect=["tui"]).startswith("pytest tests/*.py --basetemp=$TMP ")


def test_compound_command_is_rejected():
    for cmd in ("pytest -q && echo done", "pytest; true", "pytest | tee out", "pytest || true"):
        with pytest.raises(ConfigError, match="ci.test.*ci.select|ci.select.*ci.test"):
            config.from_dict({"ci": {"test": cmd, "select": [RULE]}})
    config.from_dict({"ci": {"test": "pytest -q && echo done"}})  # fine without rules


def test_quoted_operator_in_marker_is_not_compound():
    cfg = config.from_dict({"ci": {"test": "pytest -m 'a or b|c;d'", "select": [RULE]}})
    assert "-m '(a or b|c;d) and not tui'" in ci.test_command(cfg, deselect=["tui"])


def test_two_markers():
    assert "-m 'not a and not b'" in ci.test_command(config.from_dict({}), deselect=["a", "b"])


def test_subproject_command_appends_marker():
    cfg = config.from_dict({"subprojects": [{"name": "a", "path": "a"}]})
    assert ci.test_command(cfg, "a", ["tui"]).endswith("-m 'not tui'")


def test_non_pytest_command_ignores_deselect():
    cfg = config.from_dict({"ci": {"test": "make test"}})
    assert ci.test_command(cfg, deselect=["tui"]) == "make test"


def test_select_needs_pytest():
    raw = {"ci": {"test": "make test", "select": [RULE]}}
    with pytest.raises(ConfigError, match="ci.select needs ci.test to run pytest"):
        config.from_dict(raw)


@pytest.mark.parametrize(
    "row", [{"marker": "not tui", "changed": ["a"]}, {"marker": "tui", "changed": []}]
)
def test_bad_rows_are_rejected(row):
    with pytest.raises(ConfigError, match=r"ci\.select\[0\]"):
        config.from_dict({"ci": {"select": [row]}})


def _repo(tmp_path, toml):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text(toml)
    return tmp_path


def _fake_run(monkeypatch, code):
    calls = []
    real = subprocess.run

    def run(argv, **kw):
        if argv[0] != "bash":
            return real(argv, **kw)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr("ghtools.cli.subprocess.run", run)
    return calls


def test_exit_5_passes_when_deselecting(tmp_path, monkeypatch, capfd):
    root = _repo(tmp_path, '[[ci.select]]\nmarker = "tui"\nchanged = ["a/**"]\n')
    calls = _fake_run(monkeypatch, 5)
    assert main(["-C", str(root), "ci", "test", "--deselect", '["tui"]']) == 0
    assert "-m 'not tui'" in calls[0][-1]
    assert "no tests collected" in capfd.readouterr().out


def test_exit_5_passes_for_subproject_when_deselecting(tmp_path, monkeypatch):
    root = _repo(tmp_path, '[[subprojects]]\nname = "a"\npath = "a"\n')
    _fake_run(monkeypatch, 5)
    assert main(["-C", str(root), "ci", "test", "--subproject", "a", "--deselect", '["x"]']) == 0


def test_root_exit_5_without_deselect_still_fails(tmp_path, monkeypatch):
    root = _repo(tmp_path, "")
    _fake_run(monkeypatch, 5)
    assert main(["-C", str(root), "ci", "test"]) != 0


def test_other_failures_still_fail_when_deselecting(tmp_path, monkeypatch):
    root = _repo(tmp_path, "")
    _fake_run(monkeypatch, 1)
    assert main(["-C", str(root), "ci", "test", "--deselect", '["tui"]']) != 0


def test_ci_select_prints_json(tmp_path, capsys):
    root = _repo(tmp_path, '[[ci.select]]\nmarker = "tui"\nchanged = ["a/**"]\n')
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(root), *cmd], check=True)
    (root / "b.txt").write_text("1")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "one"], check=True)
    base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    (root / "c.txt").write_text("2")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "two"], check=True)
    capsys.readouterr()
    assert main(["-C", str(root), "ci", "select", "--base", base, "--event", "pull_request"]) == 0
    assert json.loads(capsys.readouterr().out) == ["tui"]
    assert main(["-C", str(root), "ci", "select", "--event", "pull_request"]) == 0
    assert json.loads(capsys.readouterr().out) == []
