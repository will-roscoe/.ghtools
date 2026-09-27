"""Detection tests on synthetic repos shaped like protonfs, sph-dev, intercom and bash-helpers."""

from __future__ import annotations

import subprocess

from ghtools.detect import detect

PROTONFS_PYPROJECT = """\
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"
[project]
name = "protonfs"
dynamic = ["version"]
[tool.hatch.version]
source = "vcs"
[tool.ruff]
line-length = 100
[tool.interrogate]
fail-under = 80
"""

PROTONFS_CI = """\
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.9", "3.10", "3.11", "3.12", "3.13"]
    steps:
      - run: pip install -e .[dev]
      - run: ruff check src tests --output-format=github
      - run: pytest -q --cov=src/protonfs --cov-report=xml:coverage.xml --cov-fail-under=80
      - run: interrogate -c pyproject.toml
      - uses: codecov/codecov-action@v5
  test-arm:
    runs-on: ubuntu-24.04-arm
  test-macos-arm:
    runs-on: macos-latest
"""

PROTONFS_RELEASE = (
    "jobs:\n  p:\n    steps:\n      - uses: pypa/gh-action-pypi-publish@release/v1\n"
    "      - run: gh release create x\n"
)

PROTONFS_DOCS = """\
jobs:
  build:
    steps:
      - run: sudo apt-get update && sudo apt-get install -y graphviz
      - run: pip install -e ".[docs]"
      - run: sphinx-apidoc -f -o docs/api/protonfs/ src/protonfs/ --separate --module-first -q
      - run: sphinx-build -W --keep-going -b html docs/ docs/_build/html
      - uses: actions/deploy-pages@v4
"""


def test_protonfs_like(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": PROTONFS_PYPROJECT,
            ".github/workflows/ci.yml": PROTONFS_CI,
            ".github/workflows/release.yml": PROTONFS_RELEASE,
            ".github/workflows/docs.yml": PROTONFS_DOCS,
            "docs/conf.py": "",
            "CHANGELOG.md": "# Changelog\n",
        },
    )
    git_repo.tag("v2.3.0")
    d = detect(git_repo.path)
    v = d.values
    assert v["branch"] == "main"
    assert v["profile"] == "python"
    assert v["version.source"] == "tag"
    assert v["release.publish"] == ["github", "pypi"]
    assert v["ci.python"] == ["3.9", "3.10", "3.11", "3.12", "3.13"]
    assert v["ci.runners"] == ["ubuntu-latest", "ubuntu-24.04-arm", "macos-latest"]
    assert v["ci.install"] == "-e .[dev]"  # -e kept: --cov=<path> needs source-tree imports
    assert v["ci.coverage.package"] == "src/protonfs"
    assert v["ci.coverage.floor"] == 80
    assert v["ci.coverage.codecov"] is True
    assert v["ci.gates"] == ["ruff", "interrogate"]
    assert v["docs.enabled"] is True
    assert v["docs.dir"] == "docs"
    assert v["docs.apidoc"] == "src/protonfs"
    assert v["docs.pages"] is True
    assert v["docs.apt"] == ["graphviz"]
    assert v["docs.install"] == ".[docs]"
    assert "hatch" in d.evidence["version.source"]
    assert d.questions == []


def test_sph_dev_like_static_version_and_doc_vs_docs_submodule(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": (
                '[project]\nname = "sph"\nversion = "0.28.0"\n[tool.ruff]\nline-length = 120\n'
            ),
            ".github/workflows/auto-release.yml": (
                "jobs:\n  c:\n    steps:\n"
                '      - run: sed -i "s/^version = \\".*\\"/version = \\"${NEXT}\\"/" pyproject.toml\n'
                "      - run: gh release create x\n"
            ),
            "doc/conf.py": "",
            ".gitmodules": (
                '[submodule "docs"]\n\tpath = docs\n\turl = git@github.com:will-roscoe/sph-docs.git\n'
            ),
        },
    )
    git_repo.run("branch", "-m", "master")
    d = detect(git_repo.path)
    assert d.values["branch"] == "master"
    assert d.values["version.source"] == "pyproject"
    assert d.values["version.bump"] == "write"
    assert d.evidence["version.source"] == 'static version = "0.28.0" in [project]'
    assert [q.key for q in d.questions] == ["docs.dir"]
    assert d.questions[0].choices == ["doc", "none"]


def test_require_mode_detected_from_verify_only_release(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "2.12.0"\n',
            ".github/workflows/auto-release.yml": (
                "jobs:\n  c:\n    steps:\n"
                '      - run: echo "::error::pyproject.toml says $DECLARED but the commits imply $NEXT."\n'
                "      - run: gh release create x\n"
            ),
        },
    )
    assert detect(git_repo.path).values["version.bump"] == "require"


def test_hacs_like(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "hacs.json": "{}",
            "custom_components/intercom/manifest.json": '{"version": "0.4.0"}',
            "pyproject.toml": "[tool.ruff]\n",
            ".github/workflows/test.yaml": (
                "jobs:\n  t:\n    steps:\n      - run: pip install pytest pyyaml voluptuous\n"
                "      - run: pytest -q\n"
            ),
            ".github/workflows/lint.yaml": (
                "jobs:\n  l:\n    steps:\n      - run: ruff format --check .\n"
            ),
            ".github/workflows/release.yaml": (
                "jobs:\n  r:\n    steps:\n      - run: |\n          cd custom_components/intercom\n"
                "          zip -r ../../intercom.zip .\n      - uses: softprops/action-gh-release@v3\n"
            ),
        },
    )
    git_repo.tag("v0.4.0")
    v = detect(git_repo.path).values
    assert v["profile"] == "hacs"
    assert v["version.source"] == "manifest"
    assert v["version.manifest"] == "custom_components/intercom/manifest.json"
    assert v["release.publish"] == ["github-zip"]
    assert v["release.zip"] == "custom_components/intercom"
    assert v["ci.install"] == "pytest pyyaml voluptuous"
    assert v["ci.gates"] == ["ruff", "ruff-format"]
    assert v["release.changelog"] == ""


def test_lint_like_without_releases(git_repo):
    git_repo.commit("feat: init", {"x.sh": "echo hi\n", "common/y.bash": "echo\n", "Makefile": ""})
    v = detect(git_repo.path).values
    assert v["profile"] == "lint"
    assert v["release.enabled"] is False
    assert v["ci.gates"] == ["shellcheck"]


BASH_HELPERS = {
    "Makefile": "docs:\n\ttrue\n",
    "x.sh": "echo\n",
    "docs/conf.py": "",
    ".github/workflows/pages.yml": (
        "jobs:\n  b:\n    steps:\n      - run: pip install -r docs/requirements.txt\n"
        "      - run: make docs\n"
        "      - run: python -m sphinx -b html docs docs/_build/html --fail-on-warning\n"
        "      - uses: actions/deploy-pages@v4\n"
    ),
    ".github/workflows/docs-check.yml": (
        "jobs:\n  c:\n    steps:\n      - run: |\n          sudo apt-get install -y -qq shellcheck\n          make lint\n          make validate\n"
        "      - run: make docs\n      - run: |\n          if ! git diff --quiet -- docs/; then exit 1; fi\n"
    ),
}


def test_bash_helpers_like_docs_settings(git_repo):
    # Review I10: no pyproject, docs installed from requirements, generated before Sphinx.
    git_repo.commit("feat: init", BASH_HELPERS)
    v = detect(git_repo.path).values
    assert v["docs.install"] == "-r docs/requirements.txt"
    assert v["docs.prebuild"] == ["make docs"]
    assert v["docs.strict"] is True  # --fail-on-warning
    assert "docs.apt" not in v  # docs-check's `apt-get install shellcheck` is not a docs dependency


def test_sphinx_without_w_is_not_strict(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            "doc/conf.py": "",
            ".github/workflows/docs.yml": "jobs:\n  d:\n    steps:\n      - run: sphinx-build --keep-going -b html doc/ doc/_build/html\n",
        },
    )
    assert detect(git_repo.path).values["docs.strict"] is False


def test_protonfs_style_sync_markers_detected(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n[tool.ruff]\n',
            "docs/_shared/overview.rst": "Hello.\n",
            "README.md": "<!-- SYNC:overview START - generated from docs/_shared/overview.rst, do not edit here -->\nold\n<!-- SYNC:overview END -->\n",
        },
    )
    v = detect(git_repo.path).values
    assert v["readme.block"] == [{"name": "overview", "source": "docs/_shared/overview.rst"}]
    assert "readme-sync" in v["ci.gates"]


def test_every_sync_block_is_detected(git_repo):
    # init migrates every SYNC block, so every one must be configured or it silently goes stale.
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
            "docs/a.rst": "A.\n",
            "docs/b.rst": "B.\n",
            "README.md": (
                "<!-- SYNC:a START - generated from docs/a.rst, do not edit here -->\n<!-- SYNC:a END -->\n"
                "<!-- SYNC:b START - generated from docs/b.rst, do not edit here -->\n<!-- SYNC:b END -->\n"
            ),
        },
    )
    blocks = detect(git_repo.path).values["readme.block"]
    assert [b["name"] for b in blocks] == ["a", "b"]


def test_python_dev_like_subprojects(git_repo):
    git_repo.commit(
        "feat: init",
        {
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
            "scrapetool/pyproject.toml": '[project]\nname = "scrapetool"\n',
            "scrapetool/src/scrapetool/__init__.py": "",
            "scrapetool/tests/test_x.py": "",
            "eclipse-flow/pyproject.toml": '[project]\nname = "eclipse-flow"\n',
            "eclipse-flow/src/eclipse_flow/__init__.py": "",
            "devlibs/pyproject.toml": '[project]\nname = "devlib"\n',
            ".gitmodules": '[submodule "coordpy"]\n\tpath = coordpy\n\turl = git@github.com:will-roscoe/coordpy.git\n',
        },
    )
    rows = {r["name"]: r for r in detect(git_repo.path).values["subprojects"]}
    assert rows["scrapetool"] == {
        "name": "scrapetool",
        "path": "scrapetool",
        "package": "scrapetool",
        "tests": "scrapetool/tests",
    }
    assert rows["eclipse_flow"]["path"] == "eclipse-flow"
    assert rows["eclipse_flow"]["package"] == "eclipse_flow"
    assert "tests" not in rows["eclipse_flow"]
    assert rows["devlib"]["package"] == "devlib"
    assert rows["coordpy"] == {"name": "coordpy", "path": "coordpy", "kind": "submodule"}
    assert detect(git_repo.path).values["ci.coverage.flags"] == "subproject"


def test_checked_out_submodule_is_not_also_an_in_tree_project(git_repo, tmp_path):
    # Review D-C1: python-dev/iot-dev/sph-dev all failed init with "duplicate subproject".
    upstream = tmp_path / "up"
    subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
    (upstream / "pyproject.toml").write_text('[project]\nname = "eqrel"\n')
    subprocess.run(["git", "-C", str(upstream), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(upstream),
            "-c",
            "user.email=t@e",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "one",
        ],
        check=True,
    )
    git_repo.commit(
        "feat: a",
        {"pyproject.toml": "[tool.ruff]\n", "a/pyproject.toml": '[project]\nname = "a"\n'},
    )
    git_repo.run(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(upstream), "eqrel"
    )
    with (git_repo.path / ".gitmodules").open("a") as f:
        f.write('#[submodule "old"]\n#\tpath = old\n#\turl = x\n')
    git_repo.run("commit", "-q", "-am", "chore(eqrel): add")
    rows = detect(git_repo.path).values["subprojects"]
    assert sorted((r["name"], r.get("kind", "in-tree")) for r in rows) == [
        ("a", "in-tree"),
        ("eqrel", "submodule"),
    ]


def test_a_comment_mentioning_project_is_not_a_package(git_repo):
    # Review D-C2: python-dev's pyproject says "Intentionally NOT an installable package: no [project]".
    git_repo.commit(
        "feat: a",
        {
            "pyproject.toml": "# Intentionally NOT an installable package: no [project]\n[tool.ruff]\n",
            ".gitmodules": '[submodule "c"]\n\tpath = c\n\turl = https://example.invalid/c.git\n',
        },
    )
    assert detect(git_repo.path).values["profile"] == "umbrella"


def test_subprojects_row_is_on_when_in_tree_subprojects_exist(git_repo):
    git_repo.commit(
        "feat: a",
        {"pyproject.toml": "[tool.ruff]\n", "a/pyproject.toml": '[project]\nname = "a"\n'},
    )
    assert "subprojects" in detect(git_repo.path).values["status.rows"]


def test_detected_install_brings_the_test_extra(git_repo):
    # Review D-C3: `-e ./path` alone left pytest uninstalled on every detected leg.
    git_repo.commit(
        "feat: a",
        {
            "pyproject.toml": "[tool.ruff]\n",
            "a/pyproject.toml": '[project]\nname = "a"\n[project.optional-dependencies]\ntest = ["pytest"]\n',
            "b/pyproject.toml": '[project]\nname = "b"\n[project.optional-dependencies]\ndev = ["pytest"]\n',
            "c/pyproject.toml": '[project]\nname = "c"\n',
        },
    )
    rows = {r["name"]: r for r in detect(git_repo.path).values["subprojects"]}
    assert rows["a"]["install"] == ["-e ./a[test]"]
    assert rows["b"]["install"] == ["-e ./b[dev]"]
    assert "install" not in rows["c"]


SPH_DIRECTIVES = """name: Commit Directives
on:
  push:
    branches: ["**"]
jobs:
  dispatch-workflows:
    runs-on: ubuntu-latest
    steps:
      - run: python .github/scripts/resolve_directives.py
      - run: gh workflow run ci.yml --repo "$GITHUB_REPOSITORY" --ref master
      - run: gh workflow run update-todo.yml --repo "$GITHUB_REPOSITORY" --ref master
"""


def test_sph_dev_directives_detected(git_repo):
    git_repo.commit(
        "feat: a",
        {
            "pyproject.toml": '[project]\nname = "sph"\nversion = "0.28.0"\n',
            ".github/workflows/directives.yml": SPH_DIRECTIVES,
        },
    )
    values = detect(git_repo.path).values
    assert values["directives.enabled"] is True
    assert values["directives.dispatch"] == {"update-todo": "update-todo.yml"}
