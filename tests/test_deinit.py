from __future__ import annotations

import pytest

from ghtools import scaffold
from ghtools.errors import PreconditionError

CI = "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest -q\n"


def _repo(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            ".github/workflows/ci.yml": CI,
        },
    )
    return git_repo


def _quiet(*_a, **_k):
    return None


def test_init_then_deinit_leaves_tree_clean(git_repo):
    repo = _repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, out=_quiet)
    assert repo.run("status", "--porcelain") != ""
    assert scaffold.deinit_repo(repo.path, out=_quiet) == 0
    assert repo.run("status", "--porcelain") == ""


def test_deinit_after_commit_restores_old_files(git_repo):
    repo = _repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, out=_quiet)
    repo.run("add", "-A")
    repo.run("commit", "-q", "-m", "ci: switch to ghtools")
    scaffold.deinit_repo(repo.path, out=_quiet)
    assert (repo.path / ".github/workflows/ci.yml").read_text() == CI
    assert not (repo.path / ".github/ghtools.toml").exists()
    assert not (repo.path / ".github/workflows/ghtools.yml").exists()
    assert not (repo.path / ".github/archive").exists()


def test_deinit_falls_back_to_tag(git_repo):
    repo = _repo(git_repo)
    repo.run("tag", "pre-ghtools")
    scaffold.init_repo(repo.path, yes=True, archive_mode="none", out=_quiet)
    scaffold.deinit_repo(repo.path, out=_quiet)
    assert (repo.path / ".github/workflows/ci.yml").read_text() == CI
    assert repo.run("status", "--porcelain") == ""


def test_deinit_without_snapshot_or_tag_refuses(git_repo):
    repo = _repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, archive_mode="none", out=_quiet)
    with pytest.raises(PreconditionError, match="nothing to restore from"):
        scaffold.deinit_repo(repo.path, out=_quiet)


def test_deinit_refuses_when_settings_edited_after_commit(git_repo):
    repo = _repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, out=_quiet)
    repo.run("add", "-A")
    repo.run("commit", "-q", "-m", "ci: switch")
    (repo.path / ".github/ghtools.toml").write_text("# edited\n")
    with pytest.raises(PreconditionError, match="uncommitted changes"):
        scaffold.deinit_repo(repo.path, out=_quiet)


def test_deinit_dry_run(git_repo):
    repo = _repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, out=_quiet)
    before = repo.run("status", "--porcelain")
    lines: list[str] = []
    scaffold.deinit_repo(repo.path, dry_run=True, out=lines.append)
    assert repo.run("status", "--porcelain") == before
    assert any("restore .github/workflows/ci.yml" in line for line in lines)


def test_deinit_refuses_to_overwrite_new_untracked_work(git_repo):
    # Review I5: an untracked file at a restore target is someone's new work.
    repo = _repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, out=_quiet)
    repo.run("add", "-A")
    repo.run("commit", "-q", "-m", "ci: switch")
    (repo.path / ".github/workflows/ci.yml").write_text("MY NEW WORK\n")
    with pytest.raises(PreconditionError, match=r"uncommitted changes.*\.github/workflows/ci\.yml"):
        scaffold.deinit_repo(repo.path, out=_quiet)
    assert (repo.path / ".github/workflows/ci.yml").read_text() == "MY NEW WORK\n"


def test_deinit_accepts_an_untracked_file_identical_to_the_archived_copy(git_repo):
    repo = _repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, out=_quiet)
    repo.run("add", "-A")
    repo.run("commit", "-q", "-m", "ci: switch")
    (repo.path / ".github/workflows/ci.yml").write_text(CI)
    assert scaffold.deinit_repo(repo.path, out=_quiet) == 0
