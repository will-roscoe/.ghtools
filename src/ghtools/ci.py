"""CI test-job commands and the push path filter."""

from __future__ import annotations

import re
import shlex
import shutil
import sys
from pathlib import Path

from .config import CONFIG_PATH, Config
from .gitutil import changed_files

STUB_PATH = ".github/workflows/ghtools.yml"
# Changes to the repo's ghtools settings or stub always run CI, whatever ci.paths says.
_ALWAYS = (CONFIG_PATH, STUB_PATH)


def python() -> str:
    """The interpreter CI jobs install into: the matrix Python on PATH, not ghtools's venv."""
    return shutil.which("python") or shutil.which("python3") or sys.executable


def install_command(cfg: Config) -> list[str]:
    return [python(), "-m", "pip", "install", *shlex.split(cfg.get("ci.install"))]


def test_command(cfg: Config) -> str:
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
