"""Tests for ghtools init on real repositories."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from ghtools import config, scaffold
from ghtools.errors import PreconditionError

CI = "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest -q\n"
CLAUDE = "jobs:\n  c:\n    steps:\n      - uses: anthropics/claude-code-action@v1\n"


def _python_repo(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n[tool.ruff]\n',
            ".github/workflows/ci.yml": CI,
            ".github/workflows/claude.yml": CLAUDE,
            ".github/scripts/compute_next_version.py": "#",
        },
    )
    return git_repo


def _quiet(*_a, **_k):
    return None


def test_classify_workflow():
    assert scaffold.classify_workflow(CI)[0] == "replace"
    assert scaffold.classify_workflow(CLAUDE) == (
        "keep",
        "not covered by ghtools (claude-code-action)",
    )
    assert scaffold.classify_workflow("jobs: {}\n") == ("keep", "not covered by ghtools")


def test_render_stub_is_valid_yaml_with_marker():
    text = scaffold.render_stub("master", pypi=False)
    assert text.startswith("# ghtools-stub: 1")
    doc = yaml.safe_load(text)
    assert doc[True]["push"]["branches"] == ["master"]  # PyYAML reads the `on` key as True
    assert (
        doc["jobs"]["pipeline"]["uses"] == "will-roscoe/.ghtools/.github/workflows/pipeline.yml@v1"
    )
    assert doc["jobs"]["pipeline"]["secrets"] == {"CODECOV_TOKEN": "${{ secrets.CODECOV_TOKEN }}"}
    assert "publish-pypi" not in doc["jobs"]


def test_render_stub_with_pypi_job():
    doc = yaml.safe_load(scaffold.render_stub("main", pypi=True, ref="main"))
    job = doc["jobs"]["publish-pypi"]
    assert job["environment"] == "pypi"
    assert job["permissions"] == {"id-token": "write"}
    assert job["steps"][1]["uses"].startswith("pypa/gh-action-pypi-publish@dc37677b")
    assert doc["jobs"]["pipeline"]["uses"].endswith("@main")


def test_dry_run_writes_nothing(git_repo):
    repo = _python_repo(git_repo)
    before = repo.run("status", "--porcelain")
    lines: list[str] = []
    assert scaffold.init_repo(repo.path, yes=True, dry_run=True, out=lines.append) == 0
    assert repo.run("status", "--porcelain") == before
    text = "\n".join(lines)
    assert "+ .github/ghtools.toml" in text
    assert "- .github/workflows/ci.yml" in text
    assert "= .github/workflows/claude.yml" in text


def test_init_writes_config_stub_archive_and_removes_replaced(git_repo):
    repo = _python_repo(git_repo)
    assert scaffold.init_repo(repo.path, yes=True, out=_quiet, today="2026-09-27") == 0
    cfg = config.load(repo.path)
    assert cfg.get("version.source") == "pyproject"
    raw = (repo.path / ".github/ghtools.toml").read_text()
    assert 'static version = "0.1.0" in [project]' in raw
    tomllib.loads(raw)
    assert (repo.path / ".github/workflows/ghtools.yml").is_file()
    assert not (repo.path / ".github/workflows/ci.yml").exists()
    assert not (repo.path / ".github/scripts/compute_next_version.py").exists()
    assert (repo.path / ".github/workflows/claude.yml").is_file()
    snap = repo.path / ".github/archive/pre-ghtools-2026-09-27"
    assert (snap / "workflows/ci.yml").read_text() == CI


def test_keep_old_and_archive_none(git_repo):
    repo = _python_repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, keep_old=True, archive_mode="none", out=_quiet)
    assert (repo.path / ".github/workflows/ci.yml").is_file()
    assert not (repo.path / ".github/archive").exists()


def test_refuses_from_subdirectory(git_repo):
    repo = _python_repo(git_repo)
    (repo.path / "sub").mkdir()
    with pytest.raises(PreconditionError, match="run ghtools init from the repository root"):
        scaffold.init_repo(repo.path / "sub", yes=True, out=_quiet)


def test_refuses_when_a_touched_file_is_dirty(git_repo):
    repo = _python_repo(git_repo)
    (repo.path / ".github/workflows/ci.yml").write_text(CI + "# local edit\n")
    with pytest.raises(PreconditionError, match=r"uncommitted changes.*\.github/workflows/ci\.yml"):
        scaffold.init_repo(repo.path, yes=True, out=_quiet)
    assert not (repo.path / ".github/ghtools.toml").exists()


def test_rerun_shows_diff_and_writes_nothing_without_write(git_repo):
    repo = _python_repo(git_repo)
    scaffold.init_repo(repo.path, yes=True, archive_mode="none", out=_quiet)
    (repo.path / ".github/ghtools.toml").write_text('branch = "main"\n')
    repo.run("add", "-A")
    repo.run("commit", "-q", "-m", "ci: switch to ghtools")  # --write refuses uncommitted edits
    lines: list[str] = []
    assert scaffold.init_repo(repo.path, yes=True, out=lines.append) == 0
    assert (repo.path / ".github/ghtools.toml").read_text() == 'branch = "main"\n'
    diff_lines = "\n".join(lines).splitlines()
    assert any(line.startswith("+version") or "+[version]" in line for line in diff_lines)
    scaffold.init_repo(repo.path, yes=True, write=True, out=_quiet)
    assert "[version]" in (repo.path / ".github/ghtools.toml").read_text()


def test_questions_use_answers(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            "doc/conf.py": "",
            ".gitmodules": '[submodule "docs"]\n\tpath = docs\n\turl = x\n',
        },
    )
    answers = iter(["none"])
    scaffold.init_repo(git_repo.path, ask=lambda _p: next(answers), archive_mode="none", out=_quiet)
    assert config.load(git_repo.path).get("docs.enabled") is False


def test_dry_run_shows_notes(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            ".github/workflows/release.yml": (
                "jobs:\n  p:\n    steps:\n      - uses: pypa/gh-action-pypi-publish@release/v1\n"
                "      - run: gh release create x\n"
            ),
        },
    )
    lines: list[str] = []
    scaffold.init_repo(git_repo.path, yes=True, dry_run=True, out=lines.append)
    assert any(line.startswith("NOTE: PyPI") for line in lines)


HACS_FILES = {
    "hacs.json": "{}",
    "custom_components/intercom/manifest.json": '{"version": "0.4.0"}',
    ".github/workflows/test.yaml": (
        "jobs:\n  t:\n    steps:\n      - uses: actions/setup-python@v5\n        with:\n"
        '          python-version: "3.13"\n      - run: pip install pytest pyyaml voluptuous\n'
        "      - run: pytest -q\n"
    ),
}


def test_rerun_after_init_still_sees_the_archived_workflows(git_repo):
    # Review I6: the old workflows live only in the archive after init.
    git_repo.commit("feat: init", HACS_FILES)
    scaffold.init_repo(git_repo.path, yes=True, out=_quiet)
    git_repo.run("add", "-A")
    git_repo.run("commit", "-q", "-m", "ci: switch to ghtools")
    lines: list[str] = []
    scaffold.init_repo(git_repo.path, yes=True, out=lines.append)
    assert lines == [".github/ghtools.toml matches what detection finds now."]


def test_rerun_write_never_deletes_existing_keys(git_repo):
    git_repo.commit("feat: init", HACS_FILES)
    scaffold.init_repo(git_repo.path, yes=True, archive_mode="none", out=_quiet)
    git_repo.run("add", "-A")
    git_repo.run(
        "commit", "-q", "-m", "ci: switch to ghtools"
    )  # old workflows are gone, no archive
    scaffold.init_repo(git_repo.path, yes=True, write=True, out=_quiet)
    cfg = config.load(git_repo.path)
    assert cfg.get("ci.install") == "pytest pyyaml voluptuous"
    assert cfg.get("ci.python") == ["3.13"]


def test_replaced_workflows_keep_their_make_checks_as_gates(git_repo):
    from test_detect import BASH_HELPERS

    git_repo.commit("feat: init", BASH_HELPERS)
    scaffold.init_repo(git_repo.path, yes=True, archive_mode="none", out=_quiet)
    assert not (git_repo.path / ".github/workflows/pages.yml").exists()  # ghtools deploys Pages now
    gates = {g["name"]: g["run"] for g in config.load(git_repo.path).get("ci.gate")}
    assert gates == {
        "make-lint": "make lint",
        "make-validate": "make validate",
        "docs-fresh": "make docs && git diff --quiet -- docs/",
    }


README = """<img src="https://raw.githubusercontent.com/will-roscoe/protonfs/main/.github/status/status.svg" width="900">
[![Coverage](./.github/badges/coverage.svg)](x) [![Build](./.github/badges/build-linux-x64.svg)](y)
![Drive](.github/badges/proton-drive.svg)
"""


def test_rewrite_readme_maps_known_badges_and_leaves_others():
    text, done, left, _names = scaffold.rewrite_readme(
        README, "will-roscoe/protonfs", "ghtools-status"
    )
    assert "https://github.com/will-roscoe/protonfs/raw/ghtools-status/status.svg" in text
    assert "https://github.com/will-roscoe/protonfs/raw/ghtools-status/badges/coverage.svg" in text
    assert "./.github/badges/build-linux-x64.svg" in text
    assert ".github/badges/proton-drive.svg" in text
    assert len(done) == 2
    assert sorted(left) == [".github/badges/build-linux-x64.svg", ".github/badges/proton-drive.svg"]


def test_init_enables_status_and_removes_replaced_status_files(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            ".github/workflows/ci.yml": CI,
            ".github/status/render.py": "#",
            ".github/badges/coverage.svg": "<svg/>",
            ".github/badges/proton-drive.svg": "<svg/>",
            "README.md": "![c](./.github/badges/coverage.svg) ![p](.github/badges/proton-drive.svg)\n",
        },
    )
    git_repo.run("remote", "add", "origin", "https://github.com/o/r.git")
    lines: list[str] = []
    scaffold.init_repo(git_repo.path, yes=True, out=lines.append)
    assert config.load(git_repo.path).get("status.enabled") is True
    assert not (git_repo.path / ".github/status/render.py").exists()
    assert not (git_repo.path / ".github/badges/coverage.svg").exists()
    assert (
        git_repo.path / ".github/badges/proton-drive.svg"
    ).exists()  # not produced by ghtools: kept
    readme = (git_repo.path / "README.md").read_text()
    assert "https://github.com/o/r/raw/ghtools-status/badges/coverage.svg" in readme
    assert any("proton-drive.svg" in line and "left as is" in line for line in lines)


@pytest.mark.parametrize(
    "ref",
    [
        "https://raw.githubusercontent.com/o/r/main/.github/badges/coverage.svg",
        "https://github.com/o/r/blob/main/.github/badges/coverage.svg",
        "https://github.com/o/r/raw/main/.github/badges/coverage.svg",
        "./.github/badges/coverage.svg",
        ".github/badges/coverage.svg",
    ],
)
def test_rewrite_replaces_whole_badge_urls(ref):
    # Review I6: an absolute URL must be replaced whole, not have a URL spliced into its middle.
    text, done, _left, names = scaffold.rewrite_readme(f"![c]({ref})\n", "o/r", "ghtools-status")
    assert text == "![c](https://github.com/o/r/raw/ghtools-status/badges/coverage.svg)\n"
    assert names == ["coverage"]


@pytest.mark.parametrize("ref", [".github/status/status.svg", "./.github/status/status.svg"])
def test_rewrite_handles_relative_status_card(ref):
    text, *_ = scaffold.rewrite_readme(f'<img src="{ref}">\n', "o/r", "ghtools-status")
    assert text == '<img src="https://github.com/o/r/raw/ghtools-status/status.svg">\n'


def test_rewritten_badges_are_published(git_repo):
    # Review I5: a README pointed at badges/docstrings.svg needs "docstrings" in status.badges.
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            ".github/badges/interrogate-badge.svg": "<svg/>",
            "README.md": "![d](.github/badges/interrogate-badge.svg)\n",
        },
    )
    git_repo.run("remote", "add", "origin", "https://github.com/o/r.git")
    scaffold.init_repo(git_repo.path, yes=True, archive_mode="none", out=_quiet)
    assert "docstrings" in config.load(git_repo.path).get("status.badges")


def test_init_migrates_old_sync_markers(git_repo):
    old = "<!-- SYNC:overview START - generated from docs/_shared/overview.rst, do not edit here -->\nold\n<!-- SYNC:overview END -->\n"
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            "docs/_shared/overview.rst": "Hello.\n",
            "README.md": old,
            ".github/scripts/sync_readme.py": "#",
        },
    )
    scaffold.init_repo(git_repo.path, yes=True, archive_mode="none", out=_quiet)
    text = (git_repo.path / "README.md").read_text()
    assert (
        "<!-- ghtools:sync overview START — generated from docs/_shared/overview.rst, do not edit here -->"
        in text
    )
    assert "<!-- ghtools:sync overview END -->" in text
    assert "SYNC:overview" not in text
    assert not (git_repo.path / ".github/scripts/sync_readme.py").exists()
    assert config.load(git_repo.path).get("readme.block")[0]["name"] == "overview"


def test_init_notes_needs_for_several_subprojects(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": "[tool.ruff]\n",
            "a/pyproject.toml": '[project]\nname = "a"\n',
            "b/pyproject.toml": '[project]\nname = "b"\n',
        },
    )
    plan, _ = scaffold.make_plan(git_repo.path, {}, False, "none", "v1")
    assert any("needs = [" in note for note in plan.notes)


def test_stub_dispatch_job_only_when_configured():
    plain = scaffold.render_stub("main", pypi=False)
    assert "dispatch.yml@" not in plain and "actions: write" not in plain
    stub = yaml.safe_load(scaffold.render_stub("main", pypi=False, dispatch=True))
    job = stub["jobs"]["dispatch"]
    assert job["permissions"] == {"actions": "write"}
    assert job["uses"].endswith("/.github/workflows/dispatch.yml@v1")
    assert "actions" not in stub["permissions"]  # granted to that one job only


def test_current_stub_keeps_the_pin_and_follows_settings(git_repo):
    from ghtools.cli import main

    stub = scaffold.render_stub("main", pypi=False, ref="main")
    git_repo.commit(
        "ci: ghtools",
        {
            ".github/ghtools.toml": '[directives]\nenabled = true\ndispatch = { "t" = "t.yml" }\n',
            ".github/workflows/ghtools.yml": stub,
        },
    )
    text = scaffold.current_stub(git_repo.path)
    assert "pipeline.yml@main" in text and "dispatch.yml@main" in text
    assert main(["-C", str(git_repo.path), "stub", "--write"]) == 0
    assert (git_repo.path / ".github/workflows/ghtools.yml").read_text() == text
    (git_repo.path / ".github/workflows/ghtools.yml").write_text("edited\n")
    assert main(["-C", str(git_repo.path), "stub", "--write"]) == 3


def test_directives_workflow_is_replaced():
    wf = "jobs:\n  r:\n    steps:\n      - run: python .github/scripts/resolve_directives.py\n"
    assert scaffold.classify_workflow(wf)[0] == "replace"


def test_init_names_tests_that_import_a_removed_script(git_repo):
    # Review F-I1: sph-dev's tests import resolve_directives from .github/scripts; removing the
    # script without saying so turns its CI red at collection.
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            ".github/scripts/resolve_directives.py": "def resolve(): ...\n",
            "tests/unit/test_resolve_directives.py": "from resolve_directives import resolve\n",
            "tests/test_other.py": "import json\n",
        },
    )
    plan, _ = scaffold.make_plan(git_repo.path, {}, False, "none", "v1")
    notes = [n for n in plan.notes if "tests/unit/test_resolve_directives.py" in n]
    assert notes and "resolve_directives" in notes[0]
    assert not any("test_other.py" in n for n in plan.notes)


def test_community_files_fill_the_slug_and_skip_existing(git_repo):
    git_repo.commit("feat: a", {".github/ISSUE_TEMPLATE/bug_report.yml": "name: mine\n"})
    git_repo.run("remote", "add", "origin", "git@github.com:will-roscoe/intercom.git")
    assert scaffold.repo_slug(git_repo.path) == "will-roscoe/intercom"
    files = scaffold.community_files(git_repo.path, docs_url="")
    assert ".github/ISSUE_TEMPLATE/bug_report.yml" not in files
    assert "will-roscoe/intercom/security/advisories/new" in files["SECURITY.md"]
    assert (
        "will-roscoe/intercom/security/advisories/new" in files[".github/ISSUE_TEMPLATE/config.yml"]
    )
    assert "Documentation" not in files[".github/ISSUE_TEMPLATE/config.yml"]
    assert "@" not in files["SECURITY.md"].replace("github.com", "")  # no email addresses


def test_init_community_writes_them_and_deinit_leaves_them(git_repo):
    git_repo.commit("feat: a", {"pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n'})
    git_repo.run("tag", "pre-ghtools")  # no .github to snapshot, so deinit restores from the tag
    git_repo.run("remote", "add", "origin", "https://github.com/o/r.git")  # SECURITY.md needs it
    scaffold.init_repo(git_repo.path, yes=True, community=True, out=lambda s: None)
    assert (git_repo.path / "SECURITY.md").is_file()
    git_repo.run("add", "-A")
    git_repo.run("commit", "-q", "-m", "ci: ghtools")
    lines = []
    scaffold.deinit_repo(git_repo.path, out=lines.append)
    assert (git_repo.path / "SECURITY.md").is_file()
    assert any("leaves the community files" in line for line in lines)


def test_community_yaml_is_valid():
    files = scaffold.community_files(Path("/nonexistent"), docs_url="https://o.github.io/r")
    for rel, text in files.items():
        if rel.endswith(".yml"):
            assert isinstance(yaml.safe_load(text), dict), rel


@pytest.mark.parametrize("releases", [True, False])
def test_contributing_fits_the_repo(tmp_path, releases):
    # Review E-I4: HACS and lint repos have no `pip install -e .[dev]`; some repos don't release.
    text = scaffold.community_files(tmp_path, docs_url="", releases=releases, slug="o/r")[
        "CONTRIBUTING.md"
    ]
    assert "pip install" not in text
    assert ("+:major" in text) is releases


def test_community_notes_private_vulnerability_reporting(git_repo):
    # Review E-I5: SECURITY.md points at private vulnerability reporting, which is off by default.
    git_repo.commit("feat: a", {"pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n'})
    git_repo.run("remote", "add", "origin", "https://github.com/o/r.git")
    plan, _ = scaffold.make_plan(git_repo.path, {}, False, "none", "v1", community=True)
    assert any("private vulnerability reporting" in n and "o/r" in n for n in plan.notes)


def test_no_github_remote_skips_link_bearing_community_files(git_repo):
    # Review E-M1: without a GitHub remote the links pointed at github.com/<directory name>.
    git_repo.commit("feat: a", {"pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n'})
    plan, _ = scaffold.make_plan(git_repo.path, {}, False, "none", "v1", community=True)
    assert "SECURITY.md" not in plan.write and ".github/ISSUE_TEMPLATE/config.yml" not in plan.write
    assert "CONTRIBUTING.md" in plan.write
    assert any("no GitHub 'origin'" in n for n in plan.notes)


def test_badges_of_replaced_workflows_point_at_the_stub(git_repo):
    # intercom, ios2ha-camera and sph-dev link GitHub's workflow badge for workflows init removes.
    readme = (
        "[![Validate](https://github.com/o/r/actions/workflows/validate.yaml/badge.svg)]"
        "(https://github.com/o/r/actions/workflows/validate.yaml)\n"
        "[![Other](https://github.com/o/r/actions/workflows/claude.yml/badge.svg)](x)\n"
    )
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            ".github/workflows/validate.yaml": CI,
            ".github/workflows/claude.yml": CLAUDE,
            "README.md": readme,
        },
    )
    git_repo.run("remote", "add", "origin", "https://github.com/o/r.git")
    plan, _ = scaffold.make_plan(git_repo.path, {}, False, "none", "v1")
    text = plan.write["README.md"]
    assert (
        "actions/workflows/ghtools.yml/badge.svg)](https://github.com/o/r/actions/workflows/ghtools.yml)"
        in text
    )
    assert "validate.yaml" not in text
    assert "actions/workflows/claude.yml/badge.svg" in text  # kept workflow: untouched


def test_init_names_tests_that_load_a_removed_script_by_path(git_repo):
    # protonfs and sph-dev load .github/scripts/*.py via importlib, which no import statement shows.
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            ".github/scripts/finalize_changelog.py": "def finalize(): ...\n",
            "tests/unit/test_changelog_finalize.py": (
                "import importlib.util\n"
                '_SCRIPT = ROOT / ".github" / "scripts" / "finalize_changelog.py"\n'
            ),
        },
    )
    plan, _ = scaffold.make_plan(git_repo.path, {}, False, "none", "v1")
    assert any("tests/unit/test_changelog_finalize.py" in n for n in plan.notes)


def test_stub_passes_the_deps_token_only_when_private_deps_are_on(git_repo):
    # videoapp_ng installs the private scrapetool; other repos' stubs must stay unchanged.
    assert "DEPS_TOKEN" not in scaffold.render_stub("main", pypi=False)
    stub = yaml.safe_load(scaffold.render_stub("main", pypi=False, private_deps=True))
    assert stub["jobs"]["pipeline"]["secrets"]["DEPS_TOKEN"] == "${{ secrets.DEPS_TOKEN }}"
    git_repo.commit(
        "ci: a",
        {
            ".github/ghtools.toml": "[ci]\nprivate-deps = true\n",
            ".github/workflows/ghtools.yml": scaffold.render_stub("main", pypi=False),
        },
    )
    assert "DEPS_TOKEN" in scaffold.current_stub(git_repo.path)


def test_init_writes_the_codecov_policy(git_repo):
    repo = _python_repo(git_repo)
    assert scaffold.init_repo(repo.path, yes=True, out=_quiet, today="2026-09-28") == 0
    assert "threshold: 2%" in (repo.path / ".github/codecov.yml").read_text()


def test_init_leaves_an_existing_codecov_file_and_says_how_to_move_it(git_repo):
    repo = _python_repo(git_repo)
    git_repo.commit("chore: codecov", {"codecov.yml": "coverage:\n  precision: 2\n"})
    lines: list[str] = []
    assert scaffold.init_repo(repo.path, yes=True, out=lines.append, today="2026-09-28") == 0
    assert not (repo.path / ".github/codecov.yml").exists()
    assert any("codecov.yml" in ln and "[ci.coverage]" in ln for ln in lines)
