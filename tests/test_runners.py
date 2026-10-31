"""Tests for ghtools.runners: per-leg runner resolution against a python build snapshot."""

from __future__ import annotations

import pytest

from ghtools import config, runners, subprojects

UBUNTU_OLD = {"linux/x64": ["22.04", "24.04"], "linux/arm64": ["22.04", "24.04"]}
EVERYWHERE = {
    "linux/x64": ["22.04", "24.04", "26.04"],
    "linux/arm64": ["22.04", "24.04", "26.04"],
    "darwin/arm64": ["*"],
    "darwin/x64": ["*"],
    "win32/x64": ["*"],
    "win32/arm64": ["*"],
}
SNAP = {
    "3.9": {**UBUNTU_OLD, "darwin/x64": ["*"], "win32/x64": ["*"]},
    "3.10": EVERYWHERE,
    "3.13": EVERYWHERE,
}


def resolve(python, labels, rules=None):
    return runners.resolve_matrix(python, labels, snapshot=SNAP, rules=rules or [])


def test_parse_labels():
    p = runners.parse_runner
    assert (p("ubuntu-latest").os, p("ubuntu-latest").version) == ("linux", "26.04")
    assert p("ubuntu-24.04").arch == "x64"
    assert (p("ubuntu-24.04-arm").arch, p("ubuntu-24.04-arm").version) == ("arm64", "24.04")
    assert (p("macos-latest").os, p("macos-latest").arch) == ("darwin", "arm64")
    assert p("macos-15-intel").arch == "x64"
    assert p("macos-14").arch == "arm64"
    assert (p("windows-latest").os, p("windows-latest").arch) == ("win32", "x64")
    assert p("windows-11-arm").arch == "arm64"
    assert p("my-self-hosted-box") is None


def test_old_python_moves_off_ubuntu_latest():
    include, notes = resolve(["3.9", "3.13"], ["ubuntu-latest"])
    assert include == [
        {"python": "3.9", "runner": "ubuntu-24.04"},
        {"python": "3.13", "runner": "ubuntu-latest"},
    ]
    assert [n.kind for n in notes] == ["notice"]
    assert "ubuntu-24.04" in notes[0].message


def test_arm_leg_stays_arm():
    include, _ = resolve(["3.9"], ["ubuntu-latest", "ubuntu-24.04-arm"])
    assert include == [
        {"python": "3.9", "runner": "ubuntu-24.04"},
        {"python": "3.9", "runner": "ubuntu-24.04-arm"},
    ]
    include, _ = resolve(["3.9"], ["ubuntu-26.04-arm"])
    assert include == [{"python": "3.9", "runner": "ubuntu-24.04-arm"}]


def test_available_everywhere_keeps_labels():
    include, notes = resolve(["3.13"], ["ubuntu-latest", "macos-latest", "windows-latest"])
    assert include == [
        {"python": "3.13", "runner": "ubuntu-latest"},
        {"python": "3.13", "runner": "macos-latest"},
        {"python": "3.13", "runner": "windows-latest"},
    ]
    assert notes == []


def test_missing_arch_drops_leg_with_warning():
    include, notes = resolve(["3.9"], ["ubuntu-latest", "macos-latest"])
    assert include == [{"python": "3.9", "runner": "ubuntu-24.04"}]
    warn = [n for n in notes if n.kind == "warning"]
    assert [n.message for n in warn] == [
        "skipped: no python 3.9 build for darwin/arm64 (macos-latest)"
    ]


def test_any_patch_counts_and_unknown_python_is_kept():
    # snapshot is the union over patches, so 3.13 on macOS is allowed
    include, notes = resolve(["3.13"], ["macos-latest"])
    assert include == [{"python": "3.13", "runner": "macos-latest"}] and notes == []
    include, _ = resolve(["3.99", "3.13t"], ["ubuntu-latest"])
    assert [leg["runner"] for leg in include] == ["ubuntu-latest", "ubuntu-latest"]
    assert runners.resolve_matrix(["3.9"], ["self-hosted-x"], snapshot=SNAP, rules=[])[0] == [
        {"python": "3.9", "runner": "self-hosted-x"}
    ]


def test_version_compare_is_numeric():
    assert runners.version_matches("3.10", ">3.9")
    assert not runners.version_matches("3.10", "<=3.9")
    assert runners.version_matches("3.9", "<=3.9")
    assert runners.version_matches("24.04", "<=24.04")
    assert not runners.version_matches("26.04", "<=24.04")
    assert runners.version_matches("3.9", "3.9") and runners.version_matches("3.9", "==3.9")
    with pytest.raises(ValueError):
        runners.version_matches("3.9", "~3.9")


def test_rule_forces_older_os_for_matching_python_only():
    rule = {"match": {"python": "<=3.10", "os": "linux"}, "require": {"version": "<=24.04"}}
    include, notes = resolve(["3.10", "3.13"], ["ubuntu-latest"], [rule])
    assert include == [
        {"python": "3.10", "runner": "ubuntu-24.04"},
        {"python": "3.13", "runner": "ubuntu-latest"},
    ]
    include, _ = resolve(["3.10"], ["ubuntu-22.04"], [rule])
    assert include == [{"python": "3.10", "runner": "ubuntu-22.04"}]


def test_rule_with_no_satisfying_runner_drops_leg():
    rule = {"match": {"python": "3.13", "os": "darwin"}, "require": {"version": "<=15"}}
    include, notes = resolve(["3.13"], ["macos-latest"], [rule])
    assert include == [] and notes[0].kind == "warning"


def test_duplicate_legs_collapse():
    include, _ = resolve(["3.9"], ["ubuntu-latest", "ubuntu-24.04"])
    assert include == [{"python": "3.9", "runner": "ubuntu-24.04"}]


def test_snapshot_from_manifest_unions_stable_patches_and_skips_freethreaded():
    def f(platform, arch, ver=None):
        row = {"platform": platform, "arch": arch}
        return {**row, "platform_version": ver} if ver else row

    manifest = [
        {"version": "3.13.18", "stable": True, "files": [f("linux", "x64", "24.04")]},
        {
            "version": "3.13.2",
            "stable": True,
            "files": [f("darwin", "arm64"), f("linux", "x64-freethreaded", "26.04")],
        },
        {"version": "3.14.0-rc.1", "stable": False, "files": [f("linux", "x64", "24.04")]},
    ]
    assert runners.snapshot_from_manifest(manifest) == {
        "3.13": {"darwin/arm64": ["*"], "linux/x64": ["24.04"]}
    }


def test_shipped_snapshot_and_table_load():
    snap = runners.load_snapshot()
    assert "linux/x64" in snap["3.12"]
    assert runners.parse_runner("ubuntu-latest") is not None


def test_export_resolves_runner(monkeypatch):
    monkeypatch.setattr(runners, "load_snapshot", lambda: SNAP)
    cfg = config.from_dict(
        {"ci": {"python": ["3.9", "3.13"], "runners": ["ubuntu-latest", "macos-latest"]}}
    )
    assert config.export(cfg)["matrix"]["include"] == [
        {"python": "3.9", "runner": "ubuntu-24.04"},
        {"python": "3.13", "runner": "ubuntu-latest"},
        {"python": "3.13", "runner": "macos-latest"},
    ]


def _umbrella(runner_list):
    return config.from_dict(
        {
            "profile": "umbrella",
            "ci": {"python": ["3.9"], "runners": runner_list},
            "subprojects": [{"name": "a", "path": "a"}],
        }
    )


def test_subproject_leg_moves_off_ubuntu_latest(monkeypatch, git_repo):
    monkeypatch.setattr(runners, "load_snapshot", lambda: SNAP)
    git_repo.commit("feat: a", {"a/x.py": "1"})
    m, _ = subprojects.matrix(_umbrella(["ubuntu-latest"]), git_repo.path, "", "HEAD")
    assert [(leg.get("subproject"), leg["runner"]) for leg in m["include"]] == [
        ("a", "ubuntu-24.04")
    ]


def test_subproject_leg_dropped_with_warning(monkeypatch, git_repo):
    monkeypatch.setattr(runners, "load_snapshot", lambda: SNAP)
    git_repo.commit("feat: a", {"a/x.py": "1"})
    notes: list = []
    m, any_legs = subprojects.matrix(
        _umbrella(["macos-latest"]), git_repo.path, "", "HEAD", notes=notes
    )
    assert m == {"include": [subprojects.PLACEHOLDER]} and not any_legs
    assert [n.kind for n in notes] == ["warning"]


def test_check_reports_unrunnable_legs(monkeypatch):
    monkeypatch.setattr(runners, "load_snapshot", lambda: SNAP)
    cfg = config.from_dict({"ci": {"python": ["3.9"], "runners": ["macos-latest"]}})
    assert [n.kind for n in runners.check(cfg)] == ["warning"]
