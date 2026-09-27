"""Which subprojects a change needs to test, ported from python-dev's select_projects.py."""

from __future__ import annotations

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
