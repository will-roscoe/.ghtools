from __future__ import annotations

import json
import subprocess

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
