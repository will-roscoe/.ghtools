"""Released components: in-tree subprojects with ``release = true``, versioned on their own.

:func:`released` lists them as :class:`Component` rows with the optional keys resolved to their
defaults. A repo that sets no ``release = true`` gets an empty list and behaves as before.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .changelog import finalize_changelog, release_section
from .config import Config
from .errors import ConfigError, GhtoolsError, PreconditionError
from .gitutil import git, log_messages, log_subjects, since_range, tag_exists
from .release import made_by_ghtools
from .version import compute_next_version, semver_sort_key
from .versionfile import set_manifest_version, set_pyproject_version

_VERSION = r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)(?:\.\d+)?)?"


@dataclass(frozen=True)
class Component:
    """One released component.

    :ivar name: the subproject name, also ``{name}`` in `tag`.
    :ivar path: its directory, relative to the repo root.
    :ivar version_file: where its version lives (default ``<path>/pyproject.toml``).
    :ivar changelog: its changelog (default ``<path>/CHANGELOG.md``).
    :ivar tag: the tag template, using only ``{name}`` and ``{version}``.
    :ivar needs_paths: paths of every subproject it needs, transitively, dependencies first.
    """

    name: str
    path: str
    version_file: str
    changelog: str
    tag: str
    needs_paths: tuple[str, ...]

    def tag_for(self, version: str) -> str:
        """The tag name for `version`, e.g. ``cdn/0.4.1``."""
        return self.tag.format(name=self.name, version=version)


def released(cfg: Config) -> list[Component]:
    """The in-tree ``release = true`` subprojects, in config order.

    Submodule rows are never included: their own repository releases them.

    :param cfg: the loaded config.
    :returns: one :class:`Component` per released row; ``[]`` when none is configured.
    """
    rows = {row["name"]: row for row in cfg.get("subprojects")}

    def needed(name: str, seen: list[str]) -> None:
        for need in rows[name]["needs"]:
            if need not in seen:
                needed(need, seen)
                seen.append(need)

    out = []
    for row in rows.values():
        if not (row["release"] and row["kind"] == "in-tree"):
            continue
        order: list[str] = []
        needed(row["name"], order)
        out.append(
            Component(
                name=row["name"],
                path=row["path"],
                version_file=row["version-file"] or f"{row['path']}/pyproject.toml",
                changelog=row["changelog"] or f"{row['path']}/CHANGELOG.md",
                tag=row["tag"],
                needs_paths=tuple(rows[n]["path"] for n in order),
            )
        )
    return out


@dataclass(frozen=True)
class ComponentDecision:
    """What :func:`decide` concluded for one component.

    :ivar component: the component decided on.
    :ivar release: whether it is to be released (a resume counts).
    :ivar version: the version to release, ``""`` when there are no releasable commits.
    :ivar tag: its tag name, ``""`` when there is no version.
    :ivar previous: its latest existing tag, ``""`` when it has none.
    :ivar reason: one line saying why.
    :ivar resume: the tag exists but has no GitHub release yet; publish it, write nothing.
    """

    component: Component
    release: bool
    version: str
    tag: str
    previous: str
    reason: str
    resume: bool = False

    def as_dict(self) -> dict[str, Any]:
        """A JSON-ready row: name, path, release, version, tag, previous, reason, resume."""
        return {
            "name": self.component.name,
            "path": self.component.path,
            "release": self.release,
            "version": self.version,
            "tag": self.tag,
            "previous": self.previous,
            "reason": self.reason,
            "resume": self.resume,
        }


def find(cfg: Config, name: str) -> Component:
    """The released component called `name`.

    :raises ConfigError: when no ``release = true`` in-tree subproject has that name.
    """
    for c in released(cfg):
        if c.name == name:
            return c
    known = ", ".join(c.name for c in released(cfg)) or "none"
    raise ConfigError(f"no released component {name!r} (released components: {known})")


def _tag_pattern(c: Component) -> re.Pattern[str]:
    parts = re.split(r"(\{name\}|\{version\})", c.tag)
    body = "".join(
        re.escape(c.name)
        if p == "{name}"
        else f"(?P<version>{_VERSION})"
        if p == "{version}"
        else re.escape(p)
        for p in parts
    )
    return re.compile(body)


def tag_version(c: Component, tag: str) -> str:
    """The version part of `tag`, a tag that matches `c`'s template (from :func:`latest_tag`)."""
    match = _tag_pattern(c).fullmatch(tag)
    assert match is not None  # callers pass a tag from latest_tag
    return match.group("version")


def latest_tag(c: Component, root: Path) -> str | None:
    """The newest tag of `c` by SemVer precedence, or None when it has never been released.

    A tag counts only when it equals the template with a SemVer version filled in, so
    ``cdnx/1.0.0`` and ``v1.0.0`` are not ``cdn``'s.
    """
    pattern = _tag_pattern(c)
    found = []
    for tag in git("tag", "--list", cwd=root).split():
        match = pattern.fullmatch(tag)
        if match:
            found.append((semver_sort_key(match.group("version")), tag))
    return max(found)[1] if found else None


def _paths(c: Component) -> list[str]:
    return [c.path, *c.needs_paths]


def decide(
    cfg: Config, root: Path, has_release: Callable[[str], bool] | None = None
) -> list[ComponentDecision]:
    """Decide the next release of every released component, in config order.

    Commits since each component's latest tag are counted only when they touch its ``path`` or a
    needed subproject's path, and go through :func:`ghtools.version.compute_next_version` with
    ``version.zero-major-breaking``. A ``chore(release) ... [skip ci]`` commit is a ``chore``
    and never causes a bump. As for the root, with `has_release` a ghtools-made latest tag with
    no GitHub release yet is resumed rather than skipped. Resume only fires for a newest tag
    annotated exactly ``Release <tag>``, so lightweight tags (like backfilled ones) are never
    resumed.

    A computed version lower than the one in the component's readable version file (a component
    with no tag starts from 0.0.0) is not released: the reason says to tag the current version
    first. A missing or unreadable version file applies no such guard.

    :param cfg: the loaded config.
    :param root: the repository root.
    :param has_release: does the GitHub release for this tag exist; None disables resuming.
    :returns: one :class:`ComponentDecision` per released component; ``[]`` when none is
        configured or ``release.enabled`` is false.
    :raises ghtools.errors.PreconditionError: when git fails (not a repository).
    """
    if not cfg.get("release.enabled"):
        return []
    out = []
    for c in released(cfg):
        current = latest_tag(c, root)
        previous = current or ""
        if (
            current
            and has_release is not None
            and made_by_ghtools(current, root)
            and not has_release(current)
        ):
            reason = f"resuming {current}: the tag exists but has no GitHub release"
            version = tag_version(c, current)
            out.append(ComponentDecision(c, True, version, current, previous, reason, True))
            continue
        base = tag_version(c, current) if current else None
        messages = log_messages(since_range(current), root, _paths(c))
        nxt = compute_next_version(
            base, messages, zero_major_breaking=cfg.get("version.zero-major-breaking")
        )
        if not nxt:
            where = current or "the first commit"
            reason = f"no feat/fix/perf/breaking commits touching {c.path} since {where}"
            out.append(ComponentDecision(c, False, "", "", previous, reason))
            continue
        tag = c.tag_for(nxt)
        declared = _declared_version(c, root)
        if declared and _lower(nxt, declared):
            reason = (
                f"computed {nxt} is lower than {declared} in {c.version_file}; "
                f"tag {c.tag_for(declared)} first"
            )
            out.append(ComponentDecision(c, False, nxt, tag, previous, reason))
            continue
        if tag_exists(tag, root):
            out.append(ComponentDecision(c, False, nxt, tag, previous, f"tag {tag} already exists"))
            continue
        reason = f"{current or 'no previous tag'} -> {tag}"
        out.append(ComponentDecision(c, True, nxt, tag, previous, reason))
    return out


def _bad_file(c: Component) -> GhtoolsError:
    return GhtoolsError(f"{c.name}: {c.version_file} is neither a .toml file nor a JSON manifest")


def _lower(a: str, b: str) -> bool:
    try:
        return semver_sort_key(a) < semver_sort_key(b)
    except ValueError:
        return False


def _declared_version(c: Component, root: Path) -> str | None:
    """The version in `c`'s version file, None when missing, unreadable or without one."""
    path = Path(root) / c.version_file
    try:
        raw = path.read_bytes().decode("utf-8")
        if c.version_file.endswith(".toml"):
            value = tomllib.loads(raw).get("project", {}).get("version")
        else:
            value = json.loads(raw).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _set_version(c: Component, raw: str, version: str) -> str:
    if c.version_file.endswith(".toml"):
        return set_pyproject_version(raw, version)
    try:
        return set_manifest_version(raw, version)
    except (ValueError, TypeError) as exc:  # not JSON, or a top level that is not an object
        raise _bad_file(c) from exc


def prepare(
    cfg: Config, root: Path, decisions: list[ComponentDecision], today: date | None = None
) -> list[str]:
    """Write the version file and finalise the changelog of each component being released.

    Decisions with ``release`` false or ``resume`` true are skipped, as is everything when
    ``release.commit`` is false. A changelog is finalised with the subjects of the commits that
    touch that component's paths only; a missing changelog, or one with nothing to finalise, is
    left alone, as for the root. All new file contents are computed before any file is written,
    and if a write then fails the files already written are put back, so a failure leaves the
    tree as it was.

    :param cfg: the loaded config.
    :param root: the repository root.
    :param decisions: the result of :func:`decide`.
    :param today: the date in the changelog heading; defaults to today (UTC).
    :returns: repo-relative paths changed, in order, without repeats.
    :raises ghtools.errors.PreconditionError: when a version file is missing.
    :raises ghtools.errors.GhtoolsError: when a pyproject has no static version, when a
        non-``.toml`` version file is not a JSON object (``<name>: <file> is neither a .toml
        file nor a JSON manifest``), or when a decision would lower the file's version.
    """
    if not cfg.get("release.commit"):
        return []
    root = Path(root)
    today = today or datetime.now(UTC).date()
    pending: dict[str, str] = {}
    for d in decisions:
        if not d.release or d.resume:
            continue
        c = d.component
        vpath = root / c.version_file
        if not vpath.is_file():
            raise PreconditionError(f"{c.name}: version file {c.version_file} does not exist")
        declared = _declared_version(c, root)
        if declared and _lower(d.version, declared):
            raise GhtoolsError(
                f"{c.name}: {d.version} is lower than {declared} in {c.version_file}; "
                f"tag {c.tag_for(declared)} first"
            )
        pending[c.version_file] = _set_version(c, vpath.read_bytes().decode("utf-8"), d.version)
        cpath = root / c.changelog
        if cpath.is_file():
            subjects = log_subjects(since_range(d.previous or None), root, _paths(c))
            text, did = finalize_changelog(
                cpath.read_bytes().decode("utf-8"), d.version, today, subjects
            )
            if did:
                pending[c.changelog] = text
    originals: dict[str, bytes] = {}
    try:
        for rel, text in pending.items():
            originals[rel] = (root / rel).read_bytes()
            (root / rel).write_bytes(text.encode("utf-8"))
    except BaseException:
        for rel, data in originals.items():
            (root / rel).write_bytes(data)
        raise
    return list(pending)


def notes(cfg: Config, root: Path, name: str, version: str) -> str:
    """The changelog section of component `name` for `version`, ``""`` when there is none.

    :raises ConfigError: when `name` is not a released component.
    """
    path = Path(root) / find(cfg, name).changelog
    if not path.is_file():
        return ""
    return release_section(path.read_text(encoding="utf-8"), version)


def commit_message(decisions: list[ComponentDecision]) -> str:
    """The release commit subject, e.g. ``chore(release): cdn 0.4.1, trainsearch 0.12.0 [skip ci]``.

    Resumed releases are left out (nothing is committed for them); ``""`` when none remain.
    """
    bumped = [f"{d.component.name} {d.version}" for d in decisions if d.release and not d.resume]
    return f"chore(release): {', '.join(bumped)} [skip ci]" if bumped else ""
