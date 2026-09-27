"""Structural checks on every shared workflow and action, plus actionlint."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ghtools.gates import BUILTIN

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
ACTIONS = sorted((ROOT / ".github/actions").glob("*/action.yml"))
SHARED = [p for p in WORKFLOWS if p.name != "self.yml"]
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")
UNTRUSTED = re.compile(
    r"\$\{\{\s*github\.event\.(head_commit|commits|pull_request\.(title|body|head\.ref)|issue|comment)"
)


def _uses(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                yield value
            else:
                yield from _uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from _uses(item)


def _runs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from _runs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _runs(item)


def test_there_are_workflows_to_check():
    assert WORKFLOWS and ACTIONS


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.parent.name + "/" + p.name)
def test_uses_are_pinned_or_self_references(path):
    for uses in _uses(yaml.safe_load(path.read_text())):
        assert uses.startswith("$/") or PINNED.match(uses), f"{path.name}: {uses}"
        assert not uses.startswith("./"), f"{path.name}: use $/ not ./ ({uses})"


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.name)
def test_no_pull_request_target_or_untrusted_interpolation(path):
    text = path.read_text()
    assert "pull_request_target" not in text
    for run in _runs(yaml.safe_load(text)):
        assert not UNTRUSTED.search(run), f"{path.name}: untrusted text in run: {run[:80]}"


@pytest.mark.parametrize("path", SHARED, ids=lambda p: p.name)
def test_shared_jobs_declare_permissions(path):
    for name, job in yaml.safe_load(path.read_text())["jobs"].items():
        assert "permissions" in job, f"{path.name}: job {name} has no permissions"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_jobs_running_ghtools_set_it_up_first(path):
    for name, job in yaml.safe_load(path.read_text())["jobs"].items():
        ready = False
        for step in job.get("steps", []):
            if step.get("uses") == "$/.github/actions/setup-ghtools":
                ready = True
            if re.search(r"(^|\s)ghtools ", step.get("run", "")):
                assert ready, f"{path.name}: job {name} runs ghtools before setup-ghtools"


@pytest.mark.skipif(not shutil.which("actionlint"), reason="actionlint not installed")
def test_actionlint_passes_with_the_builtin_ignores():
    proc = subprocess.run(
        ["bash", "-c", BUILTIN["actionlint"].run], cwd=ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_release_workflow_contract():
    doc = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    call = doc[True]["workflow_call"]
    assert set(call["outputs"]) == {"released", "tag", "version", "dist-artifact"}
    assert call["inputs"]["dry-run"]["type"] == "boolean"
    job = doc["jobs"]["release"]
    assert job["permissions"] == {"contents": "write"}
    assert job["outputs"]["dist-artifact"] == "ghtools-dist"
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert "[skip ci]" in text
    # Rebasing would fold commits that never passed this run's CI into the release (review I2).
    assert "pull --rebase" not in text
    steps = [s.get("name") for s in job["steps"]]
    # Tag locally, build, and only then push: a failed build leaves nothing pushed (review I3).
    assert steps.index("Build") < steps.index("Push release commit and tag")
    push = next(s for s in job["steps"] if s.get("name") == "Push release commit and tag")
    assert "::notice::" in push["run"] and "pushed=false" in push["run"]
    assert any(s.get("name") == "Check out the tag to resume" for s in job["steps"])


def test_pipeline_contract_matches_the_stub():
    from ghtools.scaffold import render_stub

    doc = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    call = doc[True]["workflow_call"]
    assert set(call["outputs"]) == {"released", "tag", "version", "dist-artifact"}
    assert set(call["inputs"]) == {"dry-run"}
    assert set(call["secrets"]) == {"CODECOV_TOKEN"}
    stub = yaml.safe_load(render_stub("main", pypi=True))
    assert set(stub["jobs"]["pipeline"]["with"]) <= set(call["inputs"])
    assert set(stub["jobs"]["pipeline"]["secrets"]) <= set(call["secrets"])
    jobs = doc["jobs"]
    assert {"config", "ci-python", "ci-hacs", "ci-lint", "docs", "release"} <= set(jobs)
    for name in ("ci-python", "ci-hacs", "ci-lint", "docs", "release"):
        assert jobs[name]["uses"].startswith("$/.github/workflows/"), name


def test_tests_still_run_and_report_when_a_gate_fails():
    # Learned from sph-dev #100: a failing lint gate must not hide coverage and test results.
    doc = yaml.safe_load((ROOT / ".github/workflows/python-ci.yml").read_text())
    steps = {s.get("name"): s for s in doc["jobs"]["test"]["steps"]}
    assert steps["Test"]["if"] == "${{ !cancelled() }}"
    assert "!cancelled()" in steps["Upload coverage to Codecov"]["if"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_ghtools_failures_are_never_swallowed(path):
    # `< <(cmd)` and `echo "...$(cmd)"` both discard cmd's exit status under set -e (review C1).
    for run in _runs(yaml.safe_load(path.read_text())):
        assert not re.search(r"<\s*<\(\s*ghtools", run), (
            f"{path.name}: process substitution of ghtools"
        )
        assert not re.search(r"echo\s+\"[^\"]*\$\(ghtools", run), (
            f"{path.name}: ghtools inside echo"
        )


def test_config_job_can_see_a_pending_resume():
    doc = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    step = next(s for s in doc["jobs"]["config"]["steps"] if s.get("id") == "decide")
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_hacs_ci_uses_the_configured_python():
    # Review I7: ios2ha-camera-hass needs 3.14; a hardcoded 3.13 breaks its install.
    hacs = yaml.safe_load((ROOT / ".github/workflows/hacs-ci.yml").read_text())
    assert hacs[True]["workflow_call"]["inputs"]["python"]["type"] == "string"
    for job in ("lint", "test"):
        setup = next(s for s in hacs["jobs"][job]["steps"] if "setup-python" in s.get("uses", ""))
        assert setup["with"]["python-version"] == "${{ inputs.python }}"
    pipeline = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    assert "python_latest" in pipeline["jobs"]["ci-hacs"]["with"]["python"]


def test_status_workflow_never_fails_the_pipeline():
    doc = yaml.safe_load((ROOT / ".github/workflows/status.yml").read_text())
    job = doc["jobs"]["publish"]
    assert job["continue-on-error"] is True
    assert job["permissions"] == {"contents": "write"}
    pipeline = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    status = pipeline["jobs"]["status"]
    assert status["uses"] == "$/.github/workflows/status.yml"
    assert "always()" in status["if"] and "default-ref" in status["if"]


def test_ci_legs_and_docs_upload_status_artifacts():
    ci = (ROOT / ".github/workflows/python-ci.yml").read_text()
    docs = (ROOT / ".github/workflows/docs.yml").read_text()
    assert "ghtools status leg" in ci and "ghtools-status-leg-" in ci
    assert "ghtools status collect docs" in docs and "ghtools-status-docs" in docs


def test_status_gets_a_version_only_when_one_was_released():
    # Review I2: release.yml sets `version` from its decide step even when nothing was pushed.
    pipeline = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    version = pipeline["jobs"]["status"]["with"]["version"]
    assert "needs.release.outputs.released == 'true'" in version


def test_status_recording_never_fails_ci():
    # Review I9: a crash while recording status must not fail the test or docs job.
    for name, job in (("python-ci.yml", "test"), ("docs.yml", "build")):
        steps = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())["jobs"][job][
            "steps"
        ]
        record = [
            s
            for s in steps
            if "ghtools status" in s.get("run", "")
            or "ghtools-status" in str(s.get("with", {}).get("name", ""))
        ]
        assert len(record) == 2, name
        assert all(s.get("continue-on-error") is True for s in record), name
