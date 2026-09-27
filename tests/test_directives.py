"""Commit-message directives (ported from sph-dev's resolve_directives.py tests)."""

from __future__ import annotations

import json

from ghtools.cli import main
from ghtools.directives import messages, resolve

DISPATCH = {"update-todo": "update-todo.yml"}


def kinds(messages, dispatch=DISPATCH, docs=True):
    return [(e.directive, e.kind) for e in resolve(messages, dispatch, docs)]


def test_bracket_form_detected():
    assert kinds(["fix: something [lint]"]) == [("lint:py", "force-ci")]


def test_slash_form_detected():
    assert kinds(["fix something /lint"]) == [("lint:py", "force-ci")]


def test_slash_form_mid_sentence():
    assert kinds(["fix /lint the errors in src"]) == [("lint:py", "force-ci")]


def test_test_and_test_full_force_ci():
    assert kinds(["[test]"]) == [("test:py", "force-ci")]
    assert kinds(["[test-full]"]) == [("test-full:py", "force-ci")]


def test_ci_forces_ci():
    assert kinds(["chore: rerun [ci]"]) == [("ci", "force-ci")]


def test_coverage_expands_to_both_code_and_docs():
    assert kinds(["[coverage]"]) == [
        ("coverage-code:py", "force-ci"),
        ("coverage-docs:py", "force-ci"),
    ]


def test_unmapped_lang_produces_warn_entry():
    effects = resolve(["[lint:f2]"], DISPATCH, True)
    assert [(e.directive, e.kind) for e in effects] == [("lint:f2", "warn")]
    assert "f2" in effects[0].message


def test_mixed_languages_split():
    assert kinds(["[lint:py,f2]"]) == [("lint:py", "force-ci"), ("lint:f2", "warn")]


def test_deduplicates_repeated_directives():
    assert kinds(["[lint] [lint]"]) == [("lint:py", "force-ci")]


def test_multiple_messages_deduplicated():
    assert kinds(["[lint]", "also [lint] here"]) == [("lint:py", "force-ci")]


def test_configured_dispatch():
    effects = resolve(["[update-todo]"], DISPATCH, True)
    assert [(e.directive, e.kind, e.target) for e in effects] == [
        ("update-todo", "dispatch", "update-todo.yml")
    ]


def test_unconfigured_name_is_not_a_directive():
    assert kinds(["[update-todo]"], dispatch={}) == []


def test_docs_is_a_notice_or_a_warning():
    assert kinds(["[docs:linkcheck,apidoc]"]) == [("docs", "notice")]
    assert kinds(["[docs]"], docs=False) == [("docs", "warn")]


def test_no_directives_returns_empty():
    assert kinds(["fix: boring commit with no directives"]) == []


def test_version_bump_is_not_a_directive():
    assert kinds(["chore: something [version-bump]"]) == []


def test_release_directive_is_not_a_ci_directive():
    assert kinds(["feat: x\n\n+:major"]) == []


def test_backtick_quoted_directive_does_not_actuate():
    # sph-dev PR #90's merge commit quoted several directives in markdown and triggered them all.
    msg = "docs: explain that `[ci]`, `[docs]` and `[update-todo]` still work"
    assert kinds([msg]) == []


def test_unquoted_directive_still_works_alongside_backticked_prose():
    msg = "fix: something [lint], as documented near `[docs]` elsewhere"
    assert kinds([msg]) == [("lint:py", "force-ci")]


def test_case_insensitive():
    assert kinds(["[CI]"]) == [("ci", "force-ci")]


ON = '[directives]\nenabled = true\ndispatch = { "update-todo" = "update-todo.yml" }\n'


def test_messages_range_and_new_branch(git_repo):
    git_repo.commit("feat: a", {".github/ghtools.toml": ON})
    before = git_repo.run("rev-parse", "HEAD").strip()
    git_repo.commit("fix: b [ci]")
    git_repo.commit("docs: c\n\nbody [update-todo]")
    assert [m.splitlines()[0] for m in messages(git_repo.path, before)] == [
        "docs: c",
        "fix: b [ci]",
    ]
    assert [m.splitlines()[0] for m in messages(git_repo.path, "0" * 40)] == ["docs: c"]
    assert [m.splitlines()[0] for m in messages(git_repo.path, "deadbeef" * 5)] == ["docs: c"]


def test_resolve_writes_outputs(git_repo, tmp_path, monkeypatch, capsys):
    git_repo.commit("feat: a", {".github/ghtools.toml": ON})
    before = git_repo.run("rev-parse", "HEAD").strip()
    git_repo.commit("fix: b [ci] [lint:f2]")
    git_repo.commit("chore: c [update-todo]")
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert (
        main(
            [
                "-C",
                str(git_repo.path),
                "directives",
                "resolve",
                "--before",
                before,
                "--github-output",
            ]
        )
        == 0
    )
    written = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert written["force-ci"] == "true"
    assert json.loads(written["dispatch"]) == ["update-todo.yml"]
    assert "::warning::no tool for lint:f2" in capsys.readouterr().out


def test_disabled_is_silent(git_repo, tmp_path, monkeypatch, capsys):
    git_repo.commit("feat: a [ci]", {".github/ghtools.toml": "[directives]\nenabled = false\n"})
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    main(["-C", str(git_repo.path), "directives", "resolve", "--before", "", "--github-output"])
    assert out.read_text() == "force-ci=false\ndispatch=[]\n"
    assert capsys.readouterr().out == ""
