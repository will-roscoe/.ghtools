"""ghtools doctor: check a repo's ghtools setup locally and on GitHub."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ci import STUB_PATH
from .config import Config, load
from .errors import ConfigError
from .gitutil import submodules

GhJson = Callable[[list[str]], Any]
_BLOCKING_RULES = {"pull_request", "update", "required_status_checks"}


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


def gh_json(args: list[str]) -> Any | None:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def _stub_findings(root: Path) -> list[Finding]:
    from .scaffold import STUB_VERSION

    stub = root / STUB_PATH
    if not stub.is_file():
        return [Finding("fail", f"stub: {STUB_PATH} is missing; run ghtools init")]
    text = stub.read_text(encoding="utf-8")
    marker = re.search(r"# ghtools-stub: (\d+)", text)
    version = int(marker.group(1)) if marker else 0
    out: list[Finding] = []
    if version < STUB_VERSION:
        out.append(
            Finding(
                "warn",
                f"stub: contract v{version} is older than v{STUB_VERSION}; "
                "re-run ghtools init or ghtools sync",
            )
        )
    ref = re.search(r"pipeline\.yml@(\S+)", text)
    out.append(Finding("ok", f"stub: calls pipeline.yml@{ref.group(1) if ref else '?'}"))
    return out


def _ruleset_findings(cfg: Config, name: str, gh: GhJson) -> list[Finding]:
    branch = cfg.get("branch")
    out: list[Finding] = []
    for summary in gh(["api", f"repos/{name}/rulesets"]) or []:
        rs = gh(["api", f"repos/{name}/rulesets/{summary['id']}"]) or {}
        includes = rs.get("conditions", {}).get("ref_name", {}).get("include", [])
        targets = bool({"~DEFAULT_BRANCH", "~ALL", f"refs/heads/{branch}"} & set(includes))
        blocking = {r.get("type") for r in rs.get("rules", [])} & _BLOCKING_RULES
        if rs.get("enforcement") == "active" and targets and blocking:
            out.append(
                Finding(
                    "warn",
                    f"ruleset '{rs.get('name')}' blocks direct pushes to {branch} "
                    f"({', '.join(sorted(blocking))}); the release commit will be rejected. "
                    "Add GitHub Actions to its bypass list or set release.commit = false",
                )
            )
    return out


def _subproject_findings(cfg: Config, root: Path) -> list[Finding]:
    out: list[Finding] = []
    in_tree = [r["name"] for r in cfg.get("subprojects") if r["kind"] == "in-tree"]
    codecov = root / "codecov.yml"
    if in_tree and cfg.get("ci.coverage.flags") == "subproject" and codecov.is_file():
        # ghtools doesn't own codecov.yml; say what to paste rather than editing it.
        declared = set(
            re.findall(r"^\s*-\s*name:\s*(\S+)", codecov.read_text(encoding="utf-8"), re.M)
        )
        missing = [n for n in in_tree if n not in declared]
        if missing:
            rows = {r["name"]: r["path"] for r in cfg.get("subprojects")}
            yaml = "".join(f"\n    - name: {n}\n      paths: [{rows[n]}/]" for n in missing)
            out.append(
                Finding(
                    "warn",
                    f"codecov.yml has no individual_flags entry for: {', '.join(missing)}; "
                    f"add{yaml}",
                )
            )
    module_paths = {path for path, _ in submodules(root)}
    for git_dir in sorted(root.glob("*/.git")):
        rel = git_dir.parent.relative_to(root).as_posix()
        ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=root).returncode == 0
        if rel not in module_paths and not ignored:
            out.append(
                Finding(
                    "warn", f"{rel} is a nested git repo that is neither a submodule nor gitignored"
                )
            )
    return out


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _directive_findings(cfg: Config, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    dispatch = cfg.get("directives.dispatch")
    if dispatch:
        stub_text = (
            (root / STUB_PATH).read_text(encoding="utf-8") if (root / STUB_PATH).is_file() else ""
        )
        if "dispatch.yml@" not in stub_text:
            findings.append(
                Finding("fail", "directives: stub has no dispatch job; run `ghtools stub --write`")
            )
        for name, wf in dispatch.items():
            path = root / ".github/workflows" / wf
            if not path.is_file():
                findings.append(
                    Finding(
                        "fail", f"directives.dispatch.{name}: .github/workflows/{wf} does not exist"
                    )
                )
            elif "workflow_dispatch" not in path.read_text(encoding="utf-8"):
                findings.append(
                    Finding(
                        "warn",
                        f"directives.dispatch.{name}: {wf} has no workflow_dispatch trigger, "
                        "so it can't be started",
                    )
                )
    return findings


def run_checks(root: Path, gh: GhJson = gh_json) -> list[Finding]:
    root = Path(root)
    try:
        cfg = load(root)
    except ConfigError as exc:
        return [Finding("fail", str(exc))]
    out = [Finding("ok", "settings: .github/ghtools.toml is valid"), *_stub_findings(root)]
    out += _subproject_findings(cfg, root)
    out += _directive_findings(cfg, root)

    repo = gh(["repo", "view", "--json", "nameWithOwner,visibility"])
    if not repo:
        out.append(Finding("warn", "gh unavailable or not logged in: GitHub-side checks skipped"))
        return out
    name = repo["nameWithOwner"]

    if cfg.get("ci.coverage.codecov"):
        names = {s.get("name") for s in gh(["secret", "list", "--json", "name"]) or []}
        if "CODECOV_TOKEN" not in names:
            out.append(
                Finding(
                    "warn",
                    "codecov: ci.coverage.codecov is on but the CODECOV_TOKEN secret is missing",
                )
            )

    if cfg.get("release.enabled") and cfg.get("release.commit"):
        out += _ruleset_findings(cfg, name, gh)

    if cfg.get("docs.enabled") and cfg.get("docs.pages"):
        pages = gh(["api", f"repos/{name}/pages"])
        if not pages or pages.get("build_type") != "workflow":
            out.append(
                Finding(
                    "warn",
                    "GitHub Pages is not set to build from a workflow "
                    "(Settings → Pages → Source: GitHub Actions)",
                )
            )

    if "pypi" in cfg.get("release.publish"):
        out.append(
            Finding(
                "warn",
                "PyPI: confirm the trusted publisher names workflow `ghtools.yml` and "
                "environment `pypi` (not checkable from here)",
            )
        )

    if cfg.get("status.enabled"):
        out += _status_findings(cfg, name, gh)
    return out


def _status_findings(cfg: Config, name: str, gh: GhJson) -> list[Finding]:
    branch = cfg.get("status.branch")
    out: list[Finding] = []
    if gh(["api", f"repos/{name}/branches/{branch}"]) is None:
        out.append(
            Finding(
                "warn",
                f"status: the {branch} branch doesn't exist yet "
                "(the first default-branch run creates it)",
            )
        )
    blocking = {"non_fast_forward", "creation", "update"}
    for summary in gh(["api", f"repos/{name}/rulesets"]) or []:
        rs = gh(["api", f"repos/{name}/rulesets/{summary['id']}"]) or {}
        includes = set(rs.get("conditions", {}).get("ref_name", {}).get("include", []))
        rules = {r.get("type") for r in rs.get("rules", [])}
        targets_branch = {"~ALL", f"refs/heads/{branch}"} & includes
        if rs.get("enforcement") == "active" and targets_branch and blocking & rules:
            out.append(
                Finding(
                    "warn",
                    f"ruleset '{rs.get('name')}' blocks force-pushes to {branch}; exclude "
                    f"refs/heads/{branch} from it or status publishing will fail",
                )
            )
    return out
