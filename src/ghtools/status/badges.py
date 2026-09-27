"""Flat badges rendered locally (no shields.io), so they work for private repos too."""

from __future__ import annotations

from html import escape
from typing import Any

from ..config import Config
from ..templates import render

COLORS = {
    "pass": "#1a7f37",
    "warn": "#9a6700",
    "fail": "#cf222e",
    "info": "#0969da",
    "muted": "#6e7781",
}


def text_width(text: str) -> int:
    """Approximate Verdana 11px width: narrow glyphs count less, which keeps badges snug."""
    narrow = sum(1 for c in text if c in "ijl.,:;|!' ()1")
    return int(round((len(text) - narrow) * 7 + narrow * 4))


def render_badge(label: str, message: str, color: str) -> str:
    lw, mw = text_width(label) + 12, text_width(message) + 12
    return render(
        "badge.svg.j2",
        label=escape(label),
        message=escape(message),
        color=color,
        label_width=lw,
        message_width=mw,
        width=lw + mw,
        label_x=lw / 2,
        message_x=lw + mw / 2,
    )


def _coverage_color(value: float) -> str:
    return COLORS["pass"] if value >= 80 else COLORS["warn"] if value >= 60 else COLORS["fail"]


def badges_for(data: dict[str, dict[str, Any]], cfg: Config) -> dict[str, str]:
    project, ci, docs = data.get("project", {}), data.get("ci", {}), data.get("docs", {})
    out: dict[str, str] = {}
    for name in cfg.get("status.badges"):
        badge: tuple[str, str, str] | None = None
        if name == "version" and project.get("version"):
            badge = ("version", f"v{project['version']}", COLORS["info"])
        elif name == "coverage" and ci.get("coverage") is not None:
            badge = ("coverage", f"{ci['coverage']:.1f}%", _coverage_color(ci["coverage"]))
        elif name == "docstrings" and ci.get("docstrings") is not None:
            badge = ("docstrings", f"{ci['docstrings']:.1f}%", _coverage_color(ci["docstrings"]))
        elif name == "tests" and ci.get("tests", {}).get("total"):
            t = ci["tests"]
            msg = f"{t['passed']} passed" + (f", {t['failed']} failed" if t["failed"] else "")
            badge = ("tests", msg, COLORS["fail"] if t["failed"] else COLORS["pass"])
        elif name == "lint" and ci.get("lint", {}).get("status") in ("passing", "failing"):
            ok = ci["lint"]["status"] == "passing"
            badge = (
                "lint",
                "passing" if ok else "failing",
                COLORS["pass"] if ok else COLORS["fail"],
            )
        elif name == "python" and ci.get("python"):
            versions = " | ".join(p["version"] for p in ci["python"])
            badge = ("python", versions, COLORS["info"])
        elif name == "docs" and docs.get("build"):
            ok = docs["build"] == "passing"
            badge = (
                "docs",
                "passing" if ok else "failing",
                COLORS["pass"] if ok else COLORS["fail"],
            )
        if badge:
            out[f"badges/{name}.svg"] = render_badge(*badge)
    return out
