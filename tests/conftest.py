"""Shared fixtures: a real throwaway git repository per test."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


class GitRepo:
    """A real git repository in a temp dir, with helpers for commits and tags."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._n = 0

    def run(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True
        )
        return proc.stdout

    def init(self) -> GitRepo:
        self.path.mkdir(parents=True)
        self.run("init", "-q", "-b", "main")
        self.run("config", "user.name", "Test")
        self.run("config", "user.email", "test@example.com")
        self.run("config", "commit.gpgsign", "false")
        self.run("config", "tag.gpgsign", "false")
        return self

    def write(self, rel: str, text: str) -> Path:
        path = self.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def commit(self, message: str, files: dict[str, str] | None = None) -> None:
        if files is None:
            self._n += 1
            files = {f"file{self._n}.txt": message}
        for rel, text in files.items():
            self.write(rel, text)
        self.run("add", "-A")
        self.run("commit", "-q", "--allow-empty", "-m", message)

    def tag(self, name: str) -> None:
        self.run("tag", "-a", name, "-m", name)


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path / "repo").init()
