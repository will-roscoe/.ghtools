"""Status card model and render tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ghtools import config
from ghtools.status import model

FULL = {
    "project": {
        "name": "protonfs",
        "description": "Sync a local directory tree with Proton Drive via the official Proton Drive CLI, with conflict-aware push/pull and a local sync manifest.",
        "version": "2.3.0",
        "released": "2026-09-24",
        "open_issues": 1,
        "links": {
            "pypi": "https://pypi.org/project/protonfs/",
            "docs": "https://x.github.io/protonfs",
            "repo": "https://github.com/x/protonfs",
        },
    },
    "ci": {
        "tests": {"passed": 1031, "failed": 0, "skipped": 17, "total": 1048},
        "coverage": 96.8,
        "docstrings": 91.0,
        "lint": {"status": "passing", "gates": ["ruff"]},
        "python": [
            {"version": v, "status": "passing"} for v in ["3.9", "3.10", "3.11", "3.12", "3.13"]
        ],
        "builds": {
            "Linux": {"x86_64": "passing", "arm64": "passing"},
            "macOS": {"x86_64": "passing", "arm64": "failing"},
        },
    },
    "docs": {"build": "passing", "coverage": 100.0},
    "proton-drive": {
        "label": "PROTON DRIVE",
        "items": [{"label": "Pinned", "value": "0.8.0"}, {"label": "Latest", "value": "0.8.1"}],
        "state": "warn",
    },
}


def _cfg(**status):
    return config.from_dict({"release": {"publish": ["github", "pypi"]}, "status": status})


def test_wrap():
    assert model.wrap("a b c d", 3) == ["a b", "c d"]
    assert model.wrap("", 10) == [""]
    assert model.wrap("averyveryverylongword x", 5) == ["averyveryverylongword", "x"]


def test_full_model_layout(tmp_path):
    cfg = _cfg(
        card=["tests", "coverage", "lint", "docs"],
        rows=["python", "builds", "extra"],
        extra=["proton-drive"],
    )
    m = model.build_model(FULL, cfg, tmp_path)
    assert [c["title"] for c in m["cards"]] == ["TESTS", "COVERAGE", "LINT", "DOCS"]
    assert [c["x"] for c in m["cards"]] == [35, 245, 455, 665]
    assert m["cards"][0]["value"] == "✓ 1031"
    assert m["cards"][0]["state"] == "pass"
    assert [p["kind"] for p in m["panels"]] == ["python", "builds", "extra"]
    assert all(p["x"] + p["width"] <= 865 for p in m["panels"])
    assert m["height"] > m["panels"][-1]["y"]
    assert [link["text"] for link in m["links"]] == ["PyPI", "Documentation", "Repository"]


def test_missing_sources_drop_cards_and_panels(tmp_path):
    data = {
        "project": {
            "name": "bash-helpers",
            "description": "Shell helpers",
            "version": "",
            "links": {},
        }
    }
    m = model.build_model(data, _cfg(), tmp_path)
    assert m["cards"] == []
    assert m["panels"] == []
    assert m["links"] == []
    svg = model.render_card(data, _cfg(), tmp_path)
    ET.fromstring(svg)
    assert "0%" not in svg and "QUALITY" not in svg


def test_more_than_four_cards_wrap(tmp_path):
    cfg = _cfg(card=["tests", "coverage", "lint", "docs", "docstrings", "release", "open-issues"])
    m = model.build_model(FULL, cfg, tmp_path)
    assert len(m["cards"]) == 7
    assert m["cards"][4]["y"] == m["cards"][0]["y"] + 110
    assert m["cards"][4]["x"] == 35


def test_text_is_escaped_and_long_description_wraps(tmp_path):
    data = {
        "project": {"name": "a<b>", "description": "x & y " * 50, "version": "1.0.0", "links": {}}
    }
    svg = model.render_card(data, _cfg(), tmp_path)
    root = ET.fromstring(svg)  # well-formed despite & and <
    assert "a&lt;b&gt;" in svg
    assert int(root.get("height")) >= 140 + 18 * 3


def test_logo_is_inlined(tmp_path):
    (tmp_path / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    m = model.build_model(FULL, _cfg(logo="logo.svg"), tmp_path)
    assert m["logo"].startswith("data:image/svg+xml;base64,")
    assert m["text_x"] == 130


def _chromium():
    for name in ("chromium", "chromium-browser", "google-chrome"):
        if shutil.which(name):
            return shutil.which(name)
    import glob

    hits = sorted(
        glob.glob(
            str(
                __import__("pathlib").Path.home()
                / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome"
            )
        )
    )
    return hits[-1] if hits else None


@pytest.mark.skipif(_chromium() is None, reason="no headless Chromium available")
def test_card_renders_in_chromium(tmp_path):
    cfg = _cfg(rows=["python", "builds", "extra"], extra=["proton-drive"])
    (tmp_path / "status.svg").write_text(model.render_card(FULL, cfg, tmp_path))
    out = tmp_path / "shot.png"
    subprocess.run(
        [
            _chromium(),
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--window-size=900,500",
            f"--screenshot={out}",
            (tmp_path / "status.svg").as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    assert out.stat().st_size > 5000


def test_links_are_spaced_by_their_text(tmp_path):
    # A fixed 130 px step left "PyPI" floating and "Documentation" nearly touching "Repository".
    links = model.build_model(FULL, _cfg(), tmp_path)["links"]
    gaps = [b["x"] - a["x"] for a, b in zip(links, links[1:], strict=False)]
    assert gaps[0] < 100  # after the short "PyPI"
    assert gaps[1] >= len("Documentation") * 8.8 + 20  # room for the long label


def test_card_details_fit_their_card(tmp_path):
    # Review I8: 13px mono is ~7.8 px/char and a card has ~160 px for text.
    data = {
        **FULL,
        "ci": {
            **FULL["ci"],
            "lint": {"status": "passing", "gates": ["ruff", "ruff-format", "actionlint"]},
        },
    }
    cfg = config.from_dict({"ci": {"coverage": {"package": "x", "floor": 80}}, "status": {}})
    for card in model.build_model(data, cfg, tmp_path)["cards"]:
        assert len(card["detail"]) <= model.DETAIL_CHARS, card
    lint = next(c for c in model.build_model(data, cfg, tmp_path)["cards"] if c["title"] == "LINT")
    assert lint["detail"].endswith("…")


def test_extra_columns_are_sized_by_their_text(tmp_path):
    data = {
        **FULL,
        "cal": {
            "label": "CAL",
            "items": [
                {"label": "Version", "value": "2026.9.1"},
                {"label": "Next", "value": "2026.10.12"},
            ],
            "state": "ok",
        },
    }
    cfg = _cfg(rows=["extra"], extra=["cal"])
    (panel,) = model.build_model(data, cfg, tmp_path)["panels"]
    first, second = panel["items"]
    assert second["dx"] - first["dx"] >= len("2026.9.1") * 13.2  # 22px mono value
    assert panel["x"] + panel["width"] <= 865


def test_only_the_logo_skips_escaping(tmp_path):
    # Review M10: any text starting "data:" was passed through unescaped.
    data = {
        "project": {"name": "x", "description": "data: <1% & rising", "version": "", "links": {}}
    }
    ET.fromstring(model.render_card(data, _cfg(), tmp_path))


def test_logo_outside_the_repo_is_never_inlined(tmp_path):
    # Review M16: an absolute or ../ logo path could inline any runner file into a public SVG.
    secret = tmp_path / "secret.txt"
    secret.write_text("GH_TOKEN=x")
    repo = tmp_path / "repo"
    repo.mkdir()
    for logo in (str(secret), "../secret.txt"):
        assert model.build_model(FULL, _cfg(logo=logo), repo)["logo"] == ""


def test_subprojects_panel(tmp_path):
    data = {
        "project": {"name": "python-dev", "description": "", "version": "", "links": {}},
        "ci": {
            "subprojects": [
                {"name": "scrapetool", "status": "passing"},
                {"name": "videoapp_ng", "status": "failing"},
            ]
        },
    }
    cfg = config.from_dict({"status": {"rows": ["subprojects"]}})
    m = model.build_model(data, cfg, tmp_path)
    assert [p["kind"] for p in m["panels"]] == ["subprojects"]
    svg = model.render_card(data, cfg, tmp_path)
    assert "videoapp_ng" in svg


def test_not_run_subprojects_are_muted_not_red(tmp_path):
    data = {
        "project": {"name": "p", "description": "", "version": "", "links": {}},
        "ci": {"subprojects": [{"name": "c", "status": "not run"}]},
    }
    svg = model.render_card(data, config.from_dict({"status": {"rows": ["subprojects"]}}), tmp_path)
    assert 'class="muted"' in svg and 'class="fail"' not in svg


# --- components layout ------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "status"


def _single_data():
    data = json.loads(json.dumps(FULL))
    data["ci"]["subprojects"] = [
        {"name": "a", "status": "passing"},
        {"name": "b", "status": "failing"},
    ]
    return data


def _single_cfg(**extra):
    return _cfg(
        card=["tests", "coverage", "lint", "docs"],
        rows=["python", "builds", "subprojects", "extra"],
        extra=["proton-drive"],
        **extra,
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_single_layout_unchanged(tmp_path, explicit):
    """Golden captured before the components layout existed: model and SVG are byte-identical."""
    cfg = _single_cfg(**({"layout": "single"} if explicit else {}))
    expected = (FIXTURES / "single-model.json").read_text(encoding="utf-8")
    got = model.build_model(_single_data(), cfg, tmp_path)
    assert "blocks" not in got
    assert json.dumps(got, indent=2, sort_keys=True) + "\n" == expected
    assert model.render_card(_single_data(), cfg, tmp_path) == (
        (FIXTURES / "single.svg").read_text(encoding="utf-8")
    )


def _components_cfg():
    return config.from_dict(
        {
            "status": {"layout": "components"},
            "subprojects": [
                {"name": "cdn", "path": "src/cdn", "release": True},
                {"name": "flightsearch", "path": "src/flightsearch", "release": True},
                {"name": "plain", "path": "src/plain"},
            ],
        }
    )


def _components_data():
    return {
        "project": {
            "name": "arcturus-stack",
            "description": "",
            "version": "",
            "links": {},
            "components": [
                {"name": "cdn", "version": "0.4.1"},
                {"name": "flightsearch", "version": ""},
            ],
        },
        "ci": {
            "lint": {"status": "passing", "gates": ["ruff"]},
            "subproject_legs": {
                "cdn": {"status": "passing", "tests": {"passed": 9, "failed": 0, "total": 10}},
                "flightsearch": {"status": "failing", "tests": {"failed": 2, "total": 5}},
            },
        },
        "docs": {"build": "passing"},
    }


def test_component_blocks_built(tmp_path):
    m = model.build_model(_components_data(), _components_cfg(), tmp_path)
    top, cdn, fs = m["blocks"]
    assert (
        top["name"] == "Repository"
        and top["docs"]["value"] == "✓ Built"
        and top["lint"]["state"] == "pass"
    )
    assert (cdn["name"], cdn["version"]) == ("cdn", "0.4.1")  # unreleased "plain" has no block
    assert cdn["tests"] == {"value": "✓ 10", "state": "pass"}
    assert fs["tests"] == {"value": "✗ 2 failed", "state": "fail"}
    assert "docs" not in cdn and "docs" not in fs  # the docs build is not per component
    assert m["cards"] == []
    assert len({(b["x"], b["y"]) for b in m["blocks"]}) == 3
    svg = model.render_card(_components_data(), _components_cfg(), tmp_path)
    assert "COMPONENTS" in svg and "v0.4.1" in svg and "2 failed" in svg


def test_untouched_component_keeps_last_result(tmp_path):
    from ghtools.status import fragments as fr

    previous = {
        "subproject_legs": {
            "cdn": {"status": "passing", "tests": {"passed": 9, "failed": 0, "total": 9}},
            "flightsearch": {"status": "failing", "tests": {"passed": 0, "failed": 3, "total": 3}},
        }
    }
    leg = fr.leg(
        "3.12", "ubuntu-latest", "success", "success",
        {"passed": 4, "failed": 0, "skipped": 0, "total": 4}, None, True, None, subproject="cdn",
    )  # fmt: skip
    ci = fr.ci_fragment([leg], ["ruff"], previous=previous, in_tree=["cdn", "flightsearch"])
    data = {**_components_data(), "ci": ci}
    _, cdn, fs = model.build_model(data, _components_cfg(), tmp_path)["blocks"]
    assert cdn["tests"]["value"] == "✓ 4"  # this run touched cdn
    assert fs["tests"] == {"value": "✗ 3 failed", "state": "fail"}  # carried from last run


def test_component_without_tag_shows_no_version(tmp_path, git_repo):
    from ghtools.status import fragments as fr

    git_repo.commit("chore: init", {"src/cdn/a.txt": "a"})
    git_repo.tag("cdn/0.2.0")
    cfg = _components_cfg()
    frag = fr.project_fragment(cfg, git_repo.path, None, None)
    assert frag["components"] == [
        {"name": "cdn", "version": "0.2.0"},
        {"name": "flightsearch", "version": ""},
    ]
    assert "components" not in fr.project_fragment(config.from_dict({}), git_repo.path, None, None)
    data = {**_components_data(), "project": frag}
    svg = model.render_card(data, cfg, tmp_path)
    assert ">v0.2.0<" in svg and ">v0.0.0<" not in svg and ">v<" not in svg
    assert svg.count(">v") == 1  # only cdn shows a version
    assert svg.count(">not released<") == 1  # the untagged component says so
    assert [b["version"] for b in model.build_model(data, cfg, tmp_path)["blocks"]][2] == ""
