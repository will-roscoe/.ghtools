from __future__ import annotations

from ghtools import config, docs
from ghtools.cli import main


def test_build_commands_with_apidoc_and_strict():
    cfg = config.from_dict({"docs": {"enabled": True, "dir": "docs", "apidoc": "src/protonfs"}})
    assert docs.build_commands(cfg) == [
        [
            "sphinx-apidoc",
            "-f",
            "-o",
            "docs/api/protonfs",
            "src/protonfs",
            "--separate",
            "--module-first",
            "-q",
        ],
        ["sphinx-build", "-b", "html", "-W", "--keep-going", "docs", "docs/_build/html"],
    ]
    assert docs.html_dir(cfg) == "docs/_build/html"


def test_build_commands_not_strict_no_apidoc():
    cfg = config.from_dict({"docs": {"enabled": True, "dir": "doc", "strict": False}})
    assert docs.build_commands(cfg) == [["sphinx-build", "-b", "html", "doc", "doc/_build/html"]]


def test_prebuild_commands_run_before_sphinx():
    cfg = config.from_dict({"docs": {"enabled": True, "prebuild": ["make docs"]}})
    assert docs.build_commands(cfg)[0] == ["bash", "-o", "pipefail", "-c", "make docs"]


def _components(*rows):
    return config.from_dict({"docs": {"enabled": True, "dir": "doc"}, "subprojects": list(rows)})


def test_apidoc_per_component():
    cfg = _components(
        {"name": "cdn", "path": "src/cdn", "release": True, "apidoc": ["src/cdn/cdn"]},
        {"name": "web", "path": "src/web", "release": True, "apidoc": ["src/web/a", "src/web/b"]},
        {"name": "lib", "path": "lib", "apidoc": ["lib/lib"]},  # not released: ignored
    )
    cmds = docs.build_commands(cfg)
    outs = [(c[3], c[4]) for c in cmds if c[0] == "sphinx-apidoc"]
    assert outs == [
        ("doc/api/cdn", "src/cdn/cdn"),
        ("doc/api/web/a", "src/web/a"),
        ("doc/api/web/b", "src/web/b"),
    ]
    assert cmds[-1][0] == "sphinx-build"


def test_no_components_leaves_build_commands_unchanged():
    cfg = _components({"name": "lib", "path": "lib", "apidoc": ["lib/lib"]})
    assert docs.build_commands(cfg) == [
        ["sphinx-build", "-b", "html", "-W", "--keep-going", "doc", "doc/_build/html"]
    ]


# --- docs.after-release: which release commit to build ---------------------------------------

ROOT_SUBJECT = "chore(release): v1.2.0 [skip ci]"
COMPONENTS_SUBJECT = "chore(release): cdn 0.5.0, site 1.0.0 [skip ci]"


def _head(repo):
    return repo.run("rev-parse", "HEAD").strip()


def _released(repo, *subjects):
    """Commit `base`, then one commit per subject; return (base, [sha per subject])."""
    repo.commit("feat: tested work")
    base = _head(repo)
    shas = []
    for subject in subjects:
        repo.commit(subject)
        shas.append(_head(repo))
    return base, shas


def test_nothing_pushed_builds_nothing(git_repo):
    base, _ = _released(git_repo)
    commit, reason = docs.release_commit(git_repo.path, base, "HEAD", [])
    assert commit is None and "pushed no release commit" in reason


def test_root_release_commit_is_built(git_repo):
    base, (root,) = _released(git_repo, ROOT_SUBJECT)
    commit, reason = docs.release_commit(git_repo.path, base, "HEAD", [(root, ROOT_SUBJECT)])
    assert commit == root and root[:12] in reason


def test_components_release_commit_is_built(git_repo):
    base, (comp,) = _released(git_repo, COMPONENTS_SUBJECT)
    pushed = [(comp, COMPONENTS_SUBJECT)]
    assert docs.release_commit(git_repo.path, base, "HEAD", pushed)[0] == comp


def test_root_then_components_builds_the_components_commit(git_repo):
    base, (root, comp) = _released(git_repo, ROOT_SUBJECT, COMPONENTS_SUBJECT)
    pushed = [(root, ROOT_SUBJECT), (comp, COMPONENTS_SUBJECT)]
    assert docs.release_commit(git_repo.path, base, "HEAD", pushed)[0] == comp


def test_branch_moved_past_the_release_commit_builds_nothing(git_repo):
    base, (root,) = _released(git_repo, ROOT_SUBJECT)
    git_repo.commit("fix: pushed after the run started")
    commit, reason = docs.release_commit(git_repo.path, base, "HEAD", [(root, ROOT_SUBJECT)])
    assert commit is None and "moved" in reason


def test_root_commit_alone_is_refused_when_components_landed_on_it(git_repo):
    # Only the newest commit this run pushed may be the branch head.
    base, (root, _comp) = _released(git_repo, ROOT_SUBJECT, COMPONENTS_SUBJECT)
    assert docs.release_commit(git_repo.path, base, "HEAD", [(root, ROOT_SUBJECT)])[0] is None


def test_components_commit_alone_is_refused_when_its_parent_is_not_the_tested_commit(git_repo):
    # The root release commit sits between: claiming only the components' commit breaks the chain.
    base, (_root, comp) = _released(git_repo, ROOT_SUBJECT, COMPONENTS_SUBJECT)
    commit, reason = docs.release_commit(git_repo.path, base, "HEAD", [(comp, COMPONENTS_SUBJECT)])
    assert commit is None and "not the release commit this run made" in reason


def test_a_commit_with_another_subject_is_refused(git_repo):
    base, (other,) = _released(git_repo, "chore(release): v9.9.9 [skip ci]")
    commit, reason = docs.release_commit(git_repo.path, base, "HEAD", [(other, ROOT_SUBJECT)])
    assert commit is None and "v9.9.9" in reason


def test_a_subject_that_is_no_release_subject_is_refused(git_repo):
    base, (other,) = _released(git_repo, "fix: something")
    commit, reason = docs.release_commit(git_repo.path, base, "HEAD", [(other, "fix: something")])
    assert commit is None and "not a release commit subject" in reason


def test_a_merge_commit_is_refused(git_repo):
    git_repo.commit("feat: tested work")
    base = _head(git_repo)
    git_repo.run("checkout", "-q", "-b", "side")
    git_repo.commit("feat: side")
    git_repo.run("checkout", "-q", "main")
    git_repo.run("merge", "-q", "--no-ff", "side", "-m", ROOT_SUBJECT)
    merge = _head(git_repo)
    assert docs.release_commit(git_repo.path, base, "HEAD", [(merge, ROOT_SUBJECT)])[0] is None


def test_unknown_head_or_commit_builds_nothing(git_repo):
    base, (root,) = _released(git_repo, ROOT_SUBJECT)
    assert docs.release_commit(git_repo.path, base, "nope", [(root, ROOT_SUBJECT)])[0] is None
    assert docs.release_commit(git_repo.path, base, "HEAD", [("0" * 40, ROOT_SUBJECT)])[0] is None


def test_release_subject():
    assert docs.release_subject(ROOT_SUBJECT) and docs.release_subject(COMPONENTS_SUBJECT)
    assert not docs.release_subject("chore(release): v1.2.0")
    assert not docs.release_subject("fix: x [skip ci]")


def test_cli_release_commit_writes_outputs(git_repo, tmp_path, monkeypatch, capsys):
    base, (root, comp) = _released(git_repo, ROOT_SUBJECT, COMPONENTS_SUBJECT)
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    args = ["-C", str(git_repo.path), "docs", "release-commit", "--base", base, "--head", "HEAD"]
    full = [
        *args,
        *("--root-commit", root, "--root-tag", "v1.2.0"),
        *("--components-commit", comp, "--components-message", COMPONENTS_SUBJECT),
        "--github-output",
    ]
    assert main(full) == 0
    assert out.read_text().splitlines()[0] == f"commit={comp}"
    out.write_text("")
    capsys.readouterr()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    # Empty flags (a job that pushed nothing) leave nothing to build; the reason is a notice.
    assert main([*args, "--root-commit", "", "--components-commit", "", "--github-output"]) == 0
    assert out.read_text().splitlines()[0] == "commit="
    assert capsys.readouterr().out.startswith("::notice title=docs.after-release::")


def test_cli_release_commit_prints_the_sha(git_repo, capsys):
    base, (root,) = _released(git_repo, ROOT_SUBJECT)
    args = ["-C", str(git_repo.path), "docs", "release-commit", "--base", base, "--head", "HEAD"]
    assert main([*args, "--root-commit", root, "--root-tag", "v1.2.0"]) == 0
    assert capsys.readouterr().out.strip() == root
    # A commit without the subject it should have is a usage error.
    assert main([*args, "--root-commit", root]) == 1
    assert main([*args, "--components-commit", root]) == 1
