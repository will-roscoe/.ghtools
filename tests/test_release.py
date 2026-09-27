"""Tests for ghtools.release against real repositories."""

from __future__ import annotations

import zipfile
from datetime import date

import pytest

from ghtools import config, release
from ghtools.cli import main
from ghtools.errors import CheckFailed

CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n\n- first\n"


def _cfg(**raw):
    return config.from_dict(raw)


def test_no_releasable_commits(git_repo):
    git_repo.commit("feat: a")
    git_repo.tag("v0.1.0")
    git_repo.commit("docs: b")
    d = release.decide(_cfg(), git_repo.path)
    assert d.release is False
    assert "since v0.1.0" in d.reason
    assert d.as_outputs()["released"] == "false"


def test_feature_bumps_minor(git_repo):
    git_repo.commit("feat: a")
    git_repo.tag("v0.1.0")
    git_repo.commit("feat(cli): b")
    d = release.decide(_cfg(), git_repo.path)
    assert (d.release, d.version, d.tag, d.current) == (True, "0.2.0", "v0.2.0", "v0.1.0")
    assert d.as_outputs() == {
        "released": "true",
        "version": "0.2.0",
        "tag": "v0.2.0",
        "reason": d.reason,
    }


def test_existing_tag_is_idempotent(git_repo, monkeypatch):
    git_repo.commit("feat: a")
    git_repo.tag("v0.1.0")
    git_repo.commit("fix: b")
    git_repo.tag("v0.1.1")
    # A concurrent run tagged v0.1.1 after this run read its "current" tag (the race the
    # guard exists for); any real v0.1.1 would otherwise itself be the latest tag.
    monkeypatch.setattr(release, "latest_tag", lambda _root: "v0.1.0")
    d = release.decide(_cfg(), git_repo.path)
    assert d.release is False
    assert "v0.1.1 already exists" in d.reason


def test_release_disabled(git_repo):
    git_repo.commit("feat: a")
    d = release.decide(_cfg(release={"enabled": False}), git_repo.path)
    assert d.release is False
    assert d.reason == "release.enabled = false"


def test_require_mode_fails_when_version_file_lags(git_repo):
    git_repo.commit("feat: a", {"pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n'})
    git_repo.tag("v0.1.0")
    git_repo.commit("feat: b")
    cfg = _cfg(version={"source": "pyproject", "bump": "require"})
    with pytest.raises(CheckFailed, match="pyproject.toml says 0.1.0 but the commits imply 0.2.0"):
        release.decide(cfg, git_repo.path)


def test_prepare_writes_version_and_changelog(git_repo):
    git_repo.commit(
        "feat: a",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            "CHANGELOG.md": CHANGELOG,
        },
    )
    git_repo.tag("v0.1.0")
    git_repo.commit("fix(io): read the thing")
    cfg = _cfg(version={"source": "pyproject"})
    changed = release.prepare(cfg, git_repo.path, "0.1.1", today=date(2026, 9, 27))
    assert changed == ["pyproject.toml", "CHANGELOG.md"]
    assert 'version = "0.1.1"' in (git_repo.path / "pyproject.toml").read_text()
    log = (git_repo.path / "CHANGELOG.md").read_text()
    assert "## [0.1.1] - 2026-09-27" in log
    assert "- **io**: read the thing" in log
    assert release.notes(cfg, git_repo.path, "0.1.1") == "### Bug fixes\n\n- **io**: read the thing"


def test_prepare_with_commit_false_writes_nothing(git_repo):
    git_repo.commit("feat: a", {"CHANGELOG.md": CHANGELOG})
    git_repo.commit("fix: b")
    assert release.prepare(_cfg(release={"commit": False}), git_repo.path, "0.1.1") == []
    assert (git_repo.path / "CHANGELOG.md").read_text() == CHANGELOG


def test_prepare_without_changelog_file(git_repo):
    git_repo.commit("feat: a")
    assert release.prepare(_cfg(), git_repo.path, "0.1.0") == []


def test_build_zip_contains_directory_contents(git_repo):
    git_repo.commit(
        "feat: a",
        {
            "custom_components/intercom/manifest.json": "{}",
            "custom_components/intercom/__init__.py": "",
        },
    )
    cfg = _cfg(
        profile="hacs", release={"publish": ["github-zip"], "zip": "custom_components/intercom"}
    )
    made = release.build(cfg, git_repo.path)
    assert [p.name for p in made] == ["intercom.zip"]
    with zipfile.ZipFile(made[0]) as zf:
        assert sorted(zf.namelist()) == ["__init__.py", "manifest.json"]


def test_cli_version_next_explain(git_repo, capsys):
    git_repo.commit("feat: a")
    git_repo.tag("v1.0.0")
    git_repo.commit("fix: b")
    git_repo.commit("docs: c")
    assert main(["-C", str(git_repo.path), "version", "next", "--explain"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "1.0.1\n"
    assert "patch  fix: b" in captured.err
    assert "none   docs: c" in captured.err
    assert main(["-C", str(git_repo.path), "version", "current"]) == 0
    assert capsys.readouterr().out == "v1.0.0\n"


def test_cli_release_decide_github_output(git_repo, tmp_path, monkeypatch):
    git_repo.commit("feat: a")
    out = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert main(["-C", str(git_repo.path), "release", "decide", "--github-output"]) == 0
    assert "released=true\nversion=0.1.0\ntag=v0.1.0\n" in out.read_text()


def test_cli_release_prepare_prints_changed_paths(git_repo, capsys):
    git_repo.commit("feat: a", {"CHANGELOG.md": CHANGELOG})
    git_repo.tag("v0.1.0")
    git_repo.commit("feat: b")
    assert main(["-C", str(git_repo.path), "release", "prepare", "0.2.0"]) == 0
    assert capsys.readouterr().out == "CHANGELOG.md\n"
