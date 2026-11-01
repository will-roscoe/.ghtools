"""Status card layout: turns fragments into positioned cards and panels for the template."""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from typing import Any

from .. import components
from ..config import Config
from ..templates import render

RIGHT = 865
CARD_W, CARD_H, CARD_GAP, CARD_ROW = 190, 90, 20, 110
PANEL_GAP, PANEL_ROW = 40, 110
LINK_CHAR_W, LINK_GAP = 8.8, 24  # 16px sans-serif, approximate
# Text budgets, so nothing runs past its card or into its neighbour (13px mono ≈ 7.8 px/char,
# 22px mono ≈ 13.2 px/char, 13px sans ≈ 7.5 px/char).
BLOCK_W, BLOCK_H, BLOCK_GAP, BLOCK_NAME_CHARS = 190, 112, 20, 17
DETAIL_CHARS, VALUE_CHARS, ITEM_LABEL_CHARS = 22, 14, 16
VALUE_CHAR_W, ITEM_LABEL_CHAR_W, ITEM_GAP = 13.2, 7.5, 24


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _extra_items(items: list[dict[str, str]]) -> tuple[list[dict[str, Any]], int]:
    """Columns sized by their own text (a CalVer value is far wider than a SemVer one)."""
    out, dx = [], 0.0
    for item in items:
        label = _clip(str(item.get("label", "")), ITEM_LABEL_CHARS)
        value = _clip(str(item.get("value", "")), VALUE_CHARS)
        out.append({"label": label, "value": value, "dx": int(dx)})
        dx += max(len(label) * ITEM_LABEL_CHAR_W, len(value) * VALUE_CHAR_W) + ITEM_GAP
    return out, int(dx)


def wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _inside(root: Path, rel: str) -> bool:
    """Only files inside the repository may be inlined: the card is public."""
    if not rel:
        return False
    base = Path(root).resolve()
    return (base / rel).resolve().is_relative_to(base)


def logo_data_uri(path: Path) -> str:
    """Inline the logo: GitHub serves raw SVGs under a CSP that blocks external images."""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _state_for_percent(value: float, floor: int) -> str:
    good = floor or 80
    return "pass" if value >= good else "warn" if value >= 60 else "fail"


def _card(kind: str, data: dict[str, dict[str, Any]], cfg: Config) -> dict[str, str] | None:
    ci, docs, project = data.get("ci", {}), data.get("docs", {}), data.get("project", {})
    if kind == "tests" and ci.get("tests", {}).get("total"):
        t = ci["tests"]
        value = f"✓ {t['passed']}" if not t["failed"] else f"✗ {t['failed']} failed"
        return {
            "title": "TESTS",
            "value": value,
            "detail": f"{t['failed']} failed · {t['skipped']} skipped",
            "state": "fail" if t["failed"] else "pass",
        }
    if kind == "coverage" and ci.get("coverage") is not None:
        floor = cfg.get("ci.coverage.floor")
        detail = f"line coverage (floor {floor}%)" if floor else "line coverage"
        return {
            "title": "COVERAGE",
            "value": f"{ci['coverage']:.1f}%",
            "detail": detail,
            "state": _state_for_percent(ci["coverage"], floor),
        }
    if kind == "lint" and ci.get("lint", {}).get("status") in ("passing", "failing"):
        ok = ci["lint"]["status"] == "passing"
        gates = ", ".join(ci["lint"].get("gates", [])) or "gates"
        return {
            "title": "LINT",
            "value": "✓ Clean" if ok else "✗ Failing",
            "detail": gates,
            "state": "pass" if ok else "fail",
        }
    if kind == "docs" and docs.get("build"):
        ok = docs["build"] == "passing"
        detail = (
            f"{docs['coverage']:.0f}% documented"
            if docs.get("coverage") is not None
            else "sphinx build"
        )
        return {
            "title": "DOCS",
            "value": "✓ Built" if ok else "✗ Failing",
            "detail": detail,
            "state": "pass" if ok else "fail",
        }
    if kind == "docstrings" and ci.get("docstrings") is not None:
        return {
            "title": "DOCSTRINGS",
            "value": f"{ci['docstrings']:.1f}%",
            "detail": "interrogate",
            "state": _state_for_percent(ci["docstrings"], 80),
        }
    if kind == "release" and project.get("version"):
        detail = f"released {project['released']}" if project.get("released") else "latest release"
        return {
            "title": "RELEASE",
            "value": f"v{project['version']}",
            "detail": detail,
            "state": "pass",
        }
    if kind == "open-issues" and project.get("open_issues") is not None:
        n = project["open_issues"]
        return {
            "title": "ISSUES",
            "value": str(n),
            "detail": "open",
            "state": "pass" if n == 0 else "warn",
        }
    return None


def _result(status: str, ok: str, bad: str) -> dict[str, str]:
    """One block row's value and colour class for a passing, failing or unknown status."""
    if status == "passing":
        return {"value": ok, "state": "pass"}
    if status == "failing":
        return {"value": bad, "state": "fail"}
    return {"value": "not run", "state": "muted"}


def _tests_result(leg: dict[str, Any] | None) -> dict[str, str]:
    """A component's last test result, from its (possibly carried over) subproject leg."""
    if not leg:
        return _result("", "", "")
    tests = leg.get("tests") or {}
    if leg.get("status") == "failing" or tests.get("failed"):
        failed = tests.get("failed", 0)
        return {"value": f"✗ {failed} failed" if failed else "✗ Failing", "state": "fail"}
    return {"value": f"✓ {tests['total']}" if tests.get("total") else "✓ Passed", "state": "pass"}


def _blocks(data: dict[str, dict[str, Any]], cfg: Config) -> list[dict[str, Any]]:
    """The components layout: a top block for the repo, then one per released component.

    Each block is ``{name, version, tests, docs}`` where ``tests`` and ``docs`` are
    ``{value, state}``; the repo block has ``lint`` and no ``tests``. A component that has never
    been tagged has ``version == ""``. Results for components a run did not touch were
    carried over from the previous CI fragment by :func:`..fragments.ci_fragment`.
    """
    ci, project = data.get("ci", {}), data.get("project", {})
    docs = _result((data.get("docs") or {}).get("build", ""), "✓ Built", "✗ Failing")
    lint = _result((ci.get("lint") or {}).get("status", ""), "✓ Clean", "✗ Failing")
    versions = {c["name"]: c.get("version", "") for c in project.get("components") or []}
    legs = ci.get("subproject_legs") or {}
    blocks: list[dict[str, Any]] = [
        {
            "name": _clip(project.get("name", ""), BLOCK_NAME_CHARS),
            "version": project.get("version", ""),
            "lint": lint,
            "docs": docs,
        }
    ]
    for c in components.released(cfg):
        blocks.append(
            {
                "name": _clip(c.name, BLOCK_NAME_CHARS),
                "version": versions.get(c.name, ""),
                "tests": _tests_result(legs.get(c.name)),
                "docs": docs,
            }
        )
    for i, block in enumerate(blocks):
        block["x"] = 35 + (i % 4) * (BLOCK_W + BLOCK_GAP)
        block["y"] = i // 4  # row index until the caller knows where the first row starts
    return blocks


def _panels(
    data: dict[str, dict[str, Any]], cfg: Config, top: int
) -> tuple[list[dict[str, Any]], int]:
    ci = data.get("ci", {})
    wanted: list[dict[str, Any]] = []
    for kind in cfg.get("status.rows"):
        if kind == "python" and ci.get("python"):
            wanted.append(
                {
                    "kind": "python",
                    "label": "PYTHON SUPPORT",
                    "items": ci["python"],
                    "width": max(150, 50 * len(ci["python"]) + 20),
                }
            )
        elif kind == "builds" and ci.get("builds"):
            oses = list(ci["builds"].items())
            archs = sorted({a for _, m in oses for a in m})
            wanted.append(
                {
                    "kind": "builds",
                    "label": "BUILDS",
                    "oses": oses,
                    "archs": archs,
                    "width": 70 + 60 * len(oses),
                }
            )
        elif kind == "subprojects" and ci.get("subprojects"):
            subs = ci["subprojects"]
            wanted.append(
                {
                    "kind": "subprojects",
                    "label": "SUBPROJECTS",
                    "items": subs,
                    "width": max(150, 70 * len(subs) + 20),
                }
            )
        elif kind == "extra":
            for name in cfg.get("status.extra"):
                frag = data.get(name)
                if frag and frag.get("items"):
                    wanted.append(
                        {
                            "kind": "extra",
                            "label": frag.get("label", name.upper()),
                            "items": _extra_items(frag["items"])[0],
                            "state": frag.get("state", "ok"),
                            "width": max(150, _extra_items(frag["items"])[1]),
                        }
                    )
    x, y = 35, top
    for panel in wanted:
        if x > 35 and x + panel["width"] > RIGHT:
            x, y = 35, y + PANEL_ROW
        panel["x"], panel["y"] = x, y
        x += panel["width"] + PANEL_GAP
    bottom = (y + 80) if wanted else top
    return wanted, bottom


def build_model(data: dict[str, dict[str, Any]], cfg: Config, root: Path) -> dict[str, Any]:
    project = data.get("project", {})
    logo_path = cfg.get("status.logo")
    logo = logo_data_uri(Path(root) / logo_path) if _inside(root, logo_path) else ""
    text_x = 130 if logo else 35
    lines = wrap(project.get("description", ""), 60 if logo else 74)
    links_y = 90 + 18 * len(lines) + 12
    names = {"pypi": "PyPI", "docs": "Documentation", "repo": "Repository"}
    links, lx = [], text_x
    for key in cfg.get("status.links"):
        url = project.get("links", {}).get(key)
        if url:
            links.append({"text": names[key], "url": url, "x": lx})
            lx += int(len(names[key]) * LINK_CHAR_W) + LINK_GAP
    label_y = (links_y if links else links_y - 30) + 45
    cards: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    layout_cards = cfg.get("status.card") if cfg.get("status.layout") == "single" else []
    if cfg.get("status.layout") == "components":
        blocks = _blocks(data, cfg)
        for block in blocks:
            block["y"] = label_y + 18 + block["y"] * (BLOCK_H + BLOCK_GAP)
    for i, kind in enumerate(k for k in layout_cards if _card(k, data, cfg)):
        card = _card(kind, data, cfg)
        card["detail"] = _clip(card["detail"], DETAIL_CHARS)
        card["x"] = 35 + (i % 4) * (CARD_W + CARD_GAP)
        card["y"] = label_y + 18 + (i // 4) * CARD_ROW
        cards.append(card)
    after_cards = (cards[-1]["y"] + CARD_H + 40) if cards else label_y - 10
    if blocks:
        after_cards = blocks[-1]["y"] + BLOCK_H + 40
    panels, bottom = _panels(data, cfg, after_cards)
    height = (
        (bottom + 30) if panels else ((cards[-1]["y"] + CARD_H + 30) if cards else links_y + 30)
    )
    if blocks and not panels:
        height = blocks[-1]["y"] + BLOCK_H + 30
    updated = max(
        (f.get("updated", "") for f in data.values() if isinstance(f.get("updated"), str)),
        default="",
    )
    model = {
        "width": 900,
        "height": max(height, 140),
        "name": project.get("name", ""),
        "version": project.get("version", ""),
        "description_lines": lines,
        "logo": logo,
        "text_x": text_x,
        "links_y": links_y,
        "links": links,
        "label_y": label_y,
        "cards": cards,
        "panels": panels,
        "updated": updated,
    }
    if blocks:
        model["blocks"] = blocks
    return model


def _escape_model(value: Any) -> Any:
    if isinstance(value, str):
        return escape(value)
    if isinstance(value, list):
        return [_escape_model(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_escape_model(v) for v in value)
    if isinstance(value, dict):
        return {k: _escape_model(v) for k, v in value.items()}
    return value


def render_card(data: dict[str, dict[str, Any]], cfg: Config, root: Path) -> str:
    model = build_model(data, cfg, root)
    # Everything is escaped except the logo, a data: URI built here from a file.
    return render("status.svg.j2", **{**_escape_model(model), "logo": model["logo"]})
