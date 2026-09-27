"""Tests for ghtools.versionfile."""

from __future__ import annotations

import json

import pytest

from ghtools import config, versionfile
from ghtools.errors import GhtoolsError

PYPROJECT = """\
[build-system]
requires = ["setuptools"]

[tool.bumpversion]
version = "9.9.9"

[project]
name = "sph"
version = "0.27.1"  # kept in sync by ghtools

[tool.poetry]
version = "1.0.0"
"""


def test_only_project_version_is_rewritten():
    out = versionfile.set_pyproject_version(PYPROJECT, "0.28.0")
    assert 'version = "0.28.0"  # kept in sync by ghtools' in out
    assert '[tool.bumpversion]\nversion = "9.9.9"' in out
    assert '[tool.poetry]\nversion = "1.0.0"' in out


def test_missing_static_version_is_a_clear_error():
    with pytest.raises(GhtoolsError, match=r"no static `version = \"...\"` in \[project\]"):
        versionfile.set_pyproject_version('[project]\nname = "x"\ndynamic = ["version"]\n', "1.0.0")


def test_pyproject_crlf_is_preserved(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(PYPROJECT.replace("\n", "\r\n").encode())
    cfg = config.from_dict({"version": {"source": "pyproject"}})
    assert versionfile.write_version(cfg, tmp_path, "0.28.0") == "pyproject.toml"
    data = (tmp_path / "pyproject.toml").read_bytes().decode()
    assert '\r\nversion = "0.28.0"  # kept in sync by ghtools\r\n' in data
    assert "\n" not in data.replace("\r\n", "")
    assert versionfile.read_version(cfg, tmp_path) == "0.28.0"


def test_manifest_round_trip_keeps_key_order_indent_and_crlf(tmp_path):
    manifest = tmp_path / "custom_components/intercom/manifest.json"
    manifest.parent.mkdir(parents=True)
    original = {
        "domain": "intercom",
        "name": "Intercom",
        "version": "0.4.0",
        "iot_class": "local_push",
    }
    manifest.write_bytes((json.dumps(original, indent=4) + "\n").replace("\n", "\r\n").encode())
    cfg = config.from_dict(
        {
            "profile": "hacs",
            "version": {
                "source": "manifest",
                "manifest": "custom_components/intercom/manifest.json",
            },
        }
    )
    assert versionfile.read_version(cfg, tmp_path) == "0.4.0"
    written = versionfile.write_version(cfg, tmp_path, "0.5.0")
    assert written == "custom_components/intercom/manifest.json"
    raw = manifest.read_bytes().decode()
    assert raw.startswith('{\r\n    "domain": "intercom",')
    assert list(json.loads(raw)) == ["domain", "name", "version", "iot_class"]
    assert json.loads(raw)["version"] == "0.5.0"


def test_tag_source_reads_latest_tag_and_cannot_be_written(git_repo):
    git_repo.commit("feat: a")
    git_repo.tag("v1.2.3")
    cfg = config.from_dict({})
    assert versionfile.read_version(cfg, git_repo.path) == "1.2.3"
    with pytest.raises(GhtoolsError, match="no version file"):
        versionfile.write_version(cfg, git_repo.path, "1.2.4")
