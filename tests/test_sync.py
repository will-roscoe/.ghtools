"""Stub re-rendering and cross-repo sync tests."""

from __future__ import annotations

import base64
import subprocess

from ghtools import scaffold, sync


def _consumer(git_repo, stub):
    git_repo.commit(
        "ci: ghtools",
        {
            ".github/ghtools.toml": 'branch = "master"\n[release]\npublish = ["github", "pypi"]\n',
            ".github/workflows/ghtools.yml": stub,
        },
    )
    return git_repo.path


def test_stub_version_and_ref():
    stub = scaffold.render_stub("main", pypi=False, ref="main")
    assert sync.stub_version(stub) == scaffold.STUB_VERSION
    assert sync.stub_ref(stub) == "main"
    assert sync.stub_version("name: x\n") == 0


def test_restub_keeps_the_pin_and_reads_the_repo_settings(git_repo, monkeypatch):
    old = scaffold.render_stub("master", pypi=True, ref="main")
    root = _consumer(git_repo, old)
    assert sync.restub(root) is None  # already current
    monkeypatch.setattr(scaffold, "STUB_VERSION", scaffold.STUB_VERSION + 1)
    new = sync.restub(root)
    assert sync.stub_version(new) == scaffold.STUB_VERSION  # marker follows the constant
    assert "pipeline.yml@main" in new
    assert "- master" in new and "publish-pypi:" in new


def test_every_stub_version_has_a_change_note():
    assert sorted(scaffold.STUB_CHANGES) == list(range(1, scaffold.STUB_VERSION + 1))


class FakeGh:
    def __init__(self, stub: str | None, open_prs=()):
        self.stub, self.open_prs, self.calls = stub, list(open_prs), []

    def __call__(self, args):
        self.calls.append(args)
        joined = " ".join(args)
        if joined.startswith("api repos/o/r/contents/.github/workflows/ghtools.yml"):
            return (
                None
                if self.stub is None
                else {"content": base64.b64encode(self.stub.encode()).decode()}
            )
        if joined.startswith("pr list"):
            return [{"number": n} for n in self.open_prs]
        if joined.startswith("pr create"):
            return {"url": "https://github.com/o/r/pull/9"}
        return None


def _remote(tmp_path, git_repo, stub, toml='branch = "main"\n'):
    git_repo.commit(
        "ci: ghtools", {".github/ghtools.toml": toml, ".github/workflows/ghtools.yml": stub}
    )
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(git_repo.path), str(bare)], check=True)
    return bare


def _clone_from(bare):
    def clone(repo, dest):
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{bare}", str(dest)], check=True
        )

    return clone


def _plain_git(args, cwd):
    # A fresh clone has no identity, and CI runners have no global one.
    ident = [
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
    ]
    return subprocess.run(
        ["git", *ident, *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def test_stale_stub_gets_a_branch_and_a_pr(tmp_path, git_repo, monkeypatch):
    old = scaffold.render_stub("main", pypi=False)
    bare = _remote(tmp_path, git_repo, old)
    monkeypatch.setattr(scaffold, "STUB_VERSION", scaffold.STUB_VERSION + 1)
    monkeypatch.setitem(scaffold.STUB_CHANGES, scaffold.STUB_VERSION, "adds a thing")
    gh = FakeGh(old)
    out = sync.sync_repo("o/r", gh=gh, clone=_clone_from(bare), run_git=_plain_git, dry_run=False)
    assert out.state == "proposed" and out.detail == "https://github.com/o/r/pull/9"
    branch = f"ghtools/stub-v{scaffold.STUB_VERSION}"
    pushed = subprocess.run(
        ["git", "--git-dir", str(bare), "show", f"{branch}:.github/workflows/ghtools.yml"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert sync.stub_version(pushed) == scaffold.STUB_VERSION
    create = next(c for c in gh.calls if c[:2] == ["pr", "create"])
    body = create[create.index("--body") + 1]
    assert "adds a thing" in body


def test_up_to_date_open_pr_and_missing_are_skips(tmp_path, git_repo, monkeypatch):
    current = scaffold.render_stub("main", pypi=False)
    assert (
        sync.sync_repo("o/r", gh=FakeGh(current), clone=None, run_git=None, dry_run=False).state
        == "up-to-date"
    )
    assert (
        sync.sync_repo("o/r", gh=FakeGh(None), clone=None, run_git=None, dry_run=False).state
        == "not-set-up"
    )
    monkeypatch.setattr(scaffold, "STUB_VERSION", scaffold.STUB_VERSION + 1)
    out = sync.sync_repo(
        "o/r", gh=FakeGh(current, open_prs=[4]), clone=None, run_git=None, dry_run=False
    )
    assert out.state == "already-proposed" and "#4" in out.detail


def test_invalid_settings_fail_that_repo_only(tmp_path, git_repo, monkeypatch):
    old = scaffold.render_stub("main", pypi=False)
    bare = _remote(tmp_path, git_repo, old, toml='profile = "django"\n')
    monkeypatch.setattr(scaffold, "STUB_VERSION", scaffold.STUB_VERSION + 1)
    gh = FakeGh(old)
    out = sync.sync_repo("o/r", gh=gh, clone=_clone_from(bare), run_git=_plain_git, dry_run=False)
    assert out.state == "failed" and "profile" in out.detail
    assert not any(c[:2] == ["pr", "create"] for c in gh.calls)


def test_dry_run_clones_nothing(monkeypatch):
    old = scaffold.render_stub("main", pypi=False)
    monkeypatch.setattr(scaffold, "STUB_VERSION", scaffold.STUB_VERSION + 1)
    out = sync.sync_repo("o/r", gh=FakeGh(old), clone=None, run_git=None, dry_run=True)
    assert out.state == "would-propose"


def test_cli_needs_repos_or_all_but_not_both(capsys):
    from ghtools.cli import main

    assert main(["sync"]) == 1
    assert main(["sync", "o/r", "--all"]) == 1
    assert "name repositories" in capsys.readouterr().err
