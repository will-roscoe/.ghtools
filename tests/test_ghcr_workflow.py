"""The reusable workflow that publishes a release's container image (release.publish "ghcr")."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ghcr.yml"


def _load() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as True.
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


def _steps() -> list[dict]:
    (job,) = _load()["jobs"].values()
    return job["steps"]


def test_called_with_tag_version_and_platforms():
    inputs = _load()["on"]["workflow_call"]["inputs"]
    assert set(inputs) == {"tag", "version", "platforms"}
    assert inputs["tag"]["required"] and inputs["version"]["required"]
    assert inputs["platforms"]["default"] == "linux/amd64"


def test_only_the_job_may_write_packages():
    doc = _load()
    assert doc["permissions"] == {}
    (job,) = doc["jobs"].values()
    assert job["permissions"] == {"contents": "read", "packages": "write"}


def test_a_prerelease_never_moves_latest():
    (meta,) = [s for s in _steps() if "docker/metadata-action" in s.get("uses", "")]
    assert "latest=auto" in meta["with"]["flavor"]
    tags = [t.strip() for t in meta["with"]["tags"].splitlines() if t.strip()]
    assert tags and all(t.startswith("type=semver,") for t in tags)


def test_builds_the_released_tag_and_pushes():
    (checkout,) = [s for s in _steps() if "actions/checkout" in s.get("uses", "")]
    assert checkout["with"]["ref"] == "${{ inputs.tag }}"
    (build,) = [s for s in _steps() if "docker/build-push-action" in s.get("uses", "")]
    assert build["with"]["push"] is True
    assert build["with"]["platforms"] == "${{ inputs.platforms }}"
    assert "GIT_DESCRIBE=${{ inputs.tag }}" in build["with"]["build-args"]


def test_every_action_is_pinned_to_a_commit():
    uses = [s["uses"] for s in _steps() if "uses" in s]
    assert uses
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", u), u
