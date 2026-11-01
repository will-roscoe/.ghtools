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
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
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


def record(
    root: Path, legs_dir: Path, remote: str = "origin", meta: dict[str, Any] | None = None
) -> str:
    """Push the record for HEAD's tree and return its ref. Legs: every `*.json` under `legs_dir`.

    `meta` says what the run covered: `{"source": "ci" | "local", "deselected": [markers]}`
    (`source` defaults to "ci"). It is stored as `meta.json` beside `legs/` with the ghtools
    version and an ISO timestamp added. Without a `deselected` key the file has none: the
    selection is unknown, and :func:`covers_selection` accepts such a record only in a repo with
    no rules. Pass `"deselected": []` to vouch for a full run.
    """
    root = Path(root)
    tree = tree_sha(root)
    entries = []
    for path in sorted(Path(legs_dir).rglob("*.json")):
        blob = _git_in(root, "hash-object", "-w", str(path)).strip()
        # download-artifact gives one directory per artifact, each holding the same file name
        name = path.name if path.parent == Path(legs_dir) else f"{path.parent.name}.json"
        entries.append(f"100644 blob {blob}\t{name}")
    legs = _git_in(root, "mktree", stdin="\n".join(entries) + ("\n" if entries else "")).strip()
    top_entries = f"040000 tree {legs}\tlegs\n"
    try:
        ghtools_version = _pkg_version("ghtools")
    except PackageNotFoundError:
        ghtools_version = "unknown"
    body = {
        "source": "ci",
        **(meta or {}),
        "ghtools": ghtools_version,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    meta_blob = _git_in(root, "hash-object", "-w", "--stdin", stdin=json.dumps(body)).strip()
    top = _git_in(
        root, "mktree", stdin=f"100644 blob {meta_blob}\tmeta.json\n{top_entries}"
    ).strip()
    commit = _git_in(root, "commit-tree", top, "-m", f"tested {tree}").strip()
    ref = ref_for(tree)
    _git_in(root, "push", "--quiet", "--force", remote, f"{commit}:{ref}")
    return ref


def recorded_source(root: Path, remote: str = "origin") -> str | None:
    """The `source` of the remote's record for HEAD's tree: None if unrecorded, "ci" also for a
    record with no or unreadable meta, else the stored value."""
    root = Path(root)
    try:
        _git_in(root, "fetch", "--quiet", "--no-tags", "--depth=1", remote, ref_for(tree_sha(root)))
    except PreconditionError:
        return None
    meta = _read_meta(root)
    return meta.get("source", "ci") if isinstance(meta, dict) else "ci"


def covers_selection(meta: dict[str, Any] | None, want: Sequence[str], has_rules: bool) -> bool:
    """Did the recorded run leave out nothing beyond the markers `want` leaves out?

    `meta` is the record's `meta.json`. None (a record from before metadata existed) and a meta
    without a `deselected` key (selection unknown) both cover only where the repo has no
    `ci.select` rules (`has_rules` False). `"deselected": []` is a vouched full run.
    """
    if meta is None or "deselected" not in meta:
        return not has_rules
    return set(meta["deselected"] or []) <= set(want)


def _covers(legs: list[dict[str, Any]], matrix: dict[str, Any], local: bool = False) -> bool:
    """Is every leg `matrix` would run recorded as passing (python, runner, subproject)?

    With `local`, only the subproject is compared: a local run has no CI python and runner.
    """
    if local:
        have_sub = {
            lg.get("subproject") or ""
            for lg in legs
            if not lg.get("lint_only") and lg.get("status") == "passing"
        }
        want_sub = {row.get("subproject") or "" for row in matrix.get("include", [])}
        return want_sub <= have_sub
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


_UNREADABLE = object()


def _read_meta(root: Path) -> dict[str, Any] | None | object:
    """The `meta.json` of the record just fetched (FETCH_HEAD): its dict, None for a record
    without one, or `_UNREADABLE` when it is present but not a JSON object."""
    if "meta.json" not in _git_in(root, "ls-tree", "--name-only", "FETCH_HEAD").split():
        return None
    try:
        meta = json.loads(_git_in(root, "show", "FETCH_HEAD:meta.json"))
    except json.JSONDecodeError:
        return _UNREADABLE
    return meta if isinstance(meta, dict) else _UNREADABLE


def _gates_passed(root: Path) -> bool:
    """Do the fetched record's legs show the lint gates passing (and none failing)?

    A leg's `gates` is "passing", "failing" or "" (the gates did not run in that leg), as CI's
    status legs record it. Unreadable legs count as not passed.
    """
    try:
        names = _git_in(root, "ls-tree", "--name-only", "FETCH_HEAD:legs").split()
        gates = [
            json.loads(_git_in(root, "show", f"FETCH_HEAD:legs/{n}")).get("gates")
            for n in names
            if n.endswith(".json")
        ]
    except (PreconditionError, json.JSONDecodeError, AttributeError):
        return False
    return "passing" in gates and "failing" not in gates


def _usable(
    root: Path,
    want: Sequence[str],
    has_rules: bool,
    accept_local: bool,
    needs_gates: bool = False,
) -> tuple[bool, bool]:
    """Is the fetched record's selection acceptable? Returns (usable, local source).

    With `needs_gates`, a local record is also refused unless its legs show the gates passing.
    """
    meta = _read_meta(root)
    if meta is not None and not isinstance(meta, dict):  # _UNREADABLE
        return False, False
    local = meta is not None and meta.get("source") == "local"
    if local and not accept_local:
        return False, local
    if local and needs_gates and not _gates_passed(root):
        return False, local
    return covers_selection(meta, want, has_rules), local


def lookup(
    root: Path,
    out_dir: Path,
    matrix: dict[str, Any],
    remote: str = "origin",
    want: Sequence[str] = (),
    has_rules: bool = False,
    accept_local: bool = False,
    needs_gates: bool = False,
) -> bool:
    """Is HEAD's tree recorded as tested, by passing legs covering `matrix`? If so, write the
    recorded legs to `out_dir` (for the status card) and return True. Any doubt returns False.

    The record must also cover the selection (:func:`covers_selection` with `want`, the markers
    this run would leave out, and `has_rules`: whether the repo has `ci.select` rules). A
    `source: "local"` record is refused unless `accept_local`; an accepted one covers the matrix
    by subproject only, and with `needs_gates` (the repo's lint gates apply) only if its legs
    record the gates passing.
    """
    root = Path(root)
    tree = tree_sha(root)
    try:
        _git_in(root, "fetch", "--quiet", "--no-tags", "--depth=1", remote, ref_for(tree))
        ok, local = _usable(root, want, has_rules, accept_local, needs_gates)
        if not ok:
            return False
        listing = _git_in(root, "ls-tree", "--name-only", "FETCH_HEAD:legs").split()
        legs = {
            name: json.loads(_git_in(root, "show", f"FETCH_HEAD:legs/{name}"))
            for name in listing
            if name.endswith(".json")
        }
    except (PreconditionError, json.JSONDecodeError):
        return False
    if not legs or not _covers(list(legs.values()), matrix, local=local):
        return False
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, leg in legs.items():
        (out / name).write_text(json.dumps(leg))
    return True


def is_tested(
    root: Path,
    rev: str,
    remote: str = "origin",
    want: Sequence[str] = (),
    has_rules: bool = False,
    accept_local: bool = False,
    needs_gates: bool = False,
) -> bool:
    """Is the tree of `rev` recorded under refs/ghtools/tested/, by a record covering the
    selection (see :func:`lookup`)? Any doubt (unknown rev, no remote, fetch failure) returns
    False."""
    try:
        tree = tree_sha(Path(root), rev)
        _git_in(root, "fetch", "--quiet", "--no-tags", "--depth=1", remote, ref_for(tree))
        return _usable(Path(root), want, has_rules, accept_local, needs_gates)[0]
    except (PreconditionError, subprocess.SubprocessError, OSError):
        return False


def drop(root: Path, remote: str = "origin") -> None:
    """Delete the record for HEAD's tree (it has been used); a missing record is not an error."""
    subprocess.run(
        ["git", "push", "--quiet", remote, f":{ref_for(tree_sha(Path(root)))}"],
        cwd=root,
        capture_output=True,
    )
