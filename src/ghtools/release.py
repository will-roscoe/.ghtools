"""Release steps: decide the next version, prepare files, extract notes, build assets."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .changelog import finalize_changelog, release_section
from .config import Config
from .errors import CheckFailed
from .gitutil import latest_tag, log_messages, log_subjects, since_range, tag_exists
from .version import compute_next_version
from .versionfile import read_version, version_file, write_version


@dataclass(frozen=True)
class Decision:
    release: bool
    version: str
    tag: str
    current: str
    reason: str

    def as_outputs(self) -> dict[str, str]:
        return {
            "released": "true" if self.release else "false",
            "version": self.version,
            "tag": self.tag,
            "reason": self.reason,
        }

    def summary(self) -> str:
        head = f"### Next version: `{self.tag}`" if self.release else "### No release"
        return f"{head}\n\n{self.reason}\n\n_Dry run: nothing was written or tagged._\n"


def decide(cfg: Config, root: Path) -> Decision:
    if not cfg.get("release.enabled"):
        return Decision(False, "", "", "", "release.enabled = false")
    current = latest_tag(root)
    messages = log_messages(since_range(current), root)
    nxt = compute_next_version(
        current, messages, zero_major_breaking=cfg.get("version.zero-major-breaking")
    )
    cur = current or ""
    if not nxt:
        where = current or "the first commit"
        return Decision(False, "", "", cur, f"no feat/fix/perf/breaking commits since {where}")
    tag = f"v{nxt}"
    if tag_exists(tag, root):
        return Decision(False, nxt, tag, cur, f"tag {tag} already exists")
    if cfg.get("version.source") != "tag" and cfg.get("version.bump") == "require":
        declared = read_version(cfg, root)
        if declared != nxt:
            raise CheckFailed(
                f"{version_file(cfg)} says {declared} but the commits imply {nxt}. "
                "Bump it (and add a CHANGELOG entry) before releasing."
            )
    return Decision(True, nxt, tag, cur, f"{current or 'no previous tag'} -> {tag}")


def prepare(cfg: Config, root: Path, version: str, today: date | None = None) -> list[str]:
    """Write the version file and finalize the changelog; return the paths changed."""
    if not cfg.get("release.commit"):
        return []
    root = Path(root)
    changed: list[str] = []
    if cfg.get("version.source") != "tag" and cfg.get("version.bump") == "write":
        changed.append(write_version(cfg, root, version))
    rel = cfg.get("release.changelog")
    if rel and (root / rel).is_file():
        path = root / rel
        subjects = log_subjects(since_range(latest_tag(root)), root)
        today = today or datetime.now(UTC).date()
        text, did = finalize_changelog(path.read_bytes().decode("utf-8"), version, today, subjects)
        if did:
            path.write_bytes(text.encode("utf-8"))
            changed.append(rel)
    return changed


def notes(cfg: Config, root: Path, version: str) -> str:
    rel = cfg.get("release.changelog")
    path = Path(root) / rel if rel else None
    if not path or not path.is_file():
        return ""
    return release_section(path.read_text(encoding="utf-8"), version)


def _zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(p for p in src.rglob("*") if p.is_file()):
            if "__pycache__" in file.parts:
                continue
            zf.write(file, file.relative_to(src).as_posix())
    return dest


def build(cfg: Config, root: Path, out: str = "dist") -> list[Path]:
    """Build release assets into `out`: sdist/wheel for python, a zip for github-zip."""
    root = Path(root)
    dist = root / out
    dist.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    publish = cfg.get("release.publish")
    if cfg.get("profile") == "python" or "pypi" in publish:
        python = shutil.which("python") or sys.executable
        proc = subprocess.run([python, "-m", "build", "--outdir", str(dist)], cwd=root)
        if proc.returncode != 0:
            raise CheckFailed(f"python -m build failed (exit {proc.returncode})")
        made += sorted(p for p in dist.iterdir() if p.suffix in {".whl", ".gz"})
    if "github-zip" in publish:
        src = root / cfg.get("release.zip")
        made.append(_zip_dir(src, dist / f"{src.name}.zip"))
    return made
