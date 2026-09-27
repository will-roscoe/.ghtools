from __future__ import annotations

from xml.etree import ElementTree as ET

from ghtools import config
from ghtools.status import badges


def test_render_badge_is_valid_svg_with_escaped_text():
    svg = badges.render_badge("docs", "a & <b>", badges.COLORS["pass"])
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert "a &amp; &lt;b&gt;" in svg
    assert 'fill="#1a7f37"' in svg


def test_width_grows_with_text():
    short = ET.fromstring(badges.render_badge("v", "1", "#000"))
    long = ET.fromstring(badges.render_badge("version", "1.2.3-rc.4", "#000"))
    assert int(long.get("width")) > int(short.get("width"))


def test_badges_for_uses_available_data_only():
    data = {
        "project": {"version": "2.3.0"},
        "ci": {
            "tests": {"passed": 10, "failed": 0, "skipped": 1, "total": 11},
            "coverage": 90.4,
            "lint": {"status": "passing"},
            "python": [
                {"version": "3.9", "status": "passing"},
                {"version": "3.13", "status": "passing"},
            ],
        },
    }
    cfg = config.from_dict(
        {"status": {"badges": ["version", "coverage", "tests", "lint", "python", "docs"]}}
    )
    out = badges.badges_for(data, cfg)
    assert sorted(out) == [
        "badges/coverage.svg",
        "badges/lint.svg",
        "badges/python.svg",
        "badges/tests.svg",
        "badges/version.svg",
    ]
    assert "90.4%" in out["badges/coverage.svg"]
    assert "3.9 | 3.13" in out["badges/python.svg"]
    assert "10 passed" in out["badges/tests.svg"]
    assert badges.COLORS["pass"] in out["badges/lint.svg"]


def test_failing_tests_badge_is_red():
    data = {"ci": {"tests": {"passed": 9, "failed": 1, "skipped": 0, "total": 10}}}
    cfg = config.from_dict({"status": {"badges": ["tests"]}})
    svg = badges.badges_for(data, cfg)["badges/tests.svg"]
    assert "1 failed" in svg and badges.COLORS["fail"] in svg
