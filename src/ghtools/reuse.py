"""Reusing a pull request's test results on the push that merges it (ci.reuse-pr-results).

A tested tree is recorded as `refs/ghtools/tested/<tree sha>`: a commit whose tree holds the status
legs (`legs/*.json`) the passing pull request run produced for that tree. A push to the default
branch looks up its own tree there. The tree is the whole criterion: identical tree, identical
test input, so a merge whose base moved after the pull request ran (a different tree) re-tests.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import PreconditionError
from .gitutil import git

REF_PREFIX = "refs/ghtools/tested/"
_IDENTITY = {
    "GIT_AUTHOR_NAME": "ghtools",
    "GIT_AUTHOR_EMAIL": "ghtools@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "ghtools",
    "GIT_COMMITTER_EMAIL": "ghtools@users.noreply.github.com",
}


def tree_sha(root: Path, rev: str = "HEAD") -> str:
    return git("rev-parse", f"{rev}^{{tree}}", cwd=root).strip()


def ref_for(tree: str) -> str:
    return f"{REF_PREFIX}{tree}"


def _git_in(root: Path, *args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, **_IDENTITY},
    )
    if proc.returncode != 0:
        raise PreconditionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def record(root: Path, legs_dir: Path, remote: str = "origin") -> str:
    """Push the record for HEAD's tree and return its ref. Legs: every `*.json` under `legs_dir`."""
    root = Path(root)
    tree = tree_sha(root)
    entries = []
    for path in sorted(Path(legs_dir).rglob("*.json")):
        blob = _git_in(root, "hash-object", "-w", str(path)).strip()
        # download-artifact gives one directory per artifact, each holding the same file name
        name = path.name if path.parent == Path(legs_dir) else f"{path.parent.name}.json"
        entries.append(f"100644 blob {blob}\t{name}")
    legs = _git_in(root, "mktree", stdin="\n".join(entries) + ("\n" if entries else "")).strip()
    top = _git_in(root, "mktree", stdin=f"040000 tree {legs}\tlegs\n").strip()
    commit = _git_in(root, "commit-tree", top, "-m", f"tested {tree}").strip()
    ref = ref_for(tree)
    _git_in(root, "push", "--quiet", "--force", remote, f"{commit}:{ref}")
    return ref


def _covers(legs: list[dict[str, Any]], matrix: dict[str, Any]) -> bool:
    """Is every leg `matrix` would run recorded as passing (python, runner, subproject)?"""
    have = {
        (lg.get("python"), lg.get("runner"), lg.get("subproject") or "")
        for lg in legs
        if not lg.get("lint_only") and lg.get("status") == "passing"
    }
    want = {
        (row.get("python"), row.get("runner"), row.get("subproject") or "")
        for row in matrix.get("include", [])
    }
    return want <= have


def lookup(root: Path, out_dir: Path, matrix: dict[str, Any], remote: str = "origin") -> bool:
    """Is HEAD's tree recorded as tested, by passing legs covering `matrix`? If so, write the
    recorded legs to `out_dir` (for the status card) and return True. Any doubt returns False."""
    root = Path(root)
    tree = tree_sha(root)
    try:
        _git_in(root, "fetch", "--quiet", "--no-tags", "--depth=1", remote, ref_for(tree))
        listing = _git_in(root, "ls-tree", "--name-only", "FETCH_HEAD:legs").split()
        legs = {
            name: json.loads(_git_in(root, "show", f"FETCH_HEAD:legs/{name}"))
            for name in listing
            if name.endswith(".json")
        }
    except (PreconditionError, json.JSONDecodeError):
        return False
    if not legs or not _covers(list(legs.values()), matrix):
        return False
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, leg in legs.items():
        (out / name).write_text(json.dumps(leg))
    return True


def drop(root: Path, remote: str = "origin") -> None:
    """Delete the record for HEAD's tree (it has been used); a missing record is not an error."""
    subprocess.run(
        ["git", "push", "--quiet", remote, f":{ref_for(tree_sha(Path(root)))}"],
        cwd=root,
        capture_output=True,
    )
