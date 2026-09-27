"""Thin wrappers over the git CLI used by every other module."""

from __future__ import annotations

import os
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
    """The remote's default branch: origin/HEAD if set, else asked of the remote itself.

    Many clones have no refs/remotes/origin/HEAD (repos made with `git init` + `remote add`),
    and the checkout may sit on a feature branch, so the current branch is not a fallback here.
    """
    out = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=cwd, check=False).strip()
    if out:
        return out.removeprefix("origin/")
    # Never prompt (SSH passphrase, credentials) and never hang: init must stay non-interactive.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--symref", "origin", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return None
    remote = proc.stdout if proc.returncode == 0 else ""
    match = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", remote, re.M)
    return match.group(1) if match else None


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


def submodules(cwd: Path | str = ".") -> list[tuple[str, str]]:
    """(path, url) for each submodule in .gitmodules, parsed by git (comments ignored)."""
    if not (Path(cwd) / ".gitmodules").is_file():
        return []
    listing = git(
        "config",
        "-f",
        ".gitmodules",
        "--get-regexp",
        r"^submodule\..*\.(path|url)$",
        cwd=cwd,
        check=False,
    )
    found: dict[str, dict[str, str]] = {}
    for line in listing.splitlines():
        key, _, value = line.partition(" ")
        name, _, field = key.removeprefix("submodule.").rpartition(".")
        found.setdefault(name, {})[field] = value
    return [(f["path"], f.get("url", "")) for f in found.values() if "path" in f]
