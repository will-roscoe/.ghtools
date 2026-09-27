"""Subproject selection tests (rules from python-dev's select_projects.py)."""

from __future__ import annotations

import json

from ghtools import config, subprojects
from ghtools.cli import main

CFG = config.from_dict(
    {
        "subprojects": [
            {
                "name": "scrapetool",
                "path": "scrapetool",
                "package": "scrapetool",
                "tests": "scrapetool/tests",
            },
            {
                "name": "videoapp_ng",
                "path": "videoapp_ng",
                "package": "videoapp_ng",
                "tests": "videoapp_ng/tests",
                "needs": ["scrapetool"],
            },
            {
                "name": "eclipse_flow",
                "path": "eclipse-flow",
                "package": "eclipse_flow",
                "tests": "eclipse-flow/tests",
            },
            {"name": "coordpy", "path": "coordpy", "kind": "submodule"},
        ]
    }
)


def test_changed_subproject_and_its_dependants():
    assert subprojects.select(CFG, ["scrapetool/src/scrapetool/db.py"]) == [
        "scrapetool",
        "videoapp_ng",
    ]


def test_dependant_alone_does_not_pull_its_dependency():
    assert subprojects.select(CFG, ["videoapp_ng/src/x.py"]) == ["videoapp_ng"]


def test_docs_only_change_selects_nothing():
    assert subprojects.select(CFG, ["README.md", "docs/index.rst", "coordpy"]) == []


def test_global_file_or_unknown_diff_selects_everything():
    everything = ["scrapetool", "videoapp_ng", "eclipse_flow"]
    assert subprojects.select(CFG, ["codecov.yml"]) == everything
    assert subprojects.select(CFG, None) == everything


def test_path_prefix_must_be_a_directory_boundary():
    assert subprojects.select(CFG, ["scrapetool-legacy/x.py"]) == []


def test_zero_base_is_unknown(git_repo):
    git_repo.commit("feat: a")
    assert subprojects.changed_between(git_repo.path, "0" * 40, "HEAD") is None


UMBRELLA_TOML = 'profile = "umbrella"\nsubprojects = [{name = "a", path = "a", package = "a", tests = "a/tests"}]\n'


def test_umbrella_matrix_has_only_selected_legs_and_cli(git_repo, capsys, tmp_path, monkeypatch):
    git_repo.commit("feat: a", {".github/ghtools.toml": UMBRELLA_TOML, "a/x.py": "1"})
    base = git_repo.run("rev-parse", "HEAD").strip()
    git_repo.commit("fix: a", {"a/x.py": "2"})
    assert main(["-C", str(git_repo.path), "ci", "matrix", "--base", base, "--head", "HEAD"]) == 0
    leg = {
        "python": "3.12",
        "runner": "ubuntu-latest",
        "subproject": "a",
        "package": "a",
        "tests": "a/tests",
    }
    assert json.loads(capsys.readouterr().out) == {"matrix": {"include": [leg]}, "any": True}
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    main(
        [
            "-C",
            str(git_repo.path),
            "ci",
            "matrix",
            "--base",
            base,
            "--head",
            "HEAD",
            "--github-output",
        ]
    )
    assert out.read_text().splitlines()[1] == "any=true"


def test_nothing_selected_gives_the_placeholder(git_repo):
    git_repo.commit("feat: a", {".github/ghtools.toml": UMBRELLA_TOML, "a/x.py": "1"})
    base = git_repo.run("rev-parse", "HEAD").strip()
    git_repo.commit("docs: readme", {"README.md": "x"})
    cfg = config.load(git_repo.path)
    assert subprojects.matrix(cfg, git_repo.path, base, "HEAD") == (
        {"include": [subprojects.PLACEHOLDER]},
        False,
    )


def test_python_profile_keeps_root_legs(git_repo):
    git_repo.commit("feat: a")
    plain = config.from_dict({"ci": {"python": ["3.11", "3.12"]}})
    m, any_legs = subprojects.matrix(plain, git_repo.path, "", "HEAD")
    assert any_legs and m == config.export(plain)["matrix"]
    mixed = config.from_dict(
        {"ci": {"python": ["3.11", "3.12"]}, "subprojects": [{"name": "a", "path": "a"}]}
    )
    m, _ = subprojects.matrix(mixed, git_repo.path, "", "HEAD")  # unknown base: everything
    assert [leg.get("subproject") for leg in m["include"]] == [None, None, "a"]
    assert (
        config.export(mixed)["subprojects"] is True and config.export(plain)["subprojects"] is False
    )
