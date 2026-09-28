"""CI test-job commands and the push path filter."""

from __future__ import annotations

import re
import shlex
import shutil
import sys
from pathlib import Path

from .config import CONFIG_PATH, Config
from .errors import GhtoolsError
from .gitutil import changed_files

STUB_PATH = ".github/workflows/ghtools.yml"
# Changes to the repo's ghtools settings or stub always run CI, whatever ci.paths says.
_ALWAYS = (CONFIG_PATH, STUB_PATH)


def python() -> str:
    """The interpreter CI jobs install into: the matrix Python on PATH, not ghtools's venv."""
    return shutil.which("python") or shutil.which("python3") or sys.executable


def _subproject(cfg: Config, name: str) -> dict:
    for row in cfg.get("subprojects"):
        if row["name"] == name:
            return row
    raise GhtoolsError(f"no subproject named {name!r} in [[subprojects]]")


def setup_commands(cfg: Config, subproject: str | None) -> list[str]:
    """Commands run after install: a subproject's own `setup`, or `ci.setup` for the root."""
    if subproject:
        return list(_subproject(cfg, subproject)["setup"])
    return list(cfg.get("ci.setup"))


def install_command(cfg: Config, subproject: str | None = None) -> list[str]:
    if subproject:
        # Needed siblings install from their ./path (not PyPI), dependencies first; the test
        # runner is always added since a subproject's extras may not include it.
        order: list[dict] = []

        def visit(name: str) -> None:
            row = _subproject(cfg, name)
            for need in row["needs"]:
                visit(need)
            if row not in order:
                order.append(row)

        visit(subproject)
        entries = [e for row in order for e in (row["install"] or [f"-e ./{row['path']}"])]
        words = [w for e in entries for w in shlex.split(e)]
        return [python(), "-m", "pip", "install", *words, "pytest", "pytest-cov"]
    return [python(), "-m", "pip", "install", *shlex.split(cfg.get("ci.install"))]


def test_command(cfg: Config, subproject: str | None = None) -> str:
    if subproject:
        # Run from the repo root so coverage paths stay repo-relative across legs.
        row = _subproject(cfg, subproject)
        parts = ["python", "-m", "pytest", row["tests"] or row["path"]]
        if row["package"]:
            parts += [f"--cov={row['package']}", "--cov-report=xml:coverage.xml"]
        parts += ["--junitxml=junit.xml", "-o", "junit_family=legacy"]
        return " ".join(shlex.quote(p) for p in parts)
    base = cfg.get("ci.test")
    words = shlex.split(base)
    if not (words[:1] == ["pytest"] or words[:3] == ["python", "-m", "pytest"]):
        return base
    extra: list[str] = []
    package = cfg.get("ci.coverage.package")
    if package:
        extra += [f"--cov={package}", "--cov-report=term-missing", "--cov-report=xml:coverage.xml"]
    extra += ["--junitxml=junit.xml", "-o", "junit_family=legacy"]
    if package and cfg.get("ci.coverage.floor"):
        extra.append(f"--cov-fail-under={cfg.get('ci.coverage.floor')}")
    return f"{base} {' '.join(shlex.quote(e) for e in extra)}"


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """GitHub `paths` glob: `**` crosses directories, `*` and `?` don't."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def should_run(cfg: Config, root: Path, base: str | None) -> bool:
    files = changed_files(base or "", root)
    if files is None:
        return True
    patterns = [glob_to_regex(p) for p in cfg.get("ci.paths")]
    return any(f in _ALWAYS or any(p.match(f) for p in patterns) for f in files)
