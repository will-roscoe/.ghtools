"""Thin wrappers over the git CLI used by every other module."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .errors import PreconditionError
from .version import semver_sort_key

# Only tags that are exactly vX.Y.Z[-alpha|beta|rc[.n]] count as releases. Anything else
# (calendar-ish "v1.1-20180405-…", "polaris", "release-3") is ignored, never parsed.
_SEMVER_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)(?:\.\d+)?)?$")


def git(*args: str, cwd: Path | str = ".", check: bool = True) -> str:
    """Run git and return stdout; raise PreconditionError on failure when `check`."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise PreconditionError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def repo_root(cwd: Path | str = ".") -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=cwd).strip()).resolve()


def is_work_tree_root(cwd: Path | str = ".") -> bool:
    try:
        return repo_root(cwd) == Path(cwd).resolve()
    except PreconditionError:
        return False


def release_tags(cwd: Path | str = ".") -> list[str]:
    """SemVer release tags, newest first by SemVer precedence."""
    tags = [t for t in git("tag", "--list", "v*", cwd=cwd).split() if _SEMVER_TAG.match(t)]
    return sorted(tags, key=semver_sort_key, reverse=True)


def latest_tag(cwd: Path | str = ".") -> str | None:
    tags = release_tags(cwd)
    return tags[0] if tags else None


def since_range(tag: str | None) -> str:
    return f"{tag}..HEAD" if tag else "HEAD"


def log_messages(rev_range: str, cwd: Path | str = ".") -> list[str]:
    """Full commit messages in `rev_range`, newest first."""
    out = git("log", "-z", "--format=%B", rev_range, cwd=cwd)
    return [m for m in out.split("\0") if m.strip()]


def log_subjects(rev_range: str, cwd: Path | str = ".") -> list[str]:
    """Commit subjects in `rev_range`, oldest first."""
    out = git("log", "-z", "--reverse", "--format=%s", rev_range, cwd=cwd)
    return [s for s in out.split("\0") if s.strip()]


def tag_exists(tag: str, cwd: Path | str = ".") -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def default_branch(cwd: Path | str = ".") -> str | None:
    out = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=cwd, check=False).strip()
    return out.removeprefix("origin/") or None


def current_branch(cwd: Path | str = ".") -> str | None:
    return git("branch", "--show-current", cwd=cwd, check=False).strip() or None


def dirty_paths(paths: Iterable[str], cwd: Path | str = ".") -> list[str]:
    """Which of `paths` (files or directories) have uncommitted or untracked changes."""
    paths = list(paths)
    if not paths:
        return []
    out = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths, cwd=cwd)
    entries = out.split("\0")
    dirty: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":  # renames/copies carry the source path as the next entry
            i += 1
        dirty.append(path)
    return sorted(dirty)


def changed_files(base: str, cwd: Path | str = ".") -> list[str] | None:
    """Files changed between `base` and HEAD, or None if `base` is unknown or all zeros."""
    if not base or set(base) == {"0"}:
        return None
    proc = subprocess.run(
        ["git", "diff", "--name-only", base, "HEAD"], cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line]


def tracked_files(patterns: Iterable[str], cwd: Path | str = ".") -> list[str]:
    out = git("ls-files", "--", *patterns, cwd=cwd)
    return sorted(line for line in out.splitlines() if line)
