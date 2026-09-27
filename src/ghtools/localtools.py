"""Local-only helpers: the post-push resync (replaces protonfs's hook) and a pre-push hook."""

from __future__ import annotations

import os
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path

from .errors import PreconditionError
from .gitutil import git

HOOK_MARKER = "# managed by ghtools"
PRE_PUSH = f"""#!/usr/bin/env bash
{HOOK_MARKER}: reports gate results before a push but never blocks it
command -v ghtools >/dev/null 2>&1 || exit 0
ghtools gates run --stage test --warn-only
exit 0
"""


def _hooks_dir(root: Path) -> Path:
    rel = Path(git("rev-parse", "--git-path", "hooks", cwd=root).strip())
    return rel if rel.is_absolute() else Path(root) / rel


def install_pre_push(root: Path) -> Path:
    hooks = _hooks_dir(Path(root))
    hooks.mkdir(parents=True, exist_ok=True)
    path = hooks / "pre-push"
    if path.exists() and HOOK_MARKER not in path.read_text(encoding="utf-8", errors="replace"):
        raise PreconditionError(
            f"{path} exists and is not managed by ghtools; remove it, or add "
            "`ghtools gates run --stage test --warn-only` to it yourself"
        )
    path.write_text(PRE_PUSH, encoding="utf-8")
    path.chmod(0o755)
    if not os.access(path, os.X_OK):
        raise PreconditionError(
            f"{path} could not be made executable (is the repo on a filesystem without Unix "
            "permissions?); git ignores non-executable hooks. Point core.hooksPath at a "
            "directory on a Linux filesystem and re-run."
        )
    return path


def _editable_project(root: Path) -> str | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    name = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("name")
    if not name:
        return None
    proc = subprocess.run(["python", "-m", "pip", "show", name], capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if not line.startswith("Editable project location:"):
            continue
        if Path(line.split(":", 1)[1].strip()).resolve() == root.resolve():
            return name
    return None


def resync(root: Path, out: Callable[[str], None] = print) -> int:
    """After a push: fetch tags, fast-forward, and reinstall an editable install of this repo."""
    root = Path(root)
    if not git("remote", cwd=root, check=False).split():
        out("resync: no origin remote; nothing to sync")
        return 0
    before = git("describe", "--tags", "--always", cwd=root, check=False).strip()
    git("fetch", "--tags", "--prune", "--quiet", "origin", cwd=root, check=False)
    git("merge", "--ff-only", "--quiet", "@{u}", cwd=root, check=False)
    after = git("describe", "--tags", "--always", cwd=root, check=False).strip()
    name = _editable_project(root)
    if name:
        subprocess.run(["python", "-m", "pip", "install", "-e", ".", "--quiet"], cwd=root)
    out(f"resync: {before} -> {after}" + (f"; reinstalled {name}" if name else ""))
    return 0
