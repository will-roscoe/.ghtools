"""Detection tests on synthetic repos shaped like protonfs, sph-dev, intercom and bash-helpers."""

from __future__ import annotations

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
