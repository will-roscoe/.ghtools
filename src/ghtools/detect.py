"""Inspect a repository and propose ghtools settings, each with the evidence behind it."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gitutil import current_branch, default_branch, release_tags, submodules, tracked_files


@dataclass
class Question:
    key: str
    prompt: str
    choices: list[str]
    default: str


@dataclass
class Detection:
    values: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    questions: list[Question] = field(default_factory=list)

    def set(self, key: str, value: Any, why: str) -> None:
        self.values[key] = value
        self.evidence[key] = why


_SPHINX = re.compile(r"(?:sphinx-build|python -m sphinx)\b[^\n]*")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _workflows(root: Path) -> dict[str, str]:
    """Live workflows, plus those archived by an earlier `init` (their evidence still counts)."""
    wf_dir = root / ".github/workflows"
    files = {p.name: p for p in [*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]}
    snapshots = sorted((root / ".github/archive").glob("pre-ghtools-*"))
    if snapshots:
        archived = snapshots[-1] / "workflows"
        for p in [*archived.glob("*.yml"), *archived.glob("*.yaml")]:
            files.setdefault(p.name, p)
    files.pop("ghtools.yml", None)
    return {name: _read(files[name]) for name in sorted(files)}


def _project_version(pyproject: str) -> str | None:
    try:
        return tomllib.loads(pyproject).get("project", {}).get("version")
    except tomllib.TOMLDecodeError:
        return None


def _branch(root: Path, d: Detection) -> None:
    branch = default_branch(root)
    if branch:
        d.set("branch", branch, "the remote's default branch")
    else:
        d.set("branch", current_branch(root) or "main", "current branch (no origin/HEAD)")


def _has_project_table(pyproject: str) -> bool:
    """Parsed, not substring-matched: a comment saying "no [project]" is not a package."""
    try:
        return "project" in tomllib.loads(pyproject)
    except tomllib.TOMLDecodeError:
        return False


def _profile(root: Path, pyproject: str, d: Detection) -> list[Path]:
    manifests = sorted(root.glob("custom_components/*/manifest.json"))
    if manifests and (root / "hacs.json").is_file():
        d.set("profile", "hacs", "custom_components/*/manifest.json + hacs.json")
    elif _has_project_table(pyproject) or (root / "setup.py").is_file():
        d.set("profile", "python", "pyproject.toml [project]" if pyproject else "setup.py")
    elif (root / ".gitmodules").is_file():
        d.set("profile", "umbrella", ".gitmodules with no root package")
    else:
        d.set("profile", "lint", "no Python package, HACS integration or submodules")
    return manifests


def _version(root: Path, pyproject: str, wf: str, manifests: list[Path], d: Detection) -> None:
    if d.values["profile"] == "hacs":
        rel = manifests[0].relative_to(root).as_posix()
        d.set("version.source", "manifest", f"{rel} (HACS integration)")
        d.set("version.manifest", rel, "HACS manifest")
        d.set("version.bump", "write", "ghtools computes the version from commits and writes it")
        return
    if re.search(r'\[tool\.hatch\.version\][^\[]*source\s*=\s*"vcs"', pyproject, re.S):
        d.set("version.source", "tag", '[tool.hatch.version] source = "vcs"')
    elif re.search(r"setuptools[_-]scm", pyproject):
        d.set("version.source", "tag", "setuptools_scm in pyproject.toml")
    elif (static := _project_version(pyproject)) is not None:
        d.set("version.source", "pyproject", f'static version = "{static}" in [project]')
        if re.search(r"commits imply|DECLARED", wf):
            d.set("version.bump", "require", "existing release workflow only verifies the version")
        else:
            d.set("version.bump", "write", "release commit rewrites [project].version")


def _release(root: Path, wf: str, manifests: list[Path], d: Detection) -> None:
    has_workflow = bool(re.search(r"gh release create|action-gh-release|compute_next_version", wf))
    if not release_tags(root) and not has_workflow:
        d.set("release.enabled", False, "no v* tags and no release workflow")
        return
    publish, why = ["github"], "GitHub release"
    if "pypa/gh-action-pypi-publish" in wf:
        publish, why = ["github", "pypi"], "pypa/gh-action-pypi-publish in workflows"
    zip_step = re.search(r"cd (custom_components/[\w-]+)\s*\n\s*zip -r", wf)
    if zip_step:
        publish, why = ["github-zip"], "zip step in release workflow"
        d.set("release.zip", zip_step.group(1), "zip step in release workflow")
    elif d.values["profile"] == "hacs":
        publish, why = ["github-zip"], "HACS integration"
        d.set("release.zip", manifests[0].parent.relative_to(root).as_posix(), "HACS integration")
    d.set("release.publish", publish, why)
    if not (root / "CHANGELOG.md").is_file():
        d.set("release.changelog", "", "no CHANGELOG.md")


def _ci(root: Path, pyproject: str, wfs: dict[str, str], wf: str, d: Detection) -> None:
    matrix = re.search(r"python-version:\s*\[([^\]]+)\]", wf)
    single = re.search(r"python-version:\s*[\"']?(3\.\d+)", wf)
    if matrix:
        d.set("ci.python", re.findall(r"(\d+\.\d+)", matrix.group(1)), "test matrix")
    elif single:
        d.set("ci.python", [single.group(1)], "python-version in workflows")
    runners: list[str] = []
    for runner in re.findall(r"runs-on:\s*([\w.-]+)", wf):
        if runner != "ubuntu-latest" and runner not in runners:
            runners.append(runner)
    if runners:
        d.set("ci.runners", ["ubuntu-latest", *runners], "runs-on values in workflows")
    extra = re.search(r"pip install (-e )?[\"']?(\.\[[\w,-]+\])[\"']?", wf)
    if extra:
        # Keep -e: ci.coverage.package is a source path, which only an editable install covers.
        d.set("ci.install", f"{extra.group(1) or ''}{extra.group(2)}", "pip install in workflows")
    else:
        for text in wfs.values():
            if "pytest" in text:
                raw = re.search(r"^\s*(?:-\s*)?run:\s*pip install (?!-e|\.|--)(.+)$", text, re.M)
                if raw:
                    d.set("ci.install", raw.group(1).strip(), "pip install in test workflow")
                    break
    package = re.search(r"--cov=([^\s$]+)", wf)
    if package:
        d.set("ci.coverage.package", package.group(1).rstrip("/"), "--cov in workflows")
    floor = re.search(r"--cov-fail-under[= ](\d+)", wf)
    if floor:
        d.set("ci.coverage.floor", int(floor.group(1)), "--cov-fail-under in workflows")
    if "codecov/codecov-action" in wf:
        d.set("ci.coverage.codecov", True, "codecov-action in workflows")
    gates: list[str] = []
    if "[tool.ruff" in pyproject or (root / "ruff.toml").is_file():
        gates.append("ruff")
        if "ruff format" in wf:
            gates.append("ruff-format")
    if "[tool.interrogate]" in pyproject and "interrogate" in wf:
        gates.append("interrogate")
    if d.values["profile"] in ("lint", "umbrella"):
        if tracked_files(["*.sh", "*.bash"], root):
            gates.append("shellcheck")
        if "yamllint" in wf:
            gates.append("yamllint")
    d.set("ci.gates", gates, "tool configuration and existing workflows")
    if (root / "Dockerfile").is_file() and "docker build" in wf:
        d.set("ci.docker", True, "Dockerfile + docker build step")


def _docs(root: Path, wfs: dict[str, str], wf: str, d: Detection) -> None:
    found = [p for p in ("docs", "doc") if (root / p / "conf.py").is_file()]
    if not found:
        return
    submodules = re.findall(r"path\s*=\s*(\S+)", _read(root / ".gitmodules"))
    clash = [s for s in submodules if s in ("docs", "doc") and s not in found]
    d.set("docs.enabled", True, f"{found[0]}/conf.py")
    d.set("docs.dir", found[0], f"{found[0]}/conf.py")
    if len(found) > 1 or clash:
        d.questions.append(
            Question(
                "docs.dir",
                f"Sphinx conf.py found in {found}"
                + (f" and a {clash[0]}/ submodule exists" if clash else "")
                + ". Build Sphinx docs from which directory?",
                [*found, "none"],
                found[0],
            )
        )
    apidoc = re.search(r"sphinx-apidoc[^\n]*?-o\s+\S+\s+(\S+)", wf)
    if apidoc:
        d.set("docs.apidoc", apidoc.group(1).rstrip("/"), "sphinx-apidoc in docs workflow")
    if "deploy-pages" in wf:
        d.set("docs.pages", True, "actions/deploy-pages in workflows")
    docs_wf = next((t for t in wfs.values() if _SPHINX.search(t)), "")
    apt = re.search(r"apt-get install -y ((?:[\w.+-]+ ?)+)", docs_wf)
    if apt:
        d.set("docs.apt", [p for p in apt.group(1).split() if not p.startswith("-")], "apt-get")
    install = re.search(r"pip install -e [\"']?(\.\[docs\])[\"']?", wf)
    if install:
        d.set("docs.install", install.group(1), "pip install in docs workflow")
    else:
        other = re.search(r"pip install (?!-e\b)((?:-r \S+)|[\w.\[\],=<>\- ]+?)\s*$", docs_wf, re.M)
        if other:
            d.set("docs.install", other.group(1).strip(), "pip install in docs workflow")
        elif not (root / "pyproject.toml").is_file():
            d.set("docs.install", "sphinx", "no pyproject.toml to install docs extras from")
    sphinx = _SPHINX.search(docs_wf)
    if sphinx:
        # `make X` steps before the Sphinx call generate sources it needs (bash-helpers: argdoc).
        before = docs_wf[: sphinx.start()]
        prebuild = [
            f"make {t}" for t in re.findall(r"^\s*(?:-\s*)?run:\s*make ([\w-]+)\s*$", before, re.M)
        ]
        if prebuild:
            d.set("docs.prebuild", prebuild, "make steps before the Sphinx build")
        strict = bool(re.search(r"(?:\s-W\b|--fail-on-warning)", sphinx.group(0)))
        d.set(
            "docs.strict",
            strict,
            f"{'warnings fail' if strict else 'warnings allowed'} in the existing Sphinx call",
        )


def _readme(root: Path, d: Detection) -> None:
    readme = _read(root / "README.md")
    found = re.findall(r"<!-- SYNC:(\w+) START - generated from (\S+?),", readme)
    # init migrates every SYNC block, so configure every one (an unconfigured block goes stale).
    blocks = [{"name": n, "source": s} for n, s in found if (root / s).is_file()]
    if blocks:
        d.set("readme.block", blocks, "SYNC markers in README.md")
        gates = d.values.get("ci.gates", [])
        if "readme-sync" not in gates:
            d.set("ci.gates", [*gates, "readme-sync"], "README sync markers")


def _subprojects(root: Path, d: Detection) -> None:
    rows: list[dict[str, Any]] = []
    modules = submodules(root)
    module_paths = {path for path, _ in modules}
    for sub in sorted(root.iterdir()):
        pyproject = sub / "pyproject.toml"
        if not sub.is_dir() or sub.name.startswith(".") or not pyproject.is_file():
            continue
        if sub.name in module_paths:  # a checked-out submodule: listed below, not built here
            continue
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        except tomllib.TOMLDecodeError:
            continue
        name = project.get("name")
        if not name:
            continue
        extras = project.get("optional-dependencies", {})
        pkgs = (
            [
                p.name
                for p in (sub / "src").iterdir()
                if p.is_dir() and (p / "__init__.py").is_file()
            ]
            if (sub / "src").is_dir()
            else []
        )
        row: dict[str, Any] = {
            "name": name.lower().replace("-", "_"),
            "path": sub.name,
            "package": pkgs[0] if len(pkgs) == 1 else name.lower().replace("-", "_"),
        }
        if (sub / "tests").is_dir():
            row["tests"] = f"{sub.name}/tests"
        extra = next((e for e in ("test", "tests", "dev") if e in extras), None)
        if extra:  # the leg needs the project's test dependencies, not just the package
            row["install"] = [f"-e ./{sub.name}[{extra}]"]
        rows.append(row)
    for path, _url in modules:
        rows.append(
            {"name": Path(path).name.lower().replace("-", "_"), "path": path, "kind": "submodule"}
        )
    if rows:
        d.set("subprojects", rows, "nested pyproject.toml projects and .gitmodules")
    if any(r.get("kind") != "submodule" for r in rows):
        d.set("ci.coverage.flags", "subproject", "one Codecov flag per in-tree subproject")
        d.set("status.rows", ["python", "builds", "subprojects"], "in-tree subprojects")


_DISPATCH = re.compile(r"gh workflow run (\S+\.ya?ml)")


def _directives(wfs: dict[str, str], d: Detection) -> None:
    for text in wfs.values():
        if "resolve_directives" not in text:
            continue
        files = [f for f in _DISPATCH.findall(text) if f not in ("ci.yml", "docs.yml")]
        d.set("directives.enabled", True, "a commit-directives workflow")
        d.set(
            "directives.dispatch",
            {Path(f).stem: f for f in files},
            "workflows it dispatches (ci and docs are built in)",
        )
        return


def detect(root: Path) -> Detection:
    root = Path(root)
    d = Detection()
    pyproject = _read(root / "pyproject.toml")
    wfs = _workflows(root)
    wf = "\n".join(wfs.values())
    _branch(root, d)
    manifests = _profile(root, pyproject, d)
    _version(root, pyproject, wf, manifests, d)
    _release(root, wf, manifests, d)
    _ci(root, pyproject, wfs, wf, d)
    _docs(root, wfs, wf, d)
    _readme(root, d)
    _subprojects(root, d)
    _directives(wfs, d)
    return d
