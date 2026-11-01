from __future__ import annotations

import pytest

from ghtools.cli import main, write_github_output
from ghtools.errors import CheckFailed, ConfigError, GhtoolsError, PreconditionError


def test_version_flag_prints_name(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("ghtools ")


def test_no_command_prints_help_and_returns_1(capsys):
    assert main([]) == 1
    assert "usage: ghtools" in capsys.readouterr().out


def test_exit_codes_match_spec():
    assert GhtoolsError.exit_code == 1
    assert ConfigError.exit_code == 1
    assert CheckFailed.exit_code == 2
    assert PreconditionError.exit_code == 3


def test_write_github_output_appends_lines(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    write_github_output({"a": "1", "b": "two"})
    write_github_output({"c": "3"})
    assert out.read_text() == "a=1\nb=two\nc=3\n"


def test_write_github_output_outside_actions_is_an_error(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    with pytest.raises(GhtoolsError, match="GITHUB_OUTPUT"):
        write_github_output({"a": "1"})


def test_errors_are_annotations_inside_github_actions(tmp_path, capsys, monkeypatch):
    # Review I9: CI failures must surface as ::error annotations naming the key or file.
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/ghtools.toml").write_text('profile = "django"\n')
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert main(["-C", str(tmp_path), "config", "check"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("::error::ghtools: profile: 'django' is not one of")
    monkeypatch.delenv("GITHUB_ACTIONS")
    main(["-C", str(tmp_path), "config", "check"])
    assert capsys.readouterr().err.startswith("ghtools: error: profile:")


# --- ci test --record -------------------------------------------------------------------------


def _record_repo(tmp_path, toml, test_cmd="true"):
    """A repo whose main is pushed to a bare `origin`, with `toml` plus `ci.test` committed."""
    import subprocess

    from conftest import GitRepo  # noqa: I001

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = GitRepo(tmp_path / "repo").init()
    repo.run("remote", "add", "origin", str(remote))
    body = f'[ci]\ntest = "{test_cmd}"\n' + toml
    repo.commit("init", {".github/ghtools.toml": body, "a.txt": "a"})
    repo.run("push", "-q", "origin", "main")
    return repo, remote


def _fake_bash(monkeypatch, code=0):
    """Make the shell test commands succeed (or exit `code`) and return the commands run."""
    import subprocess

    real = subprocess.run
    ran = []

    def run(argv, **kw):
        if argv[0] != "bash":
            return real(argv, **kw)
        ran.append(argv[-1])
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr("ghtools.cli.subprocess.run", run)
    return ran


def _tested_refs(remote):
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(remote), "for-each-ref", "refs/ghtools/tested/", "--format=%(refname)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def _meta(remote, ref):
    import json
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(remote), "show", f"{ref}:meta.json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def test_record_refuses_dirty_tree(tmp_path, capsys):
    repo, remote = _record_repo(tmp_path, "", test_cmd="touch ran.flag")
    repo.write("untracked.txt", "x")
    assert main(["-C", str(repo.path), "ci", "test", "--record"]) == 3
    assert "uncommitted" in capsys.readouterr().err
    assert not (repo.path / "ran.flag").exists()  # no tests ran
    assert _tested_refs(remote) == []


def test_record_pushes_after_pass(tmp_path, capsys):
    import json

    repo, remote = _record_repo(tmp_path, "")
    capsys.readouterr()
    assert main(["-C", str(repo.path), "ci", "test", "--record"]) == 0
    ref = capsys.readouterr().out.strip().splitlines()[-1]
    tree = repo.run("rev-parse", "HEAD^{tree}").strip()
    assert ref == f"refs/ghtools/tested/{tree}"
    assert _tested_refs(remote) == [ref]
    meta = _meta(remote, ref)
    assert meta["source"] == "local" and meta["deselected"] == []
    import subprocess

    leg = json.loads(
        subprocess.run(
            ["git", "-C", str(remote), "show", f"{ref}:legs/ghtools-status-leg-root.json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert leg["runner"] == "local" and leg["status"] == "passing" and leg["subproject"] == ""
    assert leg["python"].count(".") == 1


def test_no_record_on_failure(tmp_path):
    repo, remote = _record_repo(tmp_path, "", test_cmd="false")
    assert main(["-C", str(repo.path), "ci", "test", "--record"]) == 2
    assert _tested_refs(remote) == []


def test_record_uses_selection(tmp_path, monkeypatch):
    rule = '[[ci.select]]\nmarker = "tui"\nchanged = ["tui/**"]\n'
    repo, remote = _record_repo(tmp_path, rule, test_cmd="python -m pytest")
    ran = _fake_bash(monkeypatch)
    repo.run("switch", "-q", "-c", "feature")
    repo.commit("change", {"docs/readme.md": "x"})  # touches nothing the rule watches
    assert main(["-C", str(repo.path), "ci", "test", "--record"]) == 0
    (ref,) = _tested_refs(remote)
    assert _meta(remote, ref)["deselected"] == ["tui"]
    assert "and not tui'" in ran[0]  # the deselection is what the run used


def test_record_explicit_base_selects_against_it(tmp_path, monkeypatch):
    rule = '[[ci.select]]\nmarker = "tui"\nchanged = ["tui/**"]\n'
    repo, remote = _record_repo(tmp_path, rule, test_cmd="python -m pytest")
    _fake_bash(monkeypatch)
    base = repo.run("rev-parse", "HEAD").strip()
    repo.commit("change", {"tui/x.py": "x"})
    assert main(["-C", str(repo.path), "ci", "test", "--record", "--base", base]) == 0
    (ref,) = _tested_refs(remote)
    assert _meta(remote, ref)["deselected"] == []


def test_record_all_subprojects(tmp_path, monkeypatch):
    import subprocess

    toml = '[[subprojects]]\nname = "a"\npath = "a"\n[[subprojects]]\nname = "b"\npath = "b"\n'
    repo, remote = _record_repo(tmp_path, toml)
    ran = _fake_bash(monkeypatch)
    assert main(["-C", str(repo.path), "ci", "test", "--record"]) == 0
    assert len(ran) == 3  # the root, then a, then b
    assert "a" in ran[1] and "b" in ran[2]
    (ref,) = _tested_refs(remote)
    names = subprocess.run(
        ["git", "-C", str(remote), "ls-tree", "--name-only", f"{ref}:legs"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(names) == 3


def test_base_without_record_is_refused(tmp_path):
    repo, _ = _record_repo(tmp_path, "")
    assert main(["-C", str(repo.path), "ci", "test", "--base", "HEAD"]) == 3
