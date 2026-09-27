from __future__ import annotations

import os

import pytest

from ghtools import localtools
from ghtools.errors import PreconditionError


def test_install_pre_push_writes_executable_managed_hook(git_repo):
    git_repo.commit("feat: a")
    path = localtools.install_pre_push(git_repo.path)
    assert path == git_repo.path / ".git/hooks/pre-push"
    text = path.read_text()
    assert localtools.HOOK_MARKER in text
    assert "ghtools gates run --stage test --warn-only" in text
    assert os.access(path, os.X_OK)
    localtools.install_pre_push(git_repo.path)  # re-install over our own hook is fine


def test_refuses_to_overwrite_foreign_hook(git_repo):
    git_repo.commit("feat: a")
    hook = git_repo.path / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\npre-commit run\n")
    with pytest.raises(PreconditionError, match="not managed by ghtools"):
        localtools.install_pre_push(git_repo.path)


def test_reports_hook_that_cannot_be_made_executable(git_repo, monkeypatch):
    git_repo.commit("feat: a")
    monkeypatch.setattr(localtools.os, "access", lambda *_a, **_k: False)
    with pytest.raises(PreconditionError, match="could not be made executable"):
        localtools.install_pre_push(git_repo.path)


def test_hooks_path_respected(git_repo):
    git_repo.commit("feat: a")
    git_repo.run("config", "core.hooksPath", "githooks")
    assert localtools.install_pre_push(git_repo.path) == git_repo.path / "githooks/pre-push"


def test_resync_without_remote_reports_and_does_not_crash(git_repo):
    git_repo.commit("feat: a")
    lines: list[str] = []
    assert localtools.resync(git_repo.path, out=lines.append) == 0
    assert any("no origin remote" in line for line in lines)
