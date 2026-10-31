"""Resolve each (python, runner) CI leg against the Python builds GitHub actually ships.

``actions/setup-python`` fails in setup when no build exists for a runner's OS version and
architecture (e.g. Python 3.9 on ``ubuntu-latest`` once it points at Ubuntu 26.04). This module
decides, before the matrix is handed to GitHub, which runner label each leg should use.

Two shipped data files drive it (``ghtools/data/``):

* ``runners.json``: label grammar, what each ``-latest`` alias points to, the OS versions that
  have explicit labels, and constraint ``rules``. Hand-maintained.
* ``python_builds.json``: per Python minor, the OS versions with a build, per ``os/arch``,
  in any stable patch release. Regenerate with ``scripts/refresh_python_manifest.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from .config import Config

_SPEC = re.compile(r"^(<=|>=|==|!=|<|>)?\s*(\d+(?:\.\d+)*)$")
_MINOR = re.compile(r"^(\d+\.\d+)$")
_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}
ANY_VERSION = "*"


@dataclass(frozen=True)
class Runner:
    """A parsed runner label: ``os`` is linux/darwin/win32, ``arch`` is x64/arm64."""

    label: str
    os: str
    version: str
    arch: str
    tag: str
    family: dict[str, Any]


@dataclass(frozen=True)
class Note:
    """A message for the matrix step; ``kind`` is ``warning`` (leg dropped) or ``notice``."""

    kind: str
    message: str


@cache
def load_table() -> dict[str, Any]:
    """The shipped ``runners.json`` as a dict (keys: aliases, families, rules)."""
    return json.loads(resources.files("ghtools.data").joinpath("runners.json").read_text("utf-8"))


@cache
def load_snapshot() -> dict[str, dict[str, list[str]]]:
    """The shipped ``python_builds.json`` ``builds`` map: minor -> ``os/arch`` -> OS versions."""
    text = resources.files("ghtools.data").joinpath("python_builds.json").read_text("utf-8")
    return json.loads(text)["builds"]


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def version_matches(version: str, spec: str) -> bool:
    """Whether ``version`` satisfies ``spec`` (``<=3.9``, ``>24.04``, bare ``3.9``), numerically.

    :raises ValueError: if ``spec`` is not an operator plus dotted integers.
    """
    m = _SPEC.match(spec.strip())
    if not m:
        raise ValueError(f"bad version spec {spec!r} (expected e.g. '<=3.9', '>24.04', '3.12')")
    return _OPS[m.group(1) or "=="](_key(version), _key(m.group(2)))


def parse_runner(label: str, table: dict[str, Any] | None = None) -> Runner | None:
    """Parse a runner label (resolving ``-latest`` aliases) to a :class:`Runner`, or ``None``.

    ``None`` means the label is not a GitHub-hosted one ghtools knows (self-hosted, custom).
    """
    table = table or load_table()
    target = table["aliases"].get(label, label)
    for fam in table["families"]:
        m = re.match(fam["pattern"], target)
        if m:
            tag = m.group("tag") or ""
            return Runner(label, fam["os"], m.group("version"), fam["arch"][tag], tag, fam)
    return None


def _has_build(builds: dict[str, list[str]], r_os: str, arch: str, version: str) -> bool:
    versions = builds.get(f"{r_os}/{arch}", [])
    return ANY_VERSION in versions or version in versions


def _rules_allow(rules: list[dict[str, Any]], python: str, r: Runner, version: str) -> bool:
    for rule in rules:
        match = rule.get("match", {})
        if "python" in match and not version_matches(python, match["python"]):
            continue
        if match.get("os", r.os) != r.os or match.get("arch", r.arch) != r.arch:
            continue
        need = rule.get("require", {}).get("version")
        if need and not version_matches(version, need):
            return False
    return True


def resolve_leg(
    python: str,
    label: str,
    snapshot: dict[str, dict[str, list[str]]] | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> tuple[str | None, Note | None]:
    """The runner label ``python`` should use in place of ``label``, plus an explanatory note.

    Returns ``(label, None)`` unchanged when a build exists (or nothing is known about the
    python or runner), ``(other_label, notice)`` when the same os+arch has an older/newer OS
    version with a build, and ``(None, warning)`` when no OS version of that os+arch does.
    Architecture is never changed. Never raises for unknown inputs.
    """
    snapshot = load_snapshot() if snapshot is None else snapshot
    rules = load_table()["rules"] if rules is None else rules
    r = parse_runner(label)
    minor = _MINOR.match(python)
    builds = snapshot.get(python) if minor else None
    if r is None or builds is None:
        return label, None

    def ok(version: str) -> bool:
        return _has_build(builds, r.os, r.arch, version) and _rules_allow(rules, python, r, version)

    if ok(r.version):
        return label, None
    template = r.family.get("template")
    if template:
        for version in sorted(r.family["versions"], key=_key, reverse=True):
            if ok(version):
                new = template.format(version=version, tag=r.tag)
                was = f"{label} ({r.os} {r.version}/{r.arch})"
                return new, Note("notice", f"python {python}: no build for {was}; using {new}")
    return None, Note("warning", f"skipped: no python {python} build for {r.os}/{r.arch} ({label})")


def resolve_matrix(
    python: list[str],
    labels: list[str],
    *,
    snapshot: dict[str, dict[str, list[str]]] | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], list[Note]]:
    """The ``matrix.include`` legs for ``python`` x ``labels``, plus notes about changed legs.

    Every python runs on ``labels[0]``; the newest python (last) also runs on the other labels.
    Each leg is passed through :func:`resolve_leg`; dropped legs vanish and duplicates collapse.
    """
    snapshot = load_snapshot() if snapshot is None else snapshot
    rules = load_table()["rules"] if rules is None else rules
    pairs = [(p, labels[0]) for p in python] + [(python[-1], r) for r in labels[1:]]
    include: list[dict[str, str]] = []
    notes: list[Note] = []
    for py, label in pairs:
        runner, note = resolve_leg(py, label, snapshot, rules)
        if note and note not in notes:
            notes.append(note)
        leg = {"python": py, "runner": runner}
        if runner is not None and leg not in include:
            include.append(leg)
    return include, notes


def check(cfg: Config) -> list[Note]:
    """Notes for the configured legs: warnings name legs that cannot run on any OS version."""
    return resolve_matrix(cfg.get("ci.python"), cfg.get("ci.runners"))[1]


def snapshot_from_manifest(manifest: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Reduce actions/python-versions ``versions-manifest.json`` to the snapshot ``builds`` map.

    For each ``X.Y`` the union, over all *stable* patch releases, of ``os/arch`` -> OS versions
    (``*`` when the platform carries none). Free-threaded builds are ignored.
    """
    out: dict[str, dict[str, set[str]]] = {}
    for release in manifest:
        if not release.get("stable"):
            continue
        minor = ".".join(release["version"].split(".")[:2])
        for f in release["files"]:
            if "freethreaded" in f["arch"]:
                continue
            key = f"{f['platform']}/{f['arch']}"
            out.setdefault(minor, {}).setdefault(key, set()).add(f.get("platform_version") or "*")
    return {
        m: {
            k: sorted(v, key=lambda x: (x == ANY_VERSION, _key(x) if x != ANY_VERSION else ()))
            for k, v in sorted(d.items())
        }
        for m, d in sorted(out.items(), key=lambda kv: _key(kv[0]))
    }
