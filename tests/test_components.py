from __future__ import annotations

import pytest

from ghtools import ci, components, config
from ghtools.cli import main
from ghtools.errors import ConfigError


def _cfg(*rows):
    return config.from_dict({"subprojects": list(rows)})


def test_no_release_keys_no_components():
    cfg = _cfg({"name": "a", "path": "a"}, {"name": "b", "path": "b", "needs": ["a"]})
    assert components.released(cfg) == []
    assert components.released(config.from_dict({})) == []


def test_defaults_derive_from_path():
    (c,) = components.released(_cfg({"name": "cdn", "path": "src/cdn/", "release": True}))
    assert c == components.Component(
        "cdn", "src/cdn", "src/cdn/pyproject.toml", "src/cdn/CHANGELOG.md", "{name}/{version}", ()
    )
    assert c.tag_for("0.4.1") == "cdn/0.4.1"


def test_overrides_are_kept():
    (c,) = components.released(
        _cfg(
            {
                "name": "x",
                "path": "x",
                "release": True,
                "version-file": "x/package.json",
                "changelog": "x/HISTORY.md",
                "tag": "{name}-v{version}",
            }
        )
    )
    assert (c.version_file, c.changelog, c.tag_for("1.0.0")) == (
        "x/package.json",
        "x/HISTORY.md",
        "x-v1.0.0",
    )


def test_needs_paths_transitive():
    cfg = _cfg(
        {"name": "base", "path": "lib/base"},
        {"name": "mid", "path": "lib/mid", "needs": ["base"]},
        {"name": "top", "path": "app", "needs": ["mid", "base"], "release": True},
    )
    (c,) = components.released(cfg)
    assert c.needs_paths == ("lib/base", "lib/mid")


def test_submodule_component_excluded():
    cfg = _cfg(
        {"name": "vendored", "path": "vendor/v", "kind": "submodule", "release": True},
        {"name": "own", "path": "own", "release": True},
    )
    assert [c.name for c in components.released(cfg)] == ["own"]


@pytest.mark.parametrize(
    "tag", ["{name}", "v1", "{name}/{version}/{other}", "{version!r}", "{version:>3}", "{"]
)
def test_tag_template_validated(tag):
    with pytest.raises(ConfigError, match="tag"):
        _cfg({"name": "a", "path": "a", "release": True, "tag": tag})


def test_tag_template_accepts_version_only():
    _cfg({"name": "a", "path": "a", "tag": "release-{version}"})


@pytest.mark.parametrize("tag", ["v{version}", "release-{version}"])
def test_released_component_tag_needs_name(tag):
    with pytest.raises(ConfigError, match=r"subprojects\[0\]\.tag.*\{name\}"):
        _cfg({"name": "a", "path": "a", "release": True, "tag": tag})
    _cfg({"name": "a", "path": "a", "release": True, "tag": "{name}-v{version}"})


def test_release_root_defaults_true():
    assert config.from_dict({}).get("release.root") is True
    assert config.from_dict({"release": {"root": False}}).get("release.root") is False


ROW = {"name": "ms", "path": "ms", "tests": "ms/tests", "test-command": "./run-tests.sh"}


def test_test_command_verbatim():
    cfg = _cfg(ROW, {"name": "plain", "path": "plain"})
    assert ci.test_command(cfg, "ms") == "./run-tests.sh"
    # Not pytest, so markers cannot apply: more tests run, never fewer.
    assert ci.test_command(cfg, "ms", ["tui"]) == "./run-tests.sh"
    assert ci.test_command(cfg, "plain", ["tui"]).endswith("-m 'not tui'")


def test_install_skipped_for_test_command():
    cfg = _cfg(ROW)
    assert ci.install_command(cfg, "ms") == []
    with_install = _cfg({**ROW, "install": ["-e ./ms[test]"]})
    assert ci.install_command(with_install, "ms")[-5:-2] == ["install", "-e", "./ms[test]"]
    needing = _cfg({"name": "lib", "path": "lib"}, {**ROW, "needs": ["lib"]})
    assert ci.install_command(needing, "ms")[-5:-2] == ["install", "-e", "./lib"]


def test_cli_install_with_nothing_to_do(tmp_path, monkeypatch):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text(
        '[[subprojects]]\nname = "ms"\npath = "ms"\ntest-command = "./run.sh"\n'
    )
    (tmp_path / "ms").mkdir()
    monkeypatch.chdir(tmp_path)
    assert main(["ci", "install", "--subproject", "ms"]) == 0


# --- deciding and preparing releases -------------------------------------------------------

CDN = {"name": "cdn", "path": "src/cdn", "release": True}
TS = {"name": "trainsearch", "path": "src/trainsearch", "release": True, "needs": ["cdn"]}
LIB = {"name": "cdn", "path": "src/cdn"}  # an unreleased row, for a needs target


def _repo(git_repo, *rows):
    cfg = _cfg(
        *(rows or (CDN, {"name": "trainsearch", "path": "src/trainsearch", "release": True}))
    )
    git_repo.commit("chore: init", {"src/cdn/a.txt": "a", "src/trainsearch/b.txt": "b"})
    return cfg


def _decide(cfg, git_repo, **kw):
    return {d.component.name: d for d in components.decide(cfg, git_repo.path, **kw)}


def test_only_touched_component_bumps(git_repo):
    cfg = _repo(git_repo)
    git_repo.tag("cdn/0.4.0")
    git_repo.tag("trainsearch/0.11.0")
    git_repo.commit("fix(cdn): thing", {"src/cdn/a.txt": "a2"})
    got = _decide(cfg, git_repo)
    assert got["cdn"].release and got["cdn"].version == "0.4.1" and got["cdn"].tag == "cdn/0.4.1"
    assert got["cdn"].previous == "cdn/0.4.0"
    assert not got["trainsearch"].release


def test_needs_change_bumps_dependent(git_repo):
    cfg = _repo(git_repo, CDN, TS)
    git_repo.tag("cdn/0.4.0")
    git_repo.tag("trainsearch/0.11.0")
    git_repo.commit("feat: cdn thing", {"src/cdn/a.txt": "a2"})
    got = _decide(cfg, git_repo)
    assert got["cdn"].version == "0.5.0"
    assert got["trainsearch"].version == "0.12.0"


def test_chore_only_no_release(git_repo):
    cfg = _repo(git_repo)
    git_repo.tag("cdn/0.4.0")
    git_repo.commit("chore: tidy", {"src/cdn/a.txt": "a2"})
    git_repo.commit("feat: elsewhere", {"other/x.txt": "x"})
    assert not _decide(cfg, git_repo)["cdn"].release


def test_release_commit_does_not_bump(git_repo):
    cfg = _repo(git_repo)
    git_repo.tag("cdn/0.4.0")
    git_repo.commit("fix(cdn): thing", {"src/cdn/a.txt": "a2"})
    git_repo.commit("chore(release): cdn 0.4.1 [skip ci]", {"src/cdn/a.txt": "a3"})
    git_repo.tag("cdn/0.4.1")
    assert not _decide(cfg, git_repo)["cdn"].release


def test_breaking_under_one_is_minor(git_repo):
    cfg = _repo(git_repo)
    git_repo.tag("cdn/0.4.0")
    git_repo.commit("refactor: x\n\nBREAKING CHANGE: gone", {"src/cdn/a.txt": "a2"})
    assert _decide(cfg, git_repo)["cdn"].version == "0.5.0"
    git_repo.tag("cdn/1.0.0")
    git_repo.commit("fix!: y", {"src/cdn/a.txt": "a3"})
    assert _decide(cfg, git_repo)["cdn"].version == "2.0.0"


def test_component_tags_ignored_by_root(git_repo):
    from ghtools import gitutil, release

    cfg = _repo(git_repo)
    git_repo.tag("v1.0.0")
    git_repo.tag("cdn/9.0.0")
    git_repo.commit("fix: root thing")
    assert gitutil.latest_tag(git_repo.path) == "v1.0.0"
    assert release.decide(cfg, git_repo.path).tag == "v1.0.1"
    # and the component's range starts at its own tag, not the root's
    assert components.latest_tag(components.released(cfg)[0], git_repo.path) == "cdn/9.0.0"


def test_similar_name_not_matched(git_repo):
    cfg = _repo(git_repo)
    c = components.released(cfg)[0]
    git_repo.tag("cdnx/1.0.0")
    git_repo.tag("xcdn/1.0.0")
    git_repo.tag("cdn/1.0.0-beta")
    assert components.latest_tag(c, git_repo.path) == "cdn/1.0.0-beta"
    git_repo.tag("cdn/0.9.0")
    git_repo.tag("cdn/1.0.0")
    git_repo.tag("cdn/1.0")
    assert components.latest_tag(c, git_repo.path) == "cdn/1.0.0"


def test_prerelease_continues_in_channel(git_repo):
    cfg = _repo(git_repo)
    git_repo.tag("cdn/1.1.0-alpha")
    git_repo.commit("fix(cdn): x", {"src/cdn/a.txt": "a2"})
    assert _decide(cfg, git_repo)["cdn"].version == "1.1.0-alpha.1"


def test_never_released_starts_from_zero(git_repo):
    cfg = _repo(git_repo)
    git_repo.commit("feat(cdn): first", {"src/cdn/a.txt": "a2"})
    got = _decide(cfg, git_repo)["cdn"]
    assert got.version == "0.1.0" and got.previous == ""


def test_change_arriving_through_merge_commit_counts(git_repo):
    cfg = _repo(git_repo)
    git_repo.tag("cdn/0.4.0")
    git_repo.tag("trainsearch/0.11.0")
    git_repo.run("checkout", "-q", "-b", "feature")
    git_repo.commit("feat(cdn): via pr", {"src/cdn/a.txt": "a2"})
    git_repo.commit("fix(trainsearch): unrelated", {"src/trainsearch/b.txt": "b2"})
    git_repo.run("checkout", "-q", "main")
    git_repo.commit("fix: on main meanwhile", {"elsewhere.txt": "e"})
    git_repo.run("merge", "--no-ff", "-q", "feature", "-m", "Merge pull request #1 from x/feature")
    got = _decide(cfg, git_repo)
    assert got["cdn"].version == "0.5.0"
    assert got["trainsearch"].version == "0.11.1"
    only = [
        m.strip() for m in components.log_messages("cdn/0.4.0..HEAD", git_repo.path, ["src/cdn"])
    ]
    assert only == ["feat(cdn): via pr"]


def test_root_false_no_root_release(git_repo):
    cfg = config.from_dict({"release": {"root": False}})
    git_repo.commit("feat: a")
    from ghtools import release

    d = release.decide(cfg, git_repo.path)
    assert not d.release and d.reason == "release.root = false"


def test_resume_when_tag_has_no_release(git_repo):
    cfg = _repo(git_repo)
    git_repo.run("tag", "-a", "cdn/0.4.0", "-m", "Release cdn/0.4.0")
    got = _decide(cfg, git_repo, has_release=lambda tag: False)["cdn"]
    assert got.release and got.resume and got.version == "0.4.0"
    assert not _decide(cfg, git_repo, has_release=lambda tag: True)["cdn"].resume
    git_repo.tag("cdn/0.4.1")  # hand-made tag: never resumed
    assert not _decide(cfg, git_repo, has_release=lambda tag: False)["cdn"].resume


_PYPROJECT = '[project]\nname = "cdn"\nversion = "0.4.0"\n'
_LOG = "# Changelog\n\n## [Unreleased]\n"


def _prepared(git_repo):
    cfg = _cfg(
        CDN,
        {
            "name": "trainsearch",
            "path": "src/trainsearch",
            "release": True,
            "version-file": "src/trainsearch/manifest.json",
        },
    )
    git_repo.commit(
        "chore: init",
        {
            "src/cdn/pyproject.toml": _PYPROJECT,
            "src/cdn/CHANGELOG.md": _LOG,
            "src/trainsearch/manifest.json": '{\n    "version": "0.11.0"\n}\n',
            "src/trainsearch/CHANGELOG.md": _LOG,
        },
    )
    git_repo.tag("cdn/0.4.0")
    git_repo.tag("trainsearch/0.11.0")
    return cfg


def test_prepare_writes_version_and_changelog(git_repo):
    from datetime import date

    cfg = _prepared(git_repo)
    git_repo.commit("fix(cdn): thing", {"src/cdn/a.txt": "x"})
    git_repo.commit("feat(trainsearch): newer", {"src/trainsearch/b.txt": "x"})
    decisions = components.decide(cfg, git_repo.path)
    changed = components.prepare(cfg, git_repo.path, decisions, date(2026, 11, 1))
    assert changed == [
        "src/cdn/pyproject.toml",
        "src/cdn/CHANGELOG.md",
        "src/trainsearch/manifest.json",
        "src/trainsearch/CHANGELOG.md",
    ]
    assert 'version = "0.4.1"' in (git_repo.path / "src/cdn/pyproject.toml").read_text()
    assert (git_repo.path / "src/trainsearch/manifest.json").read_text() == (
        '{\n    "version": "0.12.0"\n}\n'
    )
    log = (git_repo.path / "src/cdn/CHANGELOG.md").read_text()
    assert "## [0.4.1] - 2026-11-01" in log and "- **cdn**: thing" in log
    assert (
        components.notes(cfg, git_repo.path, "cdn", "0.4.1") == "### Bug fixes\n\n- **cdn**: thing"
    )
    assert components.commit_message(decisions) == (
        "chore(release): cdn 0.4.1, trainsearch 0.12.0 [skip ci]"
    )


def test_changelog_section_only_own_commits(git_repo):
    from datetime import date

    cfg = _prepared(git_repo)
    git_repo.commit("fix(cdn): one", {"src/cdn/a.txt": "1"})
    git_repo.commit("feat(trainsearch): not mine", {"src/trainsearch/b.txt": "1"})
    git_repo.commit("feat(cdn): two", {"src/cdn/a.txt": "2"})
    git_repo.commit("docs: root only", {"README.md": "r"})
    components.prepare(cfg, git_repo.path, components.decide(cfg, git_repo.path), date(2026, 11, 1))
    section = components.notes(cfg, git_repo.path, "cdn", "0.5.0")
    assert "one" in section and "two" in section
    assert "not mine" not in section and "root only" not in section


def test_prepare_failure_leaves_tree_untouched(git_repo):
    from ghtools.errors import GhtoolsError

    cfg = _prepared(git_repo)
    git_repo.commit("fix(cdn): thing", {"src/cdn/a.txt": "x"})
    git_repo.commit("fix(trainsearch): thing", {"src/trainsearch/b.txt": "x"})
    (git_repo.path / "src/trainsearch/manifest.json").write_text("not json")
    before = (git_repo.path / "src/cdn/pyproject.toml").read_text()
    with pytest.raises(GhtoolsError):
        components.prepare(cfg, git_repo.path, components.decide(cfg, git_repo.path))
    assert (git_repo.path / "src/cdn/pyproject.toml").read_text() == before
    assert (git_repo.path / "src/cdn/CHANGELOG.md").read_text() == _LOG


def test_cli_components_decide_output(git_repo, tmp_path, monkeypatch):
    import json

    (git_repo.path / ".github").mkdir()
    git_repo.commit(
        "chore: init",
        {
            ".github/ghtools.toml": '[[subprojects]]\nname="cdn"\npath="src/cdn"\nrelease=true\n',
            "src/cdn/pyproject.toml": _PYPROJECT,
        },
    )
    git_repo.tag("cdn/0.4.0")
    git_repo.commit("fix(cdn): x", {"src/cdn/a.txt": "1"})
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert (
        main(["-C", str(git_repo.path), "release", "components", "decide", "--github-output"]) == 0
    )
    lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert lines["released"] == "true"
    assert lines["commit-message"] == "chore(release): cdn 0.4.1 [skip ci]"
    assert json.loads(lines["components"])[0]["tag"] == "cdn/0.4.1"


def test_non_json_version_file_is_a_clear_error(git_repo):
    from ghtools.errors import GhtoolsError

    cfg = _cfg({**CDN, "version-file": "src/cdn/VERSION"})
    git_repo.commit("chore: init", {"src/cdn/VERSION": "0.0.0\n"})
    git_repo.commit("feat(cdn): x", {"src/cdn/a.txt": "1"})
    decisions = components.decide(cfg, git_repo.path)
    with pytest.raises(
        GhtoolsError, match="cdn: src/cdn/VERSION is neither a .toml file nor a JSON"
    ):
        components.prepare(cfg, git_repo.path, decisions)
    git_repo.write("src/cdn/VERSION", "[1]")
    with pytest.raises(GhtoolsError, match="neither a .toml"):
        components.prepare(cfg, git_repo.path, decisions)


def _untagged(git_repo, text):
    cfg = _cfg(CDN)
    files = {"src/cdn/pyproject.toml": text} if text is not None else {"src/cdn/a.txt": "a"}
    git_repo.commit("chore: init", files)
    git_repo.commit("feat(cdn): x", {"src/cdn/a.txt": "1"})
    return cfg


def test_no_downgrade_from_version_file(git_repo):
    cfg = _untagged(git_repo, '[project]\nversion = "0.12.0"\n')
    d = components.decide(cfg, git_repo.path)[0]
    assert not d.release
    assert "0.1.0" in d.reason and "0.12.0" in d.reason and "tag cdn/0.12.0 first" in d.reason


@pytest.mark.parametrize("text", ['[project]\nversion = "0.0.0"\n', '[project]\nname = "x"\n'])
def test_zero_or_absent_version_releases(git_repo, text):
    cfg = _untagged(git_repo, text)
    assert components.decide(cfg, git_repo.path)[0].version == "0.1.0"


def test_missing_version_file_still_precondition_error(git_repo):
    from ghtools.errors import PreconditionError

    cfg = _untagged(git_repo, None)
    d = components.decide(cfg, git_repo.path)
    assert d[0].release
    with pytest.raises(PreconditionError):
        components.prepare(cfg, git_repo.path, d)


def _cli_repo(git_repo):
    (git_repo.path / ".github").mkdir()
    git_repo.commit(
        "chore: init",
        {
            ".github/ghtools.toml": '[[subprojects]]\nname="cdn"\npath="src/cdn"\nrelease=true\n',
            "src/cdn/pyproject.toml": _PYPROJECT,
        },
    )
    git_repo.tag("cdn/0.4.0")
    git_repo.commit("fix(cdn): x", {"src/cdn/a.txt": "1"})


def _prepare_cli(git_repo, rows):
    import json

    argv = ["-C", str(git_repo.path), "release", "components", "prepare"]
    return main([*argv, "--decisions", json.dumps(rows)])


def test_prepare_decisions_never_writes_a_resume_row_and_does_not_decide(
    git_repo, monkeypatch, capsys
):
    _cli_repo(git_repo)

    def boom(*a, **k):
        raise AssertionError("decide must not run")

    monkeypatch.setattr(components, "decide", boom)
    row = {"name": "cdn", "version": "0.4.1", "tag": "cdn/0.4.1", "previous": "cdn/0.4.0"}
    assert _prepare_cli(git_repo, [{**row, "release": True, "resume": True}]) == 0
    assert capsys.readouterr().out == ""
    assert 'version = "0.4.0"' in (git_repo.path / "src/cdn/pyproject.toml").read_text()
    assert _prepare_cli(git_repo, [{**row, "release": True, "resume": False}]) == 0
    assert capsys.readouterr().out.split() == ["src/cdn/pyproject.toml"]
    assert 'version = "0.4.1"' in (git_repo.path / "src/cdn/pyproject.toml").read_text()


def test_prepare_decisions_validates_rows(git_repo, capsys):
    _cli_repo(git_repo)
    bad = [
        (
            [{"name": "nope", "version": "1.0.0", "tag": "nope/1.0.0"}],
            "no released component 'nope'",
        ),
        ([{"name": "cdn"}], "needs a version"),
        ([{"name": "cdn", "version": "0.3.0", "tag": "cdn/0.3.0"}], "lower than 0.4.0"),
    ]
    for rows, message in bad:
        assert _prepare_cli(git_repo, rows) != 0
        assert message in capsys.readouterr().err
    assert 'version = "0.4.0"' in (git_repo.path / "src/cdn/pyproject.toml").read_text()


def test_same_basename_apidoc_packages_are_rejected():
    with pytest.raises(ConfigError, match="'cdn'.*same directory name"):
        _cfg({**CDN, "apidoc": ["src/cdn/a/pkg", "src/cdn/b/pkg"]})
