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
) -> dict[str, Any]:
    return {
        "python": python,
        "runner": runner,
        "status": _status(outcome),
        "gates": _status(gates_outcome) if gates_outcome else "",
        "tests": junit,
        "coverage": coverage,
        "canonical": canonical,
        "docstrings": docstrings,
    }


def ci_fragment(legs: list[dict[str, Any]], gates: list[str]) -> dict[str, Any]:
    if not legs:
        return {}
    canonical = next((lg for lg in legs if lg.get("canonical")), legs[0])
    python: dict[str, str] = {}
    builds: dict[str, dict[str, str]] = {}
    for lg in legs:
        if python.get(lg["python"]) != "failing":
            python[lg["python"]] = lg["status"]
        os_name, arch = runner_platform(lg["runner"])
        slot = builds.setdefault(os_name, {})
        if slot.get(arch) != "failing":
            slot[arch] = lg["status"]
    ordered = sorted(python, key=lambda v: [int(p) for p in v.split(".")])
    return {
        "source": "ci",
        "updated": _now(),
        "run": run_meta(),
        "tests": canonical["tests"],
        "coverage": canonical.get("coverage"),
        "docstrings": canonical.get("docstrings"),
        "lint": {"status": canonical.get("gates") or "unknown", "gates": gates},
        "python": [{"version": v, "status": python[v]} for v in ordered],
        "builds": builds,
    }


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
    cfg: Config, root: Path, version: str | None, open_issues: int | None
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
        "description": cfg.get("status.description") or project.get("description", ""),
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
