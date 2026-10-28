"""ci.reuse-pr-results: recording a tested tree and finding it again on the push."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import GitRepo  # noqa: I001
from ghtools import config, reuse
from ghtools.cli import main

LEG = {"python": "3.12", "runner": "ubuntu-latest", "status": "passing", "subproject": ""}
MATRIX = {"include": [{"python": "3.12", "runner": "ubuntu-latest"}]}


def _remote(tmp_path: Path, repo: GitRepo) -> None:
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "remote.git")], check=True)
    repo.run("remote", "add", "origin", str(tmp_path / "remote.git"))


def _legs(tmp_path: Path, leg: dict = LEG) -> Path:
    # download-artifact layout: one directory per artifact, the same file name in each
    d = tmp_path / "legs" / "ghtools-status-leg-3.12-ubuntu-latest"
    d.mkdir(parents=True)
    (d / "status-leg.json").write_text(json.dumps(leg))
    return tmp_path / "legs"


def test_defaults_change_nothing():
    cfg = config.from_dict({})
    assert cfg.get("ci.reuse-pr-results") is False
    assert cfg.get("docs.paths") == []
    assert config.export(cfg)["reuse"] is False


def test_recorded_tree_is_found_and_legs_restored(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    ref = reuse.record(git_repo.path, _legs(tmp_path))
    assert ref == reuse.ref_for(reuse.tree_sha(git_repo.path))
    out = tmp_path / "out"
    assert reuse.lookup(git_repo.path, out, MATRIX) is True
    assert json.loads((out / "ghtools-status-leg-3.12-ubuntu-latest.json").read_text()) == LEG


def test_different_tree_is_not_reused(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    reuse.record(git_repo.path, _legs(tmp_path))
    git_repo.commit("feat: b", {"a.py": "2"})  # the base moved: a different tree
    assert reuse.lookup(git_repo.path, tmp_path / "out", MATRIX) is False


def test_same_tree_other_history_is_reused(git_repo, tmp_path):
    # The merge commit differs from the tested merge ref, the tree does not.
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    reuse.record(git_repo.path, _legs(tmp_path))
    git_repo.run("commit", "-q", "--amend", "-m", "squash: a")
    assert reuse.lookup(git_repo.path, tmp_path / "out", MATRIX) is True


def test_legs_must_cover_the_matrix_and_pass(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    reuse.record(git_repo.path, _legs(tmp_path))
    wider = {"include": [*MATRIX["include"], {"python": "3.13", "runner": "ubuntu-latest"}]}
    assert reuse.lookup(git_repo.path, tmp_path / "o1", wider) is False
    assert reuse._covers([{**LEG, "status": "failing"}], MATRIX) is False


def test_no_record_or_no_remote_is_not_reused(git_repo, tmp_path):
    git_repo.commit("feat: a", {"a.py": "1"})
    assert reuse.lookup(git_repo.path, tmp_path / "out", MATRIX) is False  # no origin at all
    _remote(tmp_path, git_repo)
    assert reuse.lookup(git_repo.path, tmp_path / "out", MATRIX) is False


def test_drop_removes_the_record(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    reuse.record(git_repo.path, _legs(tmp_path))
    reuse.drop(git_repo.path)
    assert reuse.lookup(git_repo.path, tmp_path / "out", MATRIX) is False
    reuse.drop(git_repo.path)  # already gone: no error


def test_reuse_command_prints_true_false(git_repo, tmp_path, capsys):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    base = ["-C", str(git_repo.path), "ci"]
    assert main([*base, "record-tested", "--legs", str(_legs(tmp_path))]) == 0
    capsys.readouterr()
    args = [*base, "reuse", "--matrix", json.dumps(MATRIX), "--out", str(tmp_path / "out")]
    assert main(args) == 0
    assert capsys.readouterr().out.strip() == "true"
