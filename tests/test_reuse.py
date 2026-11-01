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


def _fetch_meta(repo, tree_ref: str) -> dict:
    repo.run("fetch", "--quiet", "origin", tree_ref)
    return json.loads(repo.run("show", "FETCH_HEAD:meta.json"))


def _record_old_shape(repo: GitRepo, tmp_path: Path) -> None:
    """A record as the released version wrote it: legs/ only, no meta.json."""
    blob = repo.run("hash-object", "-w", str(next(_legs(tmp_path).rglob("*.json")))).strip()
    legs = subprocess.run(
        ["git", "mktree"], cwd=repo.path, input=f"100644 blob {blob}\tleg.json\n",
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    top = subprocess.run(
        ["git", "mktree"], cwd=repo.path, input=f"040000 tree {legs}\tlegs\n",
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    commit = subprocess.run(
        ["git", "commit-tree", top, "-m", "old"], cwd=repo.path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    ref = reuse.ref_for(reuse.tree_sha(repo.path))
    repo.run("push", "--quiet", "--force", "origin", f"{commit}:{ref}")


def test_record_writes_meta(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    ref = reuse.record(
        git_repo.path, _legs(tmp_path), meta={"source": "local", "deselected": ["tui"]}
    )
    meta = _fetch_meta(git_repo, ref)
    assert meta["source"] == "local" and meta["deselected"] == ["tui"]
    assert meta["ghtools"] and meta["at"]
    assert git_repo.run("ls-tree", "--name-only", "FETCH_HEAD:legs").split()
    ref2 = reuse.record(git_repo.path, tmp_path / "legs")
    assert _fetch_meta(git_repo, ref2)["source"] == "ci"


def test_record_without_meta_still_reads(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    _record_old_shape(git_repo, tmp_path)
    assert reuse.lookup(git_repo.path, tmp_path / "out", MATRIX) is True
    assert reuse.is_tested(git_repo.path, "HEAD") is True


def test_subset_selection_covers():
    assert reuse.covers_selection({"deselected": []}, ["tui"], True) is True
    assert reuse.covers_selection({"deselected": ["tui"]}, ["tui"], True) is True


def test_missing_tests_do_not_cover():
    assert reuse.covers_selection({"deselected": ["tui"]}, [], True) is False


def test_old_record_with_rules_refused(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    _record_old_shape(git_repo, tmp_path)
    assert reuse.covers_selection(None, [], True) is False
    assert reuse.lookup(git_repo.path, tmp_path / "o", MATRIX, has_rules=True) is False
    assert reuse.is_tested(git_repo.path, "HEAD", has_rules=True) is False


def test_local_record_refused_by_default(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    reuse.record(git_repo.path, _legs(tmp_path), meta={"source": "local"})
    assert reuse.lookup(git_repo.path, tmp_path / "o", MATRIX) is False
    assert reuse.is_tested(git_repo.path, "HEAD") is False


def test_local_record_accepted_when_enabled(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    local_leg = {"status": "passing", "subproject": ""}  # no python or runner
    reuse.record(git_repo.path, _legs(tmp_path, local_leg), meta={"source": "local"})
    assert reuse.is_tested(git_repo.path, "HEAD", accept_local=True) is True
    assert reuse.lookup(git_repo.path, tmp_path / "o", MATRIX, accept_local=True) is True
    assert reuse.lookup(git_repo.path, tmp_path / "o2", MATRIX) is False


def test_defaults_match_old_behaviour(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    reuse.record(git_repo.path, _legs(tmp_path))  # default meta: ci, nothing deselected
    assert reuse.lookup(git_repo.path, tmp_path / "o", MATRIX) is True
    assert reuse.is_tested(git_repo.path, "HEAD") is True
    assert reuse.covers_selection(None, (), False) is True


def test_unknown_selection_is_not_a_full_run(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    ref = reuse.record(git_repo.path, _legs(tmp_path))
    assert "deselected" not in _fetch_meta(git_repo, ref)
    assert reuse.covers_selection({"source": "ci"}, [], True) is False
    assert reuse.lookup(git_repo.path, tmp_path / "o", MATRIX, has_rules=True) is False
    assert reuse.is_tested(git_repo.path, "HEAD", has_rules=True) is False
    assert reuse.lookup(git_repo.path, tmp_path / "o2", MATRIX) is True  # no rules: as before


def test_explicit_empty_selection_is_a_full_run(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    reuse.record(git_repo.path, _legs(tmp_path), meta={"deselected": []})
    assert reuse.lookup(git_repo.path, tmp_path / "o", MATRIX, has_rules=True) is True
    assert reuse.is_tested(git_repo.path, "HEAD", has_rules=True) is True


def test_accepted_local_record_must_still_cover_selection(git_repo, tmp_path):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1"})
    local_leg = {"status": "passing", "subproject": ""}
    meta = {"source": "local", "deselected": ["tui"]}
    reuse.record(git_repo.path, _legs(tmp_path, local_leg), meta=meta)
    assert reuse.is_tested(git_repo.path, "HEAD", want=[], accept_local=True) is False
    assert reuse.lookup(git_repo.path, tmp_path / "o", MATRIX, want=[], accept_local=True) is False
    assert reuse.is_tested(git_repo.path, "HEAD", want=["tui"], accept_local=True) is True


def test_reuse_command_fails_cleanly_on_malformed_config(git_repo, tmp_path, capsys):
    _remote(tmp_path, git_repo)
    git_repo.commit("feat: a", {"a.py": "1", ".github/ghtools.toml": "[ci\n"})
    args = ["-C", str(git_repo.path), "ci", "reuse", "--matrix", json.dumps(MATRIX)]
    assert main([*args, "--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert "ghtools.toml" in err and "Traceback" not in err
