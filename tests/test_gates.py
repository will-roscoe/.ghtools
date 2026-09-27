from __future__ import annotations

import pytest

from ghtools import config, gates
from ghtools.errors import CheckFailed


def test_resolve_builtins_and_custom_by_stage():
    cfg = config.from_dict(
        {
            "docs": {"enabled": True, "dir": "doc"},
            "ci": {
                "gates": ["ruff", "docs-coverage"],
                "gate": [
                    {"name": "secrets", "run": "python check.py", "after": "docs"},
                    {"name": "make-validate", "run": "make validate"},
                ],
            },
        }
    )
    assert [g.name for g in gates.resolve(cfg, "test")] == ["ruff", "make-validate"]
    docs_stage = gates.resolve(cfg, "docs")
    assert [g.name for g in docs_stage] == ["docs-coverage", "secrets"]
    assert "sphinx-build -q -b coverage doc doc/_build/coverage" in docs_stage[0].run


def test_pip_requirements_are_deduplicated():
    cfg = config.from_dict({"ci": {"gates": ["ruff", "ruff-format", "yamllint"]}})
    assert gates.pip_requirements(gates.resolve(cfg, "test")) == ["ruff", "yamllint"]


def test_run_reports_all_failures(tmp_path):
    specs = [
        gates.GateSpec("ok", "true"),
        gates.GateSpec("bad1", "false"),
        gates.GateSpec("bad2", "exit 3"),
    ]
    with pytest.raises(CheckFailed, match="bad1, bad2"):
        gates.run(specs, tmp_path)
    assert gates.run(specs, tmp_path, warn_only=True) == ["bad1", "bad2"]


def test_run_executes_in_repo_root(tmp_path):
    (tmp_path / "marker").write_text("x")
    assert gates.run([gates.GateSpec("cwd", "test -f marker")], tmp_path) == []
