"""Publish status files to an orphan branch using git plumbing only.

Never touches the working tree, the index or the checked-out branch. Each publish is a single
parentless commit pushed with --force-with-lease against the tip read in the same attempt, so a
concurrent publisher makes the push fail; the loser re-reads the winner's fragments and retries.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import PreconditionError
from .fragments import meaningful
from .render import render_all

_PRIVATE_REF = "refs/ghtools/status-published"
_BOT = {
    "GIT_AUTHOR_NAME": "github-actions[bot]",
    "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "github-actions[bot]",
    "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
}


def _git(
    root: Path,
    *args: str,
    input: bytes | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise PreconditionError(
            f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    return proc


def repo_slug(root: Path) -> str | None:
    url = _git(Path(root), "remote", "get-url", "origin", check=False).stdout.decode().strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def status_url(slug: str, branch: str, path: str) -> str:
    return f"https://github.com/{slug}/raw/{branch}/{path}"


def _remote_sha(root: Path, remote: str, branch: str) -> str:
    out = _git(root, "ls-remote", remote, f"refs/heads/{branch}").stdout.decode().split()
    return out[0] if out else ""


def read_published(
    root: Path, branch: str, remote: str = "origin"
) -> tuple[str, dict[str, dict[str, Any]]]:
    root = Path(root)
    sha = _remote_sha(root, remote, branch)
    if not sha:
        return "", {}
    _git(root, "fetch", "--quiet", remote, f"+refs/heads/{branch}:{_PRIVATE_REF}")
    names = _git(root, "ls-tree", "--name-only", _PRIVATE_REF, "data/").stdout.decode().split()
    data: dict[str, dict[str, Any]] = {}
    for path in names:
        if path.endswith(".json"):
            blob = _git(root, "show", f"{_PRIVATE_REF}:{path}").stdout
            data[Path(path).stem] = json.loads(blob)
    return sha, data


def _check_is_status_branch(root: Path, branch: str) -> None:
    """Never replace a branch ghtools didn't make: it must be one parentless status commit."""
    info = _git(root, "log", "-1", "--format=%P%x00%s", _PRIVATE_REF).stdout.decode()
    parents, _, subject = info.strip().partition("\0")
    if parents.strip() or not subject.startswith("chore(status):"):
        raise PreconditionError(
            f"refusing to replace {branch!r}: it isn't a ghtools status branch "
            "(it has history or other commits); set status.branch to a dedicated branch"
        )


def _write_tree(root: Path, files: dict[str, bytes]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        for path, content in sorted(files.items()):
            sha = _git(root, "hash-object", "-w", "--stdin", input=content).stdout.decode().strip()
            _git(root, "update-index", "--add", "--cacheinfo", f"100644,{sha},{path}", env=env)
        return _git(root, "write-tree", env=env).stdout.decode().strip()


def _commit_tree(root: Path, tree: str) -> str:
    identity = {} if _git(root, "config", "user.email", check=False).stdout.strip() else _BOT
    env = {**os.environ, **identity}
    message = "chore(status): update status card [skip ci]"
    return _git(root, "commit-tree", tree, "-m", message, env=env).stdout.decode().strip()


def _lost_race(stderr: str) -> bool:
    """A lease failure means someone published first; anything else (rules, auth) won't heal."""
    return "stale info" in stderr or "fetch first" in stderr


def publish(
    root: Path,
    fragments: dict[str, dict[str, Any]],
    cfg: Config,
    *,
    remote: str = "origin",
    attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
    before_push: Callable[[], None] | None = None,
) -> str:
    root = Path(root)
    if remote not in _git(root, "remote", check=False).stdout.decode().split():
        raise PreconditionError(f"no '{remote}' remote: nowhere to publish the status branch")
    branch = cfg.get("status.branch")
    for attempt in range(attempts):
        old, data = read_published(root, branch, remote)
        if old:
            _check_is_status_branch(root, branch)
        for name, fragment in fragments.items():
            # Unchanged apart from volatile keys: keep the published copy (no churn commits).
            if fragment and not (name in data and meaningful(data[name]) == meaningful(fragment)):
                data[name] = fragment
        tree = _write_tree(root, render_all(data, cfg, root))
        # Comparing whole trees also catches settings and template changes with the same data.
        if (
            old
            and tree == _git(root, "rev-parse", f"{_PRIVATE_REF}^{{tree}}").stdout.decode().strip()
        ):
            return "unchanged"
        commit = _commit_tree(root, tree)
        if before_push:
            before_push()
        pushed = _git(
            root,
            "push",
            "--quiet",
            f"--force-with-lease=refs/heads/{branch}:{old}",
            remote,
            f"{commit}:refs/heads/{branch}",
            check=False,
        )
        if pushed.returncode == 0:
            return "published"
        stderr = pushed.stderr.decode(errors="replace").strip()
        if not _lost_race(stderr):
            raise PreconditionError(f"pushing {branch} was rejected: {stderr}")
        if attempt < attempts - 1:
            sleep(random.uniform(1, 3) * (attempt + 1))
    raise PreconditionError(
        f"could not publish {branch} after {attempts} attempts (lost every race)"
    )
