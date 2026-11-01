"""Path-conditional test selection: which pytest markers a change lets CI leave out."""

from __future__ import annotations

from .ci import glob_to_regex
from .config import Config


def deselected(cfg: Config, files: list[str] | None, event: str) -> list[str]:
    """Sorted pytest markers to leave out of this run, per the ``[[ci.select]]`` rules.

    A rule is met when any of ``files`` matches any of its ``changed`` globs; an unmet rule
    deselects its ``marker``. Returns ``[]`` (run everything) when ``files`` is None (changes
    unknown), when ``event`` is in ``ci.select-full-on``, or when there are no rules.
    """
    rules = cfg.get("ci.select")
    if files is None or not rules or event in cfg.get("ci.select-full-on"):
        return []
    out = set()
    for rule in rules:
        patterns = [glob_to_regex(g) for g in rule["changed"]]
        if not any(p.match(f) for f in files for p in patterns):
            out.add(rule["marker"])
    return sorted(out)
