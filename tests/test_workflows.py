"""Structural checks on every shared workflow and action, plus actionlint."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ghtools import scaffold
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


PIPELINE = ROOT / ".github/workflows/pipeline.yml"


def _jobs():
    return yaml.safe_load(PIPELINE.read_text())["jobs"]


def _step(job, name):
    return next(s for s in _jobs()[job]["steps"] if s.get("name") == name)


def test_release_job_contract():
    job = _jobs()["release"]
    assert job["permissions"] == {"contents": "write"}
    assert job["outputs"]["dist-artifact"] == "ghtools-dist"
    text = PIPELINE.read_text()
    assert "[skip ci]" in text
    # Rebasing would fold commits that never passed this run's CI into the release (review I2).
    assert "pull --rebase" not in text
    steps = [s.get("name") for s in job["steps"]]
    # Tag locally, build, and only then push: a failed build leaves nothing pushed (review I3).
    assert steps.index("Build") < steps.index("Push release commit and tag")
    push = _step("release", "Push release commit and tag")
    assert "::notice::" in push["run"] and "pushed=false" in push["run"]
    assert "Check out the tag to resume" in steps


def test_pipeline_contract_matches_the_stub():
    from ghtools.scaffold import render_stub

    doc = yaml.safe_load(PIPELINE.read_text())
    call = doc[True]["workflow_call"]
    assert set(call["outputs"]) == {"released", "tag", "version", "dist-artifact", "dispatch"}
    assert set(call["inputs"]) == {"dry-run"}
    assert set(call["secrets"]) == {"CODECOV_TOKEN"}
    stub = yaml.safe_load(render_stub("main", pypi=True))
    assert set(stub["jobs"]["pipeline"]["with"]) <= set(call["inputs"])
    assert set(stub["jobs"]["pipeline"]["secrets"]) <= set(call["secrets"])
    expected = {
        "config",
        "test",
        "tests-passed",
        "hacs",
        "hassfest",
        "lint",
        "docs",
        "release",
        "status",
    }
    assert expected <= set(doc["jobs"])


def test_tests_still_run_and_report_when_a_gate_fails():
    # Learned from sph-dev #100: a failing lint gate must not hide coverage and test results.
    assert _step("test", "Test")["if"] == "${{ !cancelled() }}"
    assert "!cancelled()" in _step("test", "Upload coverage to Codecov")["if"]


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


def test_hacs_jobs_use_the_configured_python():
    # Review I7: ios2ha-camera-hass needs 3.14; a hardcoded 3.13 breaks its install.
    for job in ("hacs-lint", "hacs-test"):
        setup = next(s for s in _jobs()[job]["steps"] if "setup-python" in s.get("uses", ""))
        assert "python_latest" in setup["with"]["python-version"]


def test_status_job_never_fails_the_pipeline():
    job = _jobs()["status"]
    assert job["continue-on-error"] is True
    assert job["permissions"] == {"contents": "write"}
    assert "always()" in job["if"] and "default-ref" in job["if"]


def test_ci_legs_and_docs_upload_status_artifacts():
    text = PIPELINE.read_text()
    assert "ghtools status leg" in text and "ghtools-status-leg-" in text
    assert "ghtools status collect docs" in text and "ghtools-status-docs" in text


def test_status_gets_a_version_only_when_one_was_released():
    # Review I2: the release job sets `version` from its decide step even when nothing was pushed.
    version = _step("status", "Collect and publish")["env"]["VERSION"]
    assert "needs.release.outputs.released == 'true'" in version


def test_status_recording_never_fails_ci():
    # Review I9: a crash while recording status must not fail the test or docs job.
    for job in ("test", "docs"):
        record = [
            s
            for s in _jobs()[job]["steps"]
            if "ghtools status" in s.get("run", "")
            or "ghtools-status" in str(s.get("with", {}).get("name", ""))
        ]
        assert len(record) == 2, job
        assert all(s.get("continue-on-error") is True for s in record), job


def test_status_passes_the_github_description():
    run = _step("status", "Collect and publish")["run"]
    assert "--description" in run and ".description" in run


def test_config_job_computes_the_matrix_for_the_change():
    jobs = _jobs()
    step = next(s for s in jobs["config"]["steps"] if s.get("id") == "matrix")
    assert "ghtools ci matrix" in step["run"] and "--github-output" in step["run"]
    assert "pull_request.base.sha" in step["env"]["BASE"]
    test = jobs["test"]
    assert test["strategy"]["matrix"] == "${{ fromJSON(needs.config.outputs.matrix) }}"
    assert "needs.config.outputs.any" in test["if"]
    assert "subprojects" in test["if"]  # umbrella repos with in-tree subprojects run these legs
    assert "'python'" in _step("test", "Gates")["if"]  # umbrella gates run once, in the lint job


def test_subproject_legs_and_tests_passed_job():
    jobs = _jobs()
    text = PIPELINE.read_text()
    assert "--subproject" in text and "ghtools ci setup" in text
    assert "matrix.subproject" in jobs["test"]["name"]
    agg = jobs["tests-passed"]
    assert "always()" in agg["if"]
    assert agg["permissions"] == {}
    assert "skipped" in agg["steps"][0]["run"]  # zero selected legs must pass


def test_subproject_legs_have_distinct_status_artifacts():
    # Every subproject leg shares one python and runner; upload-artifact rejects duplicate names.
    text = PIPELINE.read_text()
    assert "matrix.subproject && format(" in text
    assert '--subproject "$SUBPROJECT"' in _step("test", "Record status leg")["run"]


def test_subproject_setup_runs_after_install():
    # Review D-I4: python-dev's setup (FreeImage, Playwright browser) imports the package.
    names = [s.get("name") for s in _jobs()["test"]["steps"]]
    assert names.index("Setup (subproject)") > names.index("Install")


def test_directives_force_ci_and_export_dispatch():
    doc = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    config = doc["jobs"]["config"]
    step = next(s for s in config["steps"] if s.get("id") == "directives")
    assert "ghtools directives resolve" in step["run"]
    assert step["if"] == "github.event_name == 'push'"
    paths = next(s for s in config["steps"] if s.get("id") == "paths")
    assert "FORCE_CI" in paths["env"] and "$FORCE_CI" in paths["run"]
    on = doc[True] if True in doc else doc["on"]  # PyYAML reads the key `on` as True
    assert "dispatch" in on["workflow_call"]["outputs"]


def test_dispatch_workflow():
    doc = yaml.safe_load((ROOT / ".github/workflows/dispatch.yml").read_text())
    (job,) = doc["jobs"].values()
    assert job["permissions"] == {"actions": "write"}
    run = job["steps"][0]["run"]
    assert "gh workflow run" in run and "::warning::" in run
    assert "${{" not in run  # workflow names reach the shell through env only


def _stub_grants():
    stub = yaml.safe_load(scaffold.render_stub("main", pypi=True))
    return stub["permissions"]


def test_pipeline_never_requests_more_than_the_stub_grants():
    # A called workflow's job asking for more than the caller grants fails every run at startup.
    level = {"none": 0, "read": 1, "write": 2}
    grants = _stub_grants()
    seen = set()

    def check(name):
        if name in seen:
            return
        seen.add(name)
        doc = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        for job_name, job in doc["jobs"].items():
            for scope, want in (job.get("permissions") or {}).items():
                have = grants.get(scope, "none")
                assert level[want] <= level[have], (
                    f"{name}:{job_name} wants {scope}: {want}, stub grants {have}"
                )
            uses = job.get("uses", "")
            if uses.startswith("$/.github/workflows/"):
                check(uses.removeprefix("$/.github/workflows/"))

    check("pipeline.yml")


@pytest.mark.skipif(not shutil.which("actionlint"), reason="actionlint not installed")
@pytest.mark.parametrize("dispatch", [False, True])
def test_rendered_stub_passes_actionlint(tmp_path, dispatch):
    wf = tmp_path / ".github/workflows"
    wf.mkdir(parents=True)
    (wf / "ghtools.yml").write_text(scaffold.render_stub("main", pypi=True, dispatch=dispatch))
    proc = subprocess.run(
        ["actionlint", "-no-color", str(wf / "ghtools.yml")], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_forced_ci_selects_every_subproject_and_no_dead_outputs():
    doc = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    config_job = doc["jobs"]["config"]
    step = next(s for s in config_job["steps"] if s.get("id") == "matrix")
    assert "FORCE_CI" in step["env"] and "--all" in step["run"]
    assert "force-ci" not in config_job["outputs"]  # only the step output is used


def test_pipeline_calls_no_nested_reusable_workflows():
    # A job-level `$/.github/workflows/...` inside the called pipeline resolves against the CALLER's
    # repository on push and workflow_dispatch (measured 2026-09-28: startup_failure "workflow was not
    # found"; pull_request worked), so every job runs inline. Step-level `$/` actions do work.
    doc = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    nested = {name: job["uses"] for name, job in doc["jobs"].items() if "uses" in job}
    assert nested == {}
    for gone in ("python-ci", "hacs-ci", "lint-ci", "docs", "release", "status"):
        assert not (ROOT / ".github/workflows" / f"{gone}.yml").exists(), gone
