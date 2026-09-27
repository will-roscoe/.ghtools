"""Stub re-rendering and cross-repo sync tests."""

from __future__ import annotations

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
