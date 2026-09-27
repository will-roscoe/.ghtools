from __future__ import annotations

import pytest

from ghtools import ci, config


def test_test_command_adds_coverage_and_junit():
    cfg = config.from_dict({"ci": {"coverage": {"package": "src/protonfs", "floor": 80}}})
    assert ci.test_command(cfg) == (
        "pytest -q --cov=src/protonfs --cov-report=term-missing --cov-report=xml:coverage.xml"
        " --junitxml=junit.xml -o junit_family=legacy --cov-fail-under=80"
    )


def test_test_command_without_coverage():
    expected = "pytest -q --junitxml=junit.xml -o junit_family=legacy"
    assert ci.test_command(config.from_dict({})) == expected


def test_non_pytest_command_is_left_alone():
    assert ci.test_command(config.from_dict({"ci": {"test": "make test"}})) == "make test"


def test_install_command_splits_pip_args():
    cfg = config.from_dict({"ci": {"install": "pytest pyyaml voluptuous"}})
    assert ci.install_command(cfg)[-4:] == ["install", "pytest", "pyyaml", "voluptuous"]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**.py", "src/a/b.py", True),
        ("**.py", "a.py", True),
        ("pyproject.toml", "pyproject.toml", True),
        ("pyproject.toml", "sub/pyproject.toml", False),
        ("**/pyproject.toml", "sub/pyproject.toml", True),
        ("src/*.py", "src/a/b.py", False),
        ("docs/**", "docs/x/y.rst", True),
    ],
)
def test_glob_semantics_match_github_paths(pattern, path, expected):
    assert bool(ci.glob_to_regex(pattern).match(path)) is expected


def test_should_run_on_matching_change(git_repo):
    git_repo.commit("feat: a", {"a.py": "1"})
    base = git_repo.run("rev-parse", "HEAD").strip()
    git_repo.commit("docs: readme", {"README.md": "x"})
    cfg = config.from_dict({})
    assert ci.should_run(cfg, git_repo.path, base) is False
    git_repo.commit("fix: code", {"a.py": "2"})
    assert ci.should_run(cfg, git_repo.path, base) is True


def test_should_run_when_settings_or_stub_change(git_repo):
    git_repo.commit("feat: a", {"a.py": "1"})
    base = git_repo.run("rev-parse", "HEAD").strip()
    git_repo.commit("ci: settings", {".github/ghtools.toml": "branch = 'main'\n"})
    assert ci.should_run(config.from_dict({}), git_repo.path, base) is True


def test_should_run_unknown_base_is_true(git_repo):
    git_repo.commit("feat: a")
    assert ci.should_run(config.from_dict({}), git_repo.path, "0" * 40) is True
    assert ci.should_run(config.from_dict({}), git_repo.path, None) is True
