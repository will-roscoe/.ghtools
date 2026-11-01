"""CI test-job commands and the push path filter."""

from __future__ import annotations

import re
import shlex
import shutil
import sys
from collections.abc import Sequence
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


def _marker_expr(deselect: Sequence[str], existing: str | None = None) -> str:
    """A pytest ``-m`` expression leaving out `deselect`, and-joined with `existing` if given."""
    nots = " and ".join(f"not {m}" for m in deselect)
    return f"({existing}) and {nots}" if existing else nots


def _existing_marker(words: list[str]) -> str | None:
    """The `-m` expression pytest would use for `words`: the last of ``-m X``, ``-mX``, ``-m=X``."""
    found = None
    for i, w in enumerate(words):
        if w == "-m" and i + 1 < len(words):
            found = words[i + 1]
        elif w.startswith("-m") and not w.startswith("--") and len(w) > 2:
            found = w[2:].removeprefix("=")
    return found


def _append_marker(base: str, deselect: Sequence[str]) -> str:
    """`base` as written plus a final ``-m`` word; pytest honours the last ``-m``."""
    expr = _marker_expr(deselect, _existing_marker(shlex.split(base)))
    return f"{base} {shlex.join(['-m', expr])}"


def test_command(cfg: Config, subproject: str | None = None, deselect: Sequence[str] = ()) -> str:
    """The shell command CI runs for tests (the root's, or `subproject`'s).

    `deselect` lists pytest markers to leave out (see :func:`ghtools.selection.deselected`). They
    become one final ``-m 'not a and not b'`` word appended to ``ci.test`` as written, and-joined
    with the effective ``-m`` already in it (the last one, as pytest reads it). Empty `deselect`
    gives the command unchanged. A root ``ci.test`` that is not pytest is returned as is.
    """
    if subproject:
        # Run from the repo root so coverage paths stay repo-relative across legs.
        row = _subproject(cfg, subproject)
        parts = ["python", "-m", "pytest", row["tests"] or row["path"]]
        if row["package"]:
            parts += [f"--cov={row['package']}", "--cov-report=xml:coverage.xml"]
        parts += ["--junitxml=junit.xml", "-o", "junit_family=legacy"]
        if deselect:
            parts += ["-m", _marker_expr(deselect)]
        return " ".join(shlex.quote(p) for p in parts)
    base = cfg.get("ci.test")
    words = shlex.split(base)
    if not (words[:1] == ["pytest"] or words[:3] == ["python", "-m", "pytest"]):
        return base
    if deselect:
        base = _append_marker(base, deselect)
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


def skips_pr(cfg: Config, head: str) -> bool:
    """Is `head` (a pull request's head branch) listed in ci.skip-pr-heads?"""
    return bool(head) and head in cfg.get("ci.skip-pr-heads")


def code_changed(cfg: Config, root: Path, base: str | None) -> bool:
    """Did code (ci.paths) change since `base`? Unlike should_run, ghtools's own settings and
    stub don't count: they run CI but say nothing about coverage. Unknown base: True."""
    files = changed_files(base or "", root)
    if files is None:
        return True
    patterns = [glob_to_regex(p) for p in cfg.get("ci.paths")]
    return any(any(p.match(f) for p in patterns) for f in files)


def docs_changed(cfg: Config, root: Path, base: str | None) -> bool:
    """Should the docs job run? With docs.paths empty always; otherwise only if the change since
    `base` touches one of those globs (or ghtools's own settings or stub). Unknown base: True."""
    patterns = [glob_to_regex(p) for p in cfg.get("docs.paths")]
    if not patterns:
        return True
    files = changed_files(base or "", root)
    if files is None:
        return True
    return any(f in _ALWAYS or any(p.match(f) for p in patterns) for f in files)
