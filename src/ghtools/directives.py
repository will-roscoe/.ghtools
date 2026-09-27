"""CI directives in commit messages: `[name[-full][:args]]` or `/name`.

Ported from sph-dev's resolve_directives.py. Each token becomes an effect on the pipeline
rather than a shell command: force CI past the path filter, start a configured workflow,
or explain why nothing happens. Release directives (`+:major` …) live in version.py.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

BUILTIN = ("ci", "test", "lint", "coverage", "coverage-code", "coverage-docs", "docs")
_LANGS = {"py"}


@dataclass
class Effect:
    directive: str
    kind: str  # force-ci | dispatch | notice | warn
    target: str = ""
    message: str = ""


def _pattern(names: list[str]) -> re.Pattern[str]:
    # Longest names first so `coverage-code` wins over `coverage`. A backtick right before
    # `[` or `/` means the directive is quoted in prose, not invoked.
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(
        rf"(?<![\w`])(?:\[|/)(?P<base>{alts})(?P<full>-full)?(?::(?P<spec>[a-z0-9,]+))?(?:\]|(?=[\s\],]|$))",
        re.IGNORECASE,
    )


def _expand(
    base: str, full: bool, spec: list[str] | None, dispatch: dict[str, str], docs: bool
) -> list[Effect]:
    if base in dispatch:
        return [Effect(base, "dispatch", dispatch[base])]
    if base == "ci":
        return [Effect("ci", "force-ci")]
    if base == "docs":
        if docs:
            return [
                Effect("docs", "notice", message="docs build on every run; [docs] changes nothing")
            ]
        return [Effect("docs", "warn", message="[docs] ignored: docs.enabled is false")]
    if base == "coverage":
        return [
            e
            for sub in ("coverage-code", "coverage-docs")
            for e in _expand(sub, full, spec, dispatch, docs)
        ]
    name = f"{base}-full" if full and base == "test" else base
    out = []
    for lang in spec or ["py"]:
        if lang in _LANGS:
            out.append(Effect(f"{name}:{lang}", "force-ci"))
        else:
            out.append(
                Effect(
                    f"{name}:{lang}",
                    "warn",
                    message=f"no tool for {base}:{lang} in ghtools; skipped, treated as passed",
                )
            )
    return out


def resolve(messages: list[str], dispatch: dict[str, str], docs_enabled: bool) -> list[Effect]:
    pattern = _pattern([*BUILTIN, *dispatch])
    seen: set[str] = set()
    effects: list[Effect] = []
    for message in messages:
        for m in pattern.finditer(message):
            spec = m.group("spec").lower().split(",") if m.group("spec") else None
            for effect in _expand(
                m.group("base").lower(), bool(m.group("full")), spec, dispatch, docs_enabled
            ):
                if effect.directive not in seen:
                    seen.add(effect.directive)
                    effects.append(effect)
    return effects


def messages(root: Path, before: str) -> list[str]:
    """Messages pushed since `before`, newest first; only HEAD when `before` is unusable."""
    rng = ["-1", "HEAD"]
    if before and set(before) != {"0"}:
        known = subprocess.run(
            ["git", "cat-file", "-e", f"{before}^{{commit}}"], cwd=root, capture_output=True
        )
        if known.returncode == 0:
            rng = [f"{before}..HEAD"]
    proc = subprocess.run(
        ["git", "log", "--format=%B%x00", *rng],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [m.strip() for m in proc.stdout.split("\0") if m.strip()]
