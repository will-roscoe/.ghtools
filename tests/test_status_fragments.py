"""Tests for ghtools.status.fragments."""

from __future__ import annotations

import pytest

from ghtools import config
from ghtools.status import fragments as fr

JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="a" tests="10" failures="1" errors="1" skipped="2" time="1.5"/>
<testsuite name="b" tests="5" failures="0" errors="0" skipped="0" time="0.25"/></testsuites>"""

COVERAGE = '<?xml version="1.0"?><coverage line-rate="0.9044" lines-covered="1" lines-valid="1"/>'


def test_parse_junit_and_coverage(tmp_path):
    (tmp_path / "j.xml").write_text(JUNIT)
    (tmp_path / "c.xml").write_text(COVERAGE)
    assert fr.parse_junit(tmp_path / "j.xml") == {
        "passed": 11,
        "failed": 2,
        "skipped": 2,
        "total": 15,
        "duration_s": 1.75,
    }
    assert fr.parse_coverage_xml(tmp_path / "c.xml") == 90.4
    assert fr.parse_junit(tmp_path / "missing.xml")["total"] == 0
    assert fr.parse_coverage_xml(tmp_path / "missing.xml") is None


def test_parse_interrogate_output():
    out = "RESULT: PASSED (minimum: 80.0%, actual: 92.3%)\n"
    assert fr.parse_interrogate(out) == 92.3
    assert fr.parse_interrogate("nothing here") is None


@pytest.mark.parametrize(
    ("runner", "expected"),
    [
        ("ubuntu-latest", ("Linux", "x86_64")),
        ("ubuntu-24.04-arm", ("Linux", "arm64")),
        ("macos-latest", ("macOS", "arm64")),
        ("macos-15-intel", ("macOS", "x86_64")),
        ("macos-13", ("macOS", "x86_64")),
        ("windows-latest", ("Windows", "x86_64")),
    ],
)
def test_runner_platform(runner, expected):
    assert fr.runner_platform(runner) == expected


def test_ci_fragment_merges_legs():
    legs = [
        fr.leg(
            "3.9",
            "ubuntu-latest",
            "success",
            "success",
            {"passed": 5, "failed": 0, "skipped": 1, "total": 6, "duration_s": 1.0},
            91.0,
            True,
            88.0,
        ),
        fr.leg(
            "3.13",
            "ubuntu-latest",
            "failure",
            "",
            {"passed": 4, "failed": 1, "skipped": 1, "total": 6, "duration_s": 1.0},
            None,
            False,
            None,
        ),
        fr.leg(
            "3.13",
            "macos-latest",
            "success",
            "",
            {"passed": 5, "failed": 0, "skipped": 1, "total": 6, "duration_s": 1.0},
            None,
            False,
            None,
        ),
    ]
    frag = fr.ci_fragment(legs, gates=["ruff", "ruff-format"])
    assert frag["source"] == "ci"
    assert frag["tests"]["passed"] == 5  # from the canonical leg
    assert frag["coverage"] == 91.0
    assert frag["docstrings"] == 88.0
    assert frag["lint"] == {"status": "passing", "gates": ["ruff", "ruff-format"]}
    assert frag["python"] == [
        {"version": "3.9", "status": "passing"},
        {"version": "3.13", "status": "failing"},
    ]
    assert frag["builds"] == {"Linux": {"x86_64": "failing"}, "macOS": {"arm64": "passing"}}


def test_ci_fragment_without_legs_is_empty():
    assert fr.ci_fragment([], gates=[]) == {}


def test_meaningful_ignores_volatile_keys():
    a = {"source": "x", "value": 1, "updated": "t1", "run": {"id": "1"}}
    b = {"source": "x", "value": 1, "updated": "t2", "run": {"id": "2"}}
    assert fr.meaningful(a) == fr.meaningful(b)


def test_project_fragment_reads_pyproject_and_tags(git_repo):
    git_repo.commit(
        "feat: a",
        {
            "pyproject.toml": (
                '[project]\nname = "protonfs"\ndescription = "Sync & store"\n'
                '[project.urls]\nDocumentation = "https://example.org/docs/"\n'
            )
        },
    )
    git_repo.tag("v2.3.0")
    cfg = config.from_dict({"release": {"publish": ["github", "pypi"]}})
    frag = fr.project_fragment(cfg, git_repo.path, version=None, open_issues=3)
    assert frag["name"] == "protonfs"
    assert frag["description"] == "Sync & store"
    assert frag["version"] == "2.3.0"
    assert frag["released"]  # commit date of the tag, YYYY-MM-DD
    assert frag["open_issues"] == 3
    assert frag["links"]["pypi"] == "https://pypi.org/project/protonfs/"
    assert frag["links"]["docs"] == "https://example.org/docs"


def test_custom_fragment_validates_state():
    frag = fr.custom_fragment(
        "proton-drive", "PROTON DRIVE", [("Pinned", "0.8.0"), ("Latest", "0.8.0")], "ok"
    )
    assert frag == {
        "source": "proton-drive",
        "label": "PROTON DRIVE",
        "items": [{"label": "Pinned", "value": "0.8.0"}, {"label": "Latest", "value": "0.8.0"}],
        "state": "ok",
        "updated": frag["updated"],
        "run": frag["run"],
    }
    with pytest.raises(ValueError, match="state"):
        fr.custom_fragment("x", "X", [], "purple")


def _junit(passed=1):
    return {"passed": passed, "failed": 0, "skipped": 0, "total": passed, "duration_s": 0}


def test_free_threaded_and_pypy_versions_sort_without_crashing():
    # Review M11: int() on "13t" crashed, so the status never published.
    legs = [
        fr.leg(v, "ubuntu-latest", "success", "", _junit(), None, False, None)
        for v in ["3.13t", "pypy3.10", "3.9", "3.13"]
    ]
    versions = [p["version"] for p in fr.ci_fragment(legs, [])["python"]]
    assert versions[:3] == ["3.9", "3.13", "3.13t"]
    assert "pypy3.10" in versions


@pytest.mark.parametrize("outcome", ["skipped", "cancelled", ""])
def test_gates_that_never_ran_are_unknown_not_failing(outcome):
    # Review M12: an install failure skips the Gates step; the card said "✗ Failing".
    frag = fr.ci_fragment(
        [fr.leg("3.12", "ubuntu-latest", "failure", outcome, _junit(), None, True, None)], ["ruff"]
    )
    assert frag["lint"]["status"] == "unknown"


def test_description_falls_back_to_the_github_one(tmp_path):
    # Review M17 (spec §6.3): HACS and lint repos have no pyproject description.
    cfg = config.from_dict({})
    frag = fr.project_fragment(
        cfg, tmp_path, version="1.0.0", open_issues=None, description="A HACS thing"
    )
    assert frag["description"] == "A HACS thing"


def test_ci_fragment_lists_subproject_results():
    legs = [
        fr.leg(
            "3.12",
            "ubuntu-latest",
            "success",
            "success",
            {"passed": 1, "failed": 0, "skipped": 0, "total": 1, "duration_s": 0},
            80.0,
            True,
            None,
            subproject="scrapetool",
        ),
        fr.leg(
            "3.12",
            "ubuntu-latest",
            "failure",
            "",
            {"passed": 0, "failed": 1, "skipped": 0, "total": 1, "duration_s": 0},
            None,
            False,
            None,
            subproject="videoapp_ng",
        ),
    ]
    assert fr.ci_fragment(legs, [])["subprojects"] == [
        {"name": "scrapetool", "status": "passing"},
        {"name": "videoapp_ng", "status": "failing"},
    ]


def _sub_leg(name, outcome, passed):
    t = {
        "passed": passed,
        "failed": 0 if outcome == "success" else 1,
        "skipped": 0,
        "total": passed + 1,
        "duration_s": 1.0,
    }
    return fr.leg("3.12", "ubuntu-latest", outcome, "", t, None, False, None, subproject=name)


def test_partial_runs_keep_the_other_subprojects_last_results():
    # Review D-I7: each run only tests the subprojects a change touched; the card must not flap.
    previous = fr.ci_fragment(
        [_sub_leg("a", "success", 5), _sub_leg("b", "failure", 3)], [], in_tree=["a", "b", "c"]
    )
    now = fr.ci_fragment(
        [_sub_leg("a", "success", 6)], [], previous=previous, in_tree=["a", "b", "c"]
    )
    assert now["subprojects"] == [
        {"name": "a", "status": "passing"},
        {"name": "b", "status": "failing"},
        {"name": "c", "status": "not run"},
    ]
    # An umbrella has no root leg: the TESTS card sums every subproject's latest results.
    assert now["tests"]["passed"] == 6 + 3
    assert now["coverage"] is None


def test_a_subproject_dropped_from_the_settings_is_not_carried():
    previous = fr.ci_fragment([_sub_leg("old", "success", 1)], [], in_tree=["old"])
    now = fr.ci_fragment([_sub_leg("a", "success", 1)], [], previous=previous, in_tree=["a"])
    assert [s["name"] for s in now["subprojects"]] == ["a"]


def _lint_leg(outcome):
    empty = {"passed": 0, "failed": 0, "skipped": 0, "total": 0, "duration_s": 0.0}
    return fr.leg(
        "3.12", "ubuntu-latest", outcome, outcome, empty, None, False, None, lint_only=True
    )


def test_a_lint_only_leg_reports_lint_and_nothing_else():
    # Lint-profile repos (bash-helpers) run gates but no tests: the card and badges get a lint
    # result, and no empty test count, Python row or build matrix.
    frag = fr.ci_fragment([_lint_leg("success")], gates=["make-lint"])
    assert frag["lint"] == {"status": "passing", "gates": ["make-lint"]}
    assert frag["tests"]["total"] == 0
    assert frag["python"] == [] and frag["builds"] == {}
    assert fr.ci_fragment([_lint_leg("failure")], gates=[])["lint"]["status"] == "failing"


def test_an_umbrellas_lint_comes_from_its_lint_only_leg():
    legs = [_sub_leg("scrapetool", "success", 3), _lint_leg("success")]
    frag = fr.ci_fragment(legs, gates=[], in_tree=["scrapetool"])
    assert frag["lint"]["status"] == "passing"
    assert frag["tests"]["passed"] == 3  # still the subprojects' sum
    assert frag["python"] == [{"version": "3.12", "status": "passing"}]
