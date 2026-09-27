"""ghtools init: detect settings, archive .github, write the settings file and stub."""

from __future__ import annotations

import difflib
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import archive
from .ci import STUB_PATH
from .config import CONFIG_PATH, dump_toml, from_dict
from .detect import Detection, detect
from .errors import PreconditionError
from .gitutil import dirty_paths, git, is_work_tree_root, tag_exists
from .templates import render

STUB_VERSION = 1
STUB_REF = "v1"

# What ghtools now does for a workflow that contains one of these (checked in order).
_REPLACED: list[tuple[str, str]] = [
    (r"compute_next_version|gh release create|softprops/action-gh-release", "release"),
    (r"sphinx-build", "docs"),
    (r"hacs/action|hassfest", "HACS validation"),
    (r"\bpytest\b|ruff check|ruff-action|ruff format", "CI tests and lint"),
    (r"yamllint|shellcheck", "lint gates"),
    (r"\.github/badges/version\.svg", "version badge"),
]
# Workflows doing something ghtools doesn't, which must survive init.
_KEEP = r"claude-code-action|@github/copilot|resolve_directives|workflow_run:|pull_request_target:"
REPLACED_SCRIPTS = [
    ".github/scripts/compute_next_version.py",
    ".github/scripts/finalize_changelog.py",
]


def classify_workflow(text: str) -> tuple[str, str]:
    keep = re.search(_KEEP, text)
    if keep:
        return "keep", f"not covered by ghtools ({keep.group(0).rstrip(':')})"
    for pattern, what in _REPLACED:
        if re.search(pattern, text):
            return "replace", f"ghtools {what}"
    return "keep", "not covered by ghtools"


def render_stub(branch: str, pypi: bool, ref: str = STUB_REF) -> str:
    return render("stub.yml.j2", branch=branch, pypi=pypi, ref=ref)


@dataclass
class Plan:
    write: dict[str, str] = field(default_factory=dict)
    remove: list[str] = field(default_factory=list)
    keep: dict[str, str] = field(default_factory=dict)
    archive: bool = False
    notes: list[str] = field(default_factory=list)


def _apply_answers(d: Detection, answers: dict[str, str]) -> None:
    for key, answer in answers.items():
        if key == "docs.dir" and answer == "none":
            d.set("docs.enabled", False, "chosen at init")
            d.values.pop("docs.dir", None)
        else:
            d.set(key, answer, "chosen at init")


def _nested(values: dict[str, Any]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for dotted, value in values.items():
        parts = dotted.split(".")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return tree


def make_plan(
    root: Path, answers: dict[str, str], keep_old: bool, archive_mode: str, ref: str
) -> tuple[Plan, Detection]:
    d = detect(root)
    _apply_answers(d, answers)
    cfg = from_dict(_nested(d.values))  # validates the proposal before anything is written
    plan = Plan(archive=archive_mode == "snapshot" and (root / ".github").is_dir())
    plan.write[CONFIG_PATH] = dump_toml(d.values, d.evidence)
    pypi = "pypi" in cfg.get("release.publish")
    plan.write[STUB_PATH] = render_stub(cfg.get("branch"), pypi, ref)
    wf_dir = root / ".github/workflows"
    for path in sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]):
        rel = path.relative_to(root).as_posix()
        if rel == STUB_PATH:
            continue
        verdict, why = classify_workflow(path.read_text(encoding="utf-8", errors="replace"))
        if verdict == "replace" and not keep_old:
            plan.remove.append(rel)
        else:
            plan.keep[rel] = why if verdict == "keep" else f"kept by --keep-old ({why})"
    if not keep_old:
        plan.remove += [s for s in REPLACED_SCRIPTS if (root / s).is_file()]
    if pypi:
        plan.notes.append(
            "PyPI: change this project's trusted publisher to workflow `ghtools.yml`, "
            "environment `pypi` (pypi.org → your project → Publishing)."
        )
    return plan, d


def _ask_questions(
    d: Detection, yes: bool, ask: Callable[[str], str], out: Callable[[str], None]
) -> dict[str, str]:
    answers: dict[str, str] = {}
    for q in d.questions:
        if yes or (not sys.stdin.isatty() and ask is input):
            answers[q.key] = q.default
            out(f"? {q.prompt} -> {q.default} (default)")
            continue
        reply = ask(f"? {q.prompt} [{'/'.join(q.choices)}] (default {q.default}): ").strip()
        answers[q.key] = reply if reply in q.choices else q.default
    return answers


def init_repo(
    root: Path,
    *,
    yes: bool = False,
    dry_run: bool = False,
    keep_old: bool = False,
    archive_mode: str = "snapshot",
    write: bool = False,
    ref: str = STUB_REF,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    today: str | None = None,
) -> int:
    root = Path(root).resolve()
    if not is_work_tree_root(root):
        raise PreconditionError(
            "run ghtools init from the repository root (the directory holding .git)"
        )
    existing = root / CONFIG_PATH
    first = detect(root)
    answers = _ask_questions(first, yes, ask, out)
    plan, d = make_plan(root, answers, keep_old, archive_mode, ref)

    if existing.is_file():  # re-run: compare, write only the settings file and only with --write
        old = existing.read_text(encoding="utf-8").splitlines(keepends=True)
        new = plan.write[CONFIG_PATH].splitlines(keepends=True)
        diff = list(
            difflib.unified_diff(old, new, CONFIG_PATH + " (current)", CONFIG_PATH + " (detected)")
        )
        out("".join(diff) if diff else f"{CONFIG_PATH} matches what detection finds now.")
        if write and diff and not dry_run:
            if dirty_paths([CONFIG_PATH], root):
                raise PreconditionError(
                    f"uncommitted changes in {CONFIG_PATH}; commit or stash first"
                )
            existing.write_text(plan.write[CONFIG_PATH], encoding="utf-8")
            out(f"wrote {CONFIG_PATH}")
        return 0

    out("Detected:")
    for key, value in d.values.items():
        out(f"  {key:<24} {value!r:<32} ({d.evidence.get(key, '')})")
    out("Will write:")
    out("\n".join(f"  + {p}" for p in plan.write))
    if plan.remove:
        out("Replaces (removed after archiving):")
        out("\n".join(f"  - {p}" for p in plan.remove))
    if plan.keep:
        out("Keeps:")
        out("\n".join(f"  = {p}  ({why})" for p, why in plan.keep.items()))
    if plan.archive:
        out("Archives: .github/ -> .github/archive/pre-ghtools-<date>/")
    if dry_run:
        out("Dry run: nothing written.")
        return 0

    touched = [*plan.write, *plan.remove] + ([".github"] if plan.archive else [])
    dirty = dirty_paths(touched, root)
    if dirty:
        raise PreconditionError(
            "uncommitted changes in files init would touch: " + ", ".join(dirty)
        )

    commit = git("rev-parse", "--short", "HEAD", cwd=root, check=False).strip() or "no commits"
    if plan.archive:
        snap = archive.snapshot(root, today or date.today().isoformat(), commit)
        out(f"archived .github to {snap.relative_to(root).as_posix()}/")
    for rel in plan.remove:
        (root / rel).unlink()
    for rel, text in plan.write.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for note in plan.notes:
        out(f"NOTE: {note}")
    out(
        "Next: review `git status` / `git diff`, commit, push, then run `ghtools doctor`.\n"
        "Suggested permanent restore point: git tag pre-ghtools HEAD && git push origin pre-ghtools"
    )
    return 0


def _edited_after_commit(root: Path, paths: list[str]) -> list[str]:
    """Tracked `paths` whose working copy differs from HEAD, i.e. edited after committing.

    Right after an uncommitted `init`, the ghtools files are untracked and the replaced
    workflows are deleted: exactly the state deinit undoes, so none of that counts.
    """
    existing = [p for p in paths if (root / p).exists()]
    if not existing:
        return []
    tracked = git("ls-files", "--", *existing, cwd=root).split()
    if not tracked:
        return []
    diff = git("diff", "--name-only", "HEAD", "--", *tracked, cwd=root, check=False)
    return sorted(line for line in diff.splitlines() if line)


def deinit_repo(root: Path, *, dry_run: bool = False, out: Callable[[str], None] = print) -> int:
    root = Path(root).resolve()
    if not is_work_tree_root(root):
        raise PreconditionError("run ghtools deinit from the repository root")
    snap = archive.latest_snapshot(root)
    ours = [p for p in (CONFIG_PATH, STUB_PATH) if (root / p).exists()]
    if snap is not None:
        targets = [
            (Path(".github") / f.relative_to(snap)).as_posix()
            for f in sorted(snap.rglob("*"))
            if f.is_file() and f.relative_to(snap).as_posix() != "README.md"
        ]
        source = f"snapshot {snap.relative_to(root).as_posix()}"
    elif tag_exists("pre-ghtools", root):
        listing = git("ls-tree", "-r", "--name-only", "pre-ghtools", "--", ".github", cwd=root)
        targets = [line for line in listing.splitlines() if line]
        source = "tag pre-ghtools"
    else:
        raise PreconditionError(
            "no .github/archive/pre-ghtools-* snapshot and no pre-ghtools tag: "
            "nothing to restore from"
        )
    for rel in targets:
        out(f"restore {rel}  (from {source})")
    for rel in ours:
        out(f"delete  {rel}")
    if dry_run:
        out("Dry run: nothing written.")
        return 0
    edited = _edited_after_commit(root, [*ours, *targets])
    if edited:
        raise PreconditionError(
            "uncommitted changes in files deinit would touch: " + ", ".join(edited)
        )
    if snap is not None:
        archive.restore(root, snap)
        shutil.rmtree(snap)
        base = root / archive.ARCHIVE_DIR
        if base.exists() and not any(base.iterdir()):
            base.rmdir()
    else:
        git("checkout", "pre-ghtools", "--", ".github", cwd=root)
    for rel in ours:
        (root / rel).unlink()
    out("ghtools removed.")
    out("If a ghtools-status branch exists: git push origin --delete ghtools-status")
    return 0
