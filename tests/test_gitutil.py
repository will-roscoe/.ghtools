"""Tests for ghtools.gitutil against real temporary repositories."""

from __future__ import annotations

import subprocess

import pytest

from ghtools import gitutil
from ghtools.errors import PreconditionError


def test_release_tags_sorted_newest_first_with_prereleases(git_repo):
    git_repo.commit("feat: a")
    for tag in ["v1.0.0", "v1.1.0-alpha", "v1.1.0", "v1.1.0-rc.1", "v0.9.0"]:
        git_repo.tag(tag)
    assert gitutil.release_tags(git_repo.path) == [
        "v1.1.0",
        "v1.1.0-rc.1",
        "v1.1.0-alpha",
        "v1.0.0",
        "v0.9.0",
    ]
    assert gitutil.latest_tag(git_repo.path) == "v1.1.0"


def test_non_semver_tags_are_ignored(git_repo):
    git_repo.commit("feat: a")
    for tag in ["v1.1-20180405-11730-ge6c8899a8", "polaris", "v2026.0.1", "v0.2.0", "release-3"]:
        git_repo.tag(tag)
    assert gitutil.release_tags(git_repo.path) == ["v2026.0.1", "v0.2.0"]


def test_latest_tag_none_without_tags(git_repo):
    git_repo.commit("feat: a")
    assert gitutil.latest_tag(git_repo.path) is None
    assert gitutil.since_range(None) == "HEAD"
    assert gitutil.since_range("v1.0.0") == "v1.0.0..HEAD"


def test_log_messages_and_subjects(git_repo):
    git_repo.commit("feat: one")
    git_repo.tag("v0.1.0")
    git_repo.commit("fix: two\n\nbody line")
    git_repo.commit("docs: three")
    rng = gitutil.since_range("v0.1.0")
    messages = gitutil.log_messages(rng, git_repo.path)
    assert [m.strip() for m in messages] == ["docs: three", "fix: two\n\nbody line"]
    assert gitutil.log_subjects(rng, git_repo.path) == ["fix: two", "docs: three"]


def test_tag_exists(git_repo):
    git_repo.commit("feat: a")
    git_repo.tag("v1.0.0")
    assert gitutil.tag_exists("v1.0.0", git_repo.path)
    assert not gitutil.tag_exists("v9.9.9", git_repo.path)


def test_dirty_paths_reports_only_requested_paths(git_repo):
    git_repo.commit("feat: a", {"a.txt": "1", "b.txt": "1"})
    git_repo.write("a.txt", "2")
    git_repo.write("new.txt", "x")
    assert gitutil.dirty_paths(["a.txt", "new.txt", "b.txt"], git_repo.path) == [
        "a.txt",
        "new.txt",
    ]
    assert gitutil.dirty_paths(["b.txt"], git_repo.path) == []


def test_changed_files_and_unknown_base(git_repo):
    git_repo.commit("feat: a", {"a.py": "1"})
    base = git_repo.run("rev-parse", "HEAD").strip()
    git_repo.commit("docs: b", {"README.md": "x"})
    assert gitutil.changed_files(base, git_repo.path) == ["README.md"]
    assert gitutil.changed_files("0" * 40, git_repo.path) is None
    assert gitutil.changed_files("deadbeef", git_repo.path) is None


def test_work_tree_root_detection(git_repo):
    git_repo.commit("feat: a", {"sub/x.txt": "1"})
    assert gitutil.is_work_tree_root(git_repo.path)
    assert not gitutil.is_work_tree_root(git_repo.path / "sub")
    assert gitutil.repo_root(git_repo.path / "sub") == git_repo.path.resolve()


def test_branches(git_repo):
    git_repo.commit("feat: a")
    assert gitutil.current_branch(git_repo.path) == "main"
    assert gitutil.default_branch(git_repo.path) is None  # no origin/HEAD in a fresh repo


def test_git_failure_raises_precondition(tmp_path):
    with pytest.raises(PreconditionError, match="not a git repository"):
        gitutil.repo_root(tmp_path)


def test_tracked_files(git_repo):
    git_repo.commit("feat: a", {"x.sh": "echo", "y.py": "1", "d/z.sh": "echo"})
    assert gitutil.tracked_files(["*.sh"], git_repo.path) == ["d/z.sh", "x.sh"]


def test_default_branch_asks_the_remote_when_origin_head_is_unset(git_repo, tmp_path):
    # protonfs, intercom, ios2ha-camera and bash-helpers have no refs/remotes/origin/HEAD,
    # and their checkouts sit on next/develop/feature branches.
    git_repo.commit("feat: a")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git_repo.run("remote", "add", "origin", str(bare))
    git_repo.run("push", "-q", "origin", "main")
    git_repo.run("switch", "-q", "-c", "next")
    assert (
        gitutil.git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=git_repo.path, check=False)
        == ""
    )
    assert gitutil.default_branch(git_repo.path) == "main"
