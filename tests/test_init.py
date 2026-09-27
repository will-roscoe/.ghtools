"""Tests for ghtools init on real repositories."""

from __future__ import annotations

import tomllib

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
