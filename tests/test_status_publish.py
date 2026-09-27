"""Publisher tests against a real bare remote."""

from __future__ import annotations

import subprocess

import pytest

from ghtools import config
from ghtools.errors import PreconditionError
from ghtools.status import publish as pub

CFG = config.from_dict({"status": {"enabled": True}})


def _with_remote(git_repo, tmp_path):
    git_repo.commit("feat: a", {"pyproject.toml": '[project]\nname = "x"\n'})
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git_repo.run("remote", "add", "origin", str(bare))
    git_repo.run("push", "-q", "origin", "main")
    return bare


def _frag(source, **values):
    return {"source": source, "updated": "t", "run": {}, **values}


def test_repo_slug_and_url(git_repo):
    git_repo.run("remote", "add", "origin", "git@github.com:will-roscoe/protonfs.git")
    assert pub.repo_slug(git_repo.path) == "will-roscoe/protonfs"
    git_repo.run("remote", "set-url", "origin", "https://github.com/will-roscoe/sph-dev")
    assert pub.repo_slug(git_repo.path) == "will-roscoe/sph-dev"
    assert (
        pub.status_url("o/r", "ghtools-status", "status.svg")
        == "https://github.com/o/r/raw/ghtools-status/status.svg"
    )


def test_first_publish_creates_orphan_branch(git_repo, tmp_path):
    bare = _with_remote(git_repo, tmp_path)
    head_before = git_repo.run("rev-parse", "HEAD")
    assert pub.publish(git_repo.path, {"ci": _frag("ci", coverage=90.0)}, CFG) == "published"
    parents = subprocess.run(
        ["git", "--git-dir", str(bare), "log", "--format=%P", "-1", "ghtools-status"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert parents.strip() == ""  # a root commit
    files = subprocess.run(
        ["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "ghtools-status"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert {"data/ci.json", "status.svg", "README.md"} <= set(files)
    assert git_repo.run("rev-parse", "HEAD") == head_before  # working branch untouched
    assert git_repo.run("status", "--porcelain") == ""


def test_unchanged_fragments_do_not_publish(git_repo, tmp_path):
    _with_remote(git_repo, tmp_path)
    pub.publish(git_repo.path, {"ci": _frag("ci", coverage=90.0)}, CFG)
    again = {"ci": {**_frag("ci", coverage=90.0), "updated": "later"}}
    assert pub.publish(git_repo.path, again, CFG) == "unchanged"


def test_publishes_merge_with_existing_fragments(git_repo, tmp_path):
    _with_remote(git_repo, tmp_path)
    pub.publish(git_repo.path, {"ci": _frag("ci", coverage=90.0)}, CFG)
    pub.publish(git_repo.path, {"docs": _frag("docs", build="passing")}, CFG)
    _, data = pub.read_published(git_repo.path, "ghtools-status")
    assert set(data) == {"ci", "docs"}
    assert data["ci"]["coverage"] == 90.0


def test_lost_race_rereads_and_keeps_the_winners_fragment(git_repo, tmp_path):
    _with_remote(git_repo, tmp_path)
    pub.publish(git_repo.path, {"ci": _frag("ci", coverage=90.0)}, CFG)
    calls = {"n": 0}

    def rival_publishes_first():
        if calls["n"] == 0:
            calls["n"] += 1
            pub.publish(git_repo.path, {"docs": _frag("docs", build="passing")}, CFG)

    result = pub.publish(
        git_repo.path,
        {"project": _frag("project", name="x")},
        CFG,
        before_push=rival_publishes_first,
        sleep=lambda _s: None,
    )
    assert result == "published"
    _, data = pub.read_published(git_repo.path, "ghtools-status")
    assert set(data) == {"ci", "docs", "project"}


def test_no_remote_is_a_clear_error(git_repo):
    git_repo.commit("feat: a")
    with pytest.raises(PreconditionError, match="no 'origin' remote"):
        pub.publish(git_repo.path, {"ci": _frag("ci")}, CFG)


def test_gives_up_after_attempts(git_repo, tmp_path):
    _with_remote(git_repo, tmp_path)
    counter = {"n": 0}

    def always_race():
        counter["n"] += 1
        pub.publish(git_repo.path, {"docs": _frag("docs", build=f"passing-{counter['n']}")}, CFG)

    with pytest.raises(PreconditionError, match="after 3 attempts"):
        pub.publish(
            git_repo.path,
            {"x": _frag("x", v=1)},
            CFG,
            attempts=3,
            before_push=always_race,
            sleep=lambda _s: None,
        )


def test_refuses_to_overwrite_a_branch_that_is_not_a_status_branch(git_repo, tmp_path):
    # Review C1: a status.branch naming a real branch must never be replaced.
    bare = _with_remote(git_repo, tmp_path)
    git_repo.run("push", "-q", "origin", "main:work")
    cfg = config.from_dict({"status": {"enabled": True, "branch": "work"}})
    before = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "work"], capture_output=True, text=True
    ).stdout
    with pytest.raises(PreconditionError, match="refusing to replace 'work'"):
        pub.publish(git_repo.path, {"ci": _frag("ci", coverage=1.0)}, cfg)
    after = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "work"], capture_output=True, text=True
    ).stdout
    assert before == after
