from __future__ import annotations

import pytest

from ghtools import ci, config
from ghtools.cli import main
from ghtools.errors import GhtoolsError


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


SUB = config.from_dict(
    {
        "subprojects": [
            {
                "name": "scrapetool",
                "path": "scrapetool",
                "package": "scrapetool",
                "tests": "scrapetool/tests",
                "install": ["-e ./scrapetool[test]", "-e ./videoapp_ng[test]"],
                "setup": ["false", "true"],
            },
            {"name": "bare", "path": "bare", "package": "bare"},
        ]
    }
)


def test_subproject_install_and_test_commands():
    assert ci.install_command(SUB, "scrapetool")[-7:-2] == [
        "install",
        "-e",
        "./scrapetool[test]",
        "-e",
        "./videoapp_ng[test]",
    ]
    assert ci.install_command(SUB, "bare")[-5:-2] == ["install", "-e", "./bare"]
    assert ci.test_command(SUB, "scrapetool") == (
        "python -m pytest scrapetool/tests --cov=scrapetool --cov-report=xml:coverage.xml"
        " --junitxml=junit.xml -o junit_family=legacy"
    )


def test_unknown_subproject_is_an_error():
    with pytest.raises(GhtoolsError, match="no subproject named 'nope'"):
        ci.test_command(SUB, "nope")


def test_failing_setup_commands_warn_and_continue(tmp_path, capfd):
    # capfd, not capsys: the setup commands are subprocesses writing to fd 1.
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text(
        '[[subprojects]]\nname = "s"\npath = "s"\nsetup = ["false", "echo ran-second"]\n'
    )
    (tmp_path / "s").mkdir()
    assert main(["-C", str(tmp_path), "ci", "setup", "--subproject", "s"]) == 0
    out = capfd.readouterr().out
    assert "setup command failed (exit 1): false" in out and "ran-second" in out


def test_subproject_install_includes_needs_and_the_test_runner():
    # Review D-C3: a needed sibling must come from ./path, not PyPI; pytest must be present.
    cfg = config.from_dict(
        {
            "subprojects": [
                {"name": "a", "path": "a", "install": ["-e ./a[test]"]},
                {"name": "b", "path": "b", "needs": ["a"]},
            ]
        }
    )
    cmd = ci.install_command(cfg, "b")
    assert cmd[cmd.index("install") + 1 :] == [
        "-e",
        "./a[test]",
        "-e",
        "./b",
        "pytest",
        "pytest-cov",
    ]


def test_subproject_without_tests_passes_with_a_notice(tmp_path, capfd):
    # Review D-I5: pytest exits 5 when it collects nothing (python-dev's devlibs, epc-calc).
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text('[[subprojects]]\nname = "a"\npath = "a"\n')
    (tmp_path / "a").mkdir()
    (tmp_path / "a/README.md").write_text("no tests here\n")
    assert main(["-C", str(tmp_path), "ci", "test", "--subproject", "a"]) == 0
    assert "no tests collected for a" in capfd.readouterr().out


def test_root_setup_commands_run_after_install(tmp_path, capfd):
    # sph-dev patches its pinned sarracen after installing (src/setup/applysetup.py update).
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text('[ci]\nsetup = ["echo patched-root"]\n')
    assert ci.setup_commands(config.load(tmp_path), None) == ["echo patched-root"]
    assert main(["-C", str(tmp_path), "ci", "setup"]) == 0
    assert "patched-root" in capfd.readouterr().out
