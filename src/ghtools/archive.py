"""Snapshot .github before init; restore it on deinit (spec §5.3–§5.4)."""

from __future__ import annotations

import shutil
from pathlib import Path

from .errors import PreconditionError
from .templates import render

ARCHIVE_DIR = ".github/archive"
_PREFIX = "pre-ghtools-"
# Files outside .github that init rewrites (README.md): the original, plus what init wrote,
# so deinit can tell whether the file was edited since.
ROOT_FILES = "_root"
WRITTEN = ".written"


def snapshot(root: Path, today: str, commit: str) -> Path:
    root = Path(root)
    # The snapshot's own README.md is never restored, so a repo README at .github/README.md
    # would be lost on deinit; make the user move it rather than guess.
    if (root / ".github/README.md").exists():
        raise PreconditionError(
            ".github/README.md exists; move it before init (the archive uses that name)"
        )
    base = root / ARCHIVE_DIR
    dest = base / f"{_PREFIX}{today}"
    n = 2
    while dest.exists():
        dest = base / f"{_PREFIX}{today}-{n}"
        n += 1
    shutil.copytree(root / ".github", dest, ignore=shutil.ignore_patterns("archive"))
    (dest / "README.md").write_text(
        render("archive-readme.md.j2", today=today, commit=commit), encoding="utf-8"
    )
    return dest


def save_root_file(snap: Path, rel: str, original: bytes, written: str) -> None:
    dest = Path(snap) / ROOT_FILES / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(original)
    Path(f"{dest}{WRITTEN}").write_text(written, encoding="utf-8")


def root_files(snap: Path) -> dict[str, tuple[bytes, str]]:
    """rel path -> (original bytes, text init wrote), for root files the snapshot holds."""
    base = Path(snap) / ROOT_FILES
    out: dict[str, tuple[bytes, str]] = {}
    for f in sorted(base.rglob("*")) if base.is_dir() else []:
        if f.is_file() and not f.name.endswith(WRITTEN):
            written = Path(f"{f}{WRITTEN}")
            text = written.read_text(encoding="utf-8") if written.is_file() else ""
            out[f.relative_to(base).as_posix()] = (f.read_bytes(), text)
    return out


def latest_snapshot(root: Path) -> Path | None:
    base = Path(root) / ARCHIVE_DIR
    snaps = sorted(base.glob(f"{_PREFIX}*"), key=lambda p: p.stat().st_mtime_ns)
    return snaps[-1] if snaps else None


def restore(root: Path, snap: Path) -> list[str]:
    root = Path(root)
    restored: list[str] = []
    for file in sorted(p for p in snap.rglob("*") if p.is_file()):
        rel = file.relative_to(snap)
        if rel.as_posix() == "README.md" or rel.parts[0] == ROOT_FILES:
            continue
        target = root / ".github" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        restored.append((Path(".github") / rel).as_posix())
    return restored


def prune(root: Path) -> bool:
    base = Path(root) / ARCHIVE_DIR
    if not base.exists():
        return False
    shutil.rmtree(base)
    return True
