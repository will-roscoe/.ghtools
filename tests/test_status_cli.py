from __future__ import annotations

import json
import subprocess

import pytest

from ghtools.cli import main

TOML = '[status]\nenabled = true\nextra = ["proton-drive"]\n'


def _repo(git_repo, tmp_path):
    git_repo.commit(
        "feat: a", {".github/ghtools.toml": TOML, "pyproject.toml": '[project]\nname = "x"\n'}
    )
    bare = tmp_path / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git_repo.run("remote", "add", "origin", str(bare))
    git_repo.run("push", "-q", "origin", "main")
    return git_repo


def test_leg_collect_publish_roundtrip(git_repo, tmp_path, capsys):
    repo = _repo(git_repo, tmp_path)
    (repo.path / "junit.xml").write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0" time="0.1"/></testsuites>'
    )
    legs = tmp_path / "legs"
    legs.mkdir()
    args = ["-C", str(repo.path), "status"]
    assert (
        main(
            [
                *args,
                "leg",
                "--python",
                "3.12",
                "--runner",
                "ubuntu-latest",
                "--outcome",
                "success",
                "--gates-outcome",
                "success",
                "--canonical",
                "--out",
                str(legs / "a.json"),
            ]
        )
        == 0
    )
    assert json.loads((legs / "a.json").read_text())["tests"]["passed"] == 3
    out = tmp_path / "out"
    assert main([*args, "collect", "ci", "--legs", str(legs), "--out", str(out / "ci.json")]) == 0
    assert (
        main(
            [*args, "collect", "project", "--version", "1.2.3", "--out", str(out / "project.json")]
        )
        == 0
    )
    assert (
        main(
            [
                *args,
                "set",
                "proton-drive",
                "--label",
                "PROTON DRIVE",
                "--item",
                "Pinned=0.8.0",
                "--item",
                "Latest=0.8.1",
                "--state",
                "warn",
                "--out",
                str(out / "proton-drive.json"),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main([*args, "publish", *(str(p) for p in sorted(out.glob("*.json")))]) == 0
    assert capsys.readouterr().out.strip() == "published"


def test_url_command(git_repo, capsys):
    git_repo.commit("feat: a", {".github/ghtools.toml": TOML})
    git_repo.run("remote", "add", "origin", "git@github.com:o/r.git")
    assert main(["-C", str(git_repo.path), "status", "url"]) == 0
    assert capsys.readouterr().out.strip() == "https://github.com/o/r/raw/ghtools-status/status.svg"


def test_collect_ci_ignores_non_leg_json(git_repo, tmp_path):
    # status.yml downloads every ghtools-status-* artifact, including the docs fragment.
    git_repo.commit("feat: a", {".github/ghtools.toml": TOML})
    legs = tmp_path / "in"
    (legs / "ghtools-status-docs").mkdir(parents=True)
    (legs / "ghtools-status-docs/docs.json").write_text('{"source": "docs", "build": "passing"}')
    (legs / "leg").mkdir()
    (legs / "leg/status-leg.json").write_text(
        '{"python": "3.12", "runner": "ubuntu-latest", "status": "passing", "gates": "passing",'
        ' "tests": {"passed": 1, "failed": 0, "skipped": 0, "total": 1, "duration_s": 0},'
        ' "coverage": null, "canonical": true, "docstrings": null}'
    )
    out = tmp_path / "ci.json"
    assert (
        main(
            [
                "-C",
                str(git_repo.path),
                "status",
                "collect",
                "ci",
                "--legs",
                str(legs),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert [p["version"] for p in json.loads(out.read_text())["python"]] == ["3.12"]


@pytest.mark.parametrize(("gates", "expected"), [("[]", None), ('["docs-coverage"]', 100.0)])
def test_docs_coverage_only_when_the_gate_measures_it(git_repo, tmp_path, gates, expected):
    # Review I4: a passing docs-gates step without docs-coverage must not become "100% documented".
    git_repo.commit("feat: a", {".github/ghtools.toml": f"[ci]\ngates = {gates}\n"})
    out = tmp_path / "docs.json"
    args = ["-C", str(git_repo.path), "status", "collect", "docs", "--result", "success"]
    assert main([*args, "--coverage-gate", "success", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["coverage"] == expected


def test_published_fragments_are_named_by_their_source(git_repo, tmp_path, capsys):
    # Review M15: `--out pd.json` published the fragment as "pd", which status.extra never shows.
    repo = _repo(git_repo, tmp_path)
    args = ["-C", str(repo.path), "status"]
    out = tmp_path / "pd.json"
    assert (
        main(
            [*args, "set", "proton-drive", "--label", "PD", "--item", "Pinned=1", "--out", str(out)]
        )
        == 0
    )
    assert main([*args, "publish", str(out)]) == 0
    listing = subprocess.run(
        [
            "git",
            "--git-dir",
            str(tmp_path / "r.git"),
            "ls-tree",
            "-r",
            "--name-only",
            "ghtools-status",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "data/proton-drive.json" in listing and "data/pd.json" not in listing


def test_collect_ci_carries_unselected_subprojects_from_the_status_branch(git_repo, tmp_path):
    toml = (
        TOML + '[[subprojects]]\nname = "a"\npath = "a"\n[[subprojects]]\nname = "b"\npath = "b"\n'
    )
    git_repo.commit(
        "feat: a", {".github/ghtools.toml": toml, "pyproject.toml": '[project]\nname = "x"\n'}
    )
    bare = tmp_path / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git_repo.run("remote", "add", "origin", str(bare))
    git_repo.run("push", "-q", "origin", "main")
    args = ["-C", str(git_repo.path), "status"]

    def leg(name, outcome, out):
        return main(
            [
                *args,
                "leg",
                "--python",
                "3.12",
                "--runner",
                "ubuntu-latest",
                "--outcome",
                outcome,
                "--subproject",
                name,
                "--out",
                str(out),
            ]
        )

    first, second = tmp_path / "l1", tmp_path / "l2"
    leg("a", "success", first / "a.json")
    leg("b", "failure", first / "b.json")
    main([*args, "collect", "ci", "--legs", str(first), "--out", str(tmp_path / "ci1.json")])
    main([*args, "publish", str(tmp_path / "ci1.json")])
    leg("a", "success", second / "a.json")
    main([*args, "collect", "ci", "--legs", str(second), "--out", str(tmp_path / "ci2.json")])
    subs = json.loads((tmp_path / "ci2.json").read_text())["subprojects"]
    assert subs == [{"name": "a", "status": "passing"}, {"name": "b", "status": "failing"}]


def test_leg_can_record_a_lint_only_leg(git_repo, tmp_path):
    repo = _repo(git_repo, tmp_path)
    out = tmp_path / "leg.json"
    args = ["-C", str(repo.path), "status", "leg", "--python", "3.12", "--runner", "ubuntu-latest"]
    assert (
        main(
            [
                *args,
                "--outcome",
                "success",
                "--gates-outcome",
                "success",
                "--lint-only",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert json.loads(out.read_text())["lint_only"] is True


def test_collect_ci_names_custom_gates_on_the_lint_row(git_repo, tmp_path):
    # bash-helpers' only gates are custom ([[ci.gate]] make-lint, make-validate).
    toml = (
        '[ci]\ngates = ["ruff"]\n\n[[ci.gate]]\nname = "make-lint"\nrun = "make lint"\n\n'
        '[[ci.gate]]\nname = "docs-check"\nrun = "true"\nafter = "docs"\n'
    )
    git_repo.commit("feat: a", {".github/ghtools.toml": toml})
    legs = tmp_path / "in"
    (legs / "leg").mkdir(parents=True)
    (legs / "leg/status-leg.json").write_text(
        '{"python": "3.12", "runner": "ubuntu-latest", "status": "passing", "gates": "passing",'
        ' "tests": {"passed": 0, "failed": 0, "skipped": 0, "total": 0, "duration_s": 0},'
        ' "coverage": null, "canonical": false, "docstrings": null, "lint_only": true}'
    )
    out = tmp_path / "ci.json"
    args = ["-C", str(git_repo.path), "status", "collect", "ci", "--legs", str(legs)]
    assert main([*args, "--out", str(out)]) == 0
    assert json.loads(out.read_text())["lint"] == {
        "status": "passing",
        "gates": ["ruff", "make-lint"],
    }
