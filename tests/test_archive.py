from __future__ import annotations

import pytest

from ghtools import archive
from ghtools.errors import PreconditionError


def _github(root):
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/ci.yml").write_text("ci")
    (root / ".github/scripts").mkdir()
    (root / ".github/scripts/x.py").write_text("x")


def test_snapshot_copies_whole_github_dir_with_readme(tmp_path):
    _github(tmp_path)
    snap = archive.snapshot(tmp_path, "2026-09-27", "abc1234")
    assert snap == tmp_path / ".github/archive/pre-ghtools-2026-09-27"
    assert (snap / "workflows/ci.yml").read_text() == "ci"
    assert (snap / "scripts/x.py").read_text() == "x"
    readme = (snap / "README.md").read_text()
    assert "abc1234" in readme
    assert "git checkout pre-ghtools -- .github" in readme


def test_second_snapshot_same_day_gets_suffix_and_skips_archive(tmp_path):
    _github(tmp_path)
    first = archive.snapshot(tmp_path, "2026-09-27", "a")
    second = archive.snapshot(tmp_path, "2026-09-27", "b")
    assert second.name == "pre-ghtools-2026-09-27-2"
    assert not (second / "archive").exists()
    assert archive.latest_snapshot(tmp_path) == second
    assert first.exists()


def test_restore_puts_files_back_and_prune_removes_archive(tmp_path):
    _github(tmp_path)
    snap = archive.snapshot(tmp_path, "2026-09-27", "a")
    (tmp_path / ".github/workflows/ci.yml").unlink()
    (tmp_path / ".github/scripts/x.py").write_text("changed")
    restored = archive.restore(tmp_path, snap)
    assert sorted(restored) == [".github/scripts/x.py", ".github/workflows/ci.yml"]
    assert (tmp_path / ".github/workflows/ci.yml").read_text() == "ci"
    assert (tmp_path / ".github/scripts/x.py").read_text() == "x"
    assert archive.prune(tmp_path) is True
    assert not (tmp_path / ".github/archive").exists()
    assert archive.prune(tmp_path) is False


def test_refuses_when_github_readme_exists(tmp_path):
    _github(tmp_path)
    (tmp_path / ".github/README.md").write_text("mine")
    with pytest.raises(PreconditionError, match=r"\.github/README\.md exists"):
        archive.snapshot(tmp_path, "2026-09-27", "a")
