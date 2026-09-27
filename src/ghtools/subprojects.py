"""Which subprojects a change needs to test, ported from python-dev's select_projects.py."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .config import Config, export, in_tree

GLOBAL = (".github/ghtools.toml", ".github/workflows/ghtools.yml", "pyproject.toml", "codecov.yml")


def changed_between(root: Path, base: str, head: str) -> list[str] | None:
    """Files changed on `head` since it diverged from `base`; None when git can't tell."""
    if not base or set(base) == {"0"}:
        return None
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"], cwd=root, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line]


def select(cfg: Config, files: list[str] | None) -> list[str]:
    rows = in_tree(cfg)
    names = [r["name"] for r in rows]
    if files is None or any(f in GLOBAL for f in files):
        return names
    chosen = {
        r["name"] for r in rows if any(f.startswith(r["path"].rstrip("/") + "/") for f in files)
    }
    grew = True
    while grew:
        extra = {r["name"] for r in rows if r["name"] not in chosen and chosen & set(r["needs"])}
        chosen |= extra
        grew = bool(extra)
    return [n for n in names if n in chosen]


PLACEHOLDER = {"python": "none", "runner": "ubuntu-latest"}


def matrix(cfg: Config, root: Path, base: str, head: str) -> tuple[dict[str, Any], bool]:
    include = list(export(cfg)["matrix"]["include"]) if cfg.get("profile") == "python" else []
    rows = in_tree(cfg)
    if rows:
        picked = select(cfg, changed_between(Path(root), base, head))
        by_name = {r["name"]: r for r in rows}
        python, runner = cfg.get("ci.python")[-1], cfg.get("ci.runners")[0]
        include += [
            {
                "python": python,
                "runner": runner,
                "subproject": n,
                "package": by_name[n]["package"],
                "tests": by_name[n]["tests"],
            }
            for n in picked
        ]
    if not include:
        return {"include": [PLACEHOLDER]}, False
    return {"include": include}, True


def _gitmodules(root: Path) -> list[tuple[str, str]]:
    """(path, url) per submodule; git writes path before url, but accept either order."""
    text = (
        (root / ".gitmodules").read_text(encoding="utf-8")
        if (root / ".gitmodules").is_file()
        else ""
    )
    out: list[tuple[str, str]] = []
    for section in re.split(r"^\[submodule [^\]]*\]\s*$", text, flags=re.M)[1:]:
        path = re.search(r"^\s*path\s*=\s*(\S+)", section, re.M)
        url = re.search(r"^\s*url\s*=\s*(\S+)", section, re.M)
        if path and url:
            out.append((path.group(1), url.group(1)))
    return out


def submodule_status(root: Path) -> list[dict[str, str]]:
    """Each submodule's recorded pointer against its remote's HEAD (never prompts, 15 s cap)."""
    root = Path(root)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    rows: list[dict[str, str]] = []
    for path, url in _gitmodules(root):
        tree = subprocess.run(
            ["git", "ls-tree", "HEAD", path], cwd=root, capture_output=True, text=True
        )
        fields = tree.stdout.split()
        pointer = fields[2] if len(fields) >= 3 else ""
        try:
            remote = subprocess.run(
                ["git", "ls-remote", url, "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            head = (
                remote.stdout.split()[0] if remote.returncode == 0 and remote.stdout.strip() else ""
            )
        except subprocess.TimeoutExpired:
            head = ""
        if not head:
            state = "unknown (remote unreachable)"
        else:
            state = "up to date" if head == pointer else "pointer behind remote"
        rows.append(
            {
                "name": Path(path).name,
                "path": path,
                "pointer": pointer,
                "remote_head": head,
                "state": state,
            }
        )
    return rows
