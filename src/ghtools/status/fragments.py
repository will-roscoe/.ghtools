"""Per-source status fragments (JSON-serialisable dicts), ported from protonfs collect.py."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..config import Config
from ..gitutil import latest_tag

VOLATILE = ("updated", "run")
STATES = ("ok", "warn", "fail")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_meta() -> dict[str, str]:
    env = os.environ.get
    server, repo, run_id = (
        env("GITHUB_SERVER_URL", "https://github.com"),
        env("GITHUB_REPOSITORY", ""),
        env("GITHUB_RUN_ID", ""),
    )
    return {
        "id": run_id,
        "url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "",
        "commit": env("GITHUB_SHA", ""),
    }


def meaningful(fragment: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fragment.items() if k not in VOLATILE}


def _status(outcome: str) -> str:
    return "passing" if outcome.strip().lower() == "success" else "failing"


def _gates_status(outcome: str) -> str:
    """Only a gate step that ran has a result; skipped or cancelled gates are unknown."""
    return {"success": "passing", "failure": "failing"}.get(outcome.strip().lower(), "")


def _version_key(version: str) -> tuple[str, tuple[int, ...], str]:
    """CPython first, numerically (3.9 < 3.13 < 3.13t), then other implementations (pypy3.10)."""
    match = re.fullmatch(r"(\D*)(\d+(?:\.\d+)*)(.*)", version)
    if not match:
        return (version, (), "")
    return (match.group(1), tuple(int(p) for p in match.group(2).split(".")), match.group(3))


def parse_junit(path: Path) -> dict[str, Any]:
    empty = {"passed": 0, "failed": 0, "skipped": 0, "total": 0, "duration_s": 0.0}
    if not Path(path).is_file():
        return empty
    total = failed = skipped = 0
    duration = 0.0
    for suite in ET.parse(path).getroot().iter("testsuite"):
        total += int(suite.get("tests", 0))
        failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        duration += float(suite.get("time", 0) or 0)
    return {
        "passed": max(total - failed - skipped, 0),
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "duration_s": round(duration, 2),
    }


def parse_coverage_xml(path: Path) -> float | None:
    if not Path(path).is_file():
        return None
    rate = ET.parse(path).getroot().get("line-rate")
    return round(float(rate) * 100, 1) if rate is not None else None


def parse_interrogate(output: str) -> float | None:
    match = re.search(r"actual:\s*([\d.]+)%", output)
    return float(match.group(1)) if match else None


def runner_platform(runner: str) -> tuple[str, str]:
    r = runner.lower()
    if r.startswith("macos"):
        intel = "intel" in r or re.match(r"macos-1[0-3]\b", r) is not None
        return ("macOS", "x86_64" if intel else "arm64")
    if r.startswith("windows"):
        return ("Windows", "arm64" if "arm" in r else "x86_64")
    return ("Linux", "arm64" if "arm" in r else "x86_64")


def leg(
    python: str,
    runner: str,
    outcome: str,
    gates_outcome: str,
    junit: dict[str, Any],
    coverage: float | None,
    canonical: bool,
    docstrings: float | None,
    subproject: str = "",
    lint_only: bool = False,
) -> dict[str, Any]:
    """One CI leg's results. A lint-only leg (the lint job of lint-only and umbrella repos) ran
    gates and no tests: it supplies the lint result and nothing else."""
    return {
        "python": python,
        "runner": runner,
        "status": _status(outcome),
        "gates": _gates_status(gates_outcome),
        "tests": junit,
        "coverage": coverage,
        "canonical": canonical,
        "docstrings": docstrings,
        "subproject": subproject,
        "lint_only": lint_only,
    }


def _sum_tests(results: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("passed", "failed", "skipped", "total")
    total = {k: sum(r.get(k, 0) for r in results) for k in keys}
    total["duration_s"] = round(sum(r.get("duration_s", 0) for r in results), 2)
    return total


def ci_fragment(
    legs: list[dict[str, Any]],
    gates: list[str],
    previous: dict[str, Any] | None = None,
    in_tree: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """The CI summary. Subproject results carry over from `previous` for subprojects this run
    didn't select, so the card shows each one's latest result instead of flapping."""
    if not legs:
        return {}
    lint_leg = next((lg for lg in legs if lg.get("lint_only")), None)
    legs = [lg for lg in legs if not lg.get("lint_only")]
    # Root legs only: in an umbrella the first matrix leg is some subproject.
    root_legs = [lg for lg in legs if not lg.get("subproject")]
    canonical = next((lg for lg in root_legs if lg.get("canonical")), None) or (
        root_legs[0] if root_legs else None
    )
    python: dict[str, str] = {}
    builds: dict[str, dict[str, str]] = {}
    for lg in legs:
        if python.get(lg["python"]) != "failing":
            python[lg["python"]] = lg["status"]
        os_name, arch = runner_platform(lg["runner"])
        slot = builds.setdefault(os_name, {})
        if slot.get(arch) != "failing":
            slot[arch] = lg["status"]
    ordered = sorted(python, key=_version_key)
    now = {
        lg["subproject"]: {"status": lg["status"], "tests": lg["tests"]}
        for lg in legs
        if lg.get("subproject")
    }
    carried = {
        name: result
        for name, result in ((previous or {}).get("subproject_legs") or {}).items()
        if name in in_tree
    }
    merged = {**carried, **now}
    if canonical:
        tests = canonical["tests"]
        coverage, docstrings = canonical.get("coverage"), canonical.get("docstrings")
        lint = canonical.get("gates") or "unknown"
    else:  # umbrella: every subproject's latest results; coverage lives in Codecov's flags
        tests = _sum_tests([r["tests"] for r in merged.values()])
        coverage = docstrings = None
        lint = "unknown"
    if lint_leg and lint in ("unknown", ""):
        lint = lint_leg["gates"] or "unknown"
    fragment = {
        "source": "ci",
        "updated": _now(),
        "run": run_meta(),
        "tests": tests,
        "coverage": coverage,
        "docstrings": docstrings,
        "lint": {"status": lint, "gates": gates},
        "python": [{"version": v, "status": python[v]} for v in ordered],
        "builds": builds,
    }
    if merged or in_tree:
        names = list(in_tree) or sorted(merged)
        fragment["subproject_legs"] = merged
        fragment["subprojects"] = [
            {"name": n, "status": merged[n]["status"] if n in merged else "not run"} for n in names
        ]
    return fragment


def docs_fragment(result: str, coverage_gate: str | None) -> dict[str, Any]:
    return {
        "source": "docs",
        "updated": _now(),
        "run": run_meta(),
        "build": _status(result),
        # The docs-coverage gate fails on any undocumented object, so passing means 100%.
        "coverage": (100.0 if coverage_gate == "success" else None) if coverage_gate else None,
    }


def project_fragment(
    cfg: Config,
    root: Path,
    version: str | None,
    open_issues: int | None,
    description: str = "",
) -> dict[str, Any]:
    root = Path(root)
    project: dict[str, Any] = {}
    if (root / "pyproject.toml").is_file():
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get(
            "project", {}
        )
    name = project.get("name") or root.name
    urls = {k.lower(): v for k, v in (project.get("urls") or {}).items()}
    tag = f"v{version}" if version else latest_tag(root)
    released = ""
    if tag:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%cs", tag], cwd=root, capture_output=True, text=True
        )
        released = proc.stdout.strip() if proc.returncode == 0 else ""
    publish = cfg.get("release.publish")
    return {
        "source": "project",
        "updated": _now(),
        "name": name,
        # Settings first, then pyproject, then the GitHub repository's own (HACS and lint repos).
        "description": cfg.get("status.description") or project.get("description") or description,
        "version": (version or (tag or "")).lstrip("v"),
        "released": released,
        "open_issues": open_issues,
        "links": {
            "pypi": f"https://pypi.org/project/{name}/" if "pypi" in publish else "",
            "docs": (urls.get("documentation") or "").rstrip("/"),
            "repo": urls.get("repository") or urls.get("homepage") or "",
        },
    }


def custom_fragment(
    name: str, label: str, items: list[tuple[str, str]], state: str
) -> dict[str, Any]:
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}, got {state!r}")
    return {
        "source": name,
        "label": label,
        "items": [{"label": k, "value": v} for k, v in items],
        "state": state,
        "updated": _now(),
        "run": run_meta(),
    }
