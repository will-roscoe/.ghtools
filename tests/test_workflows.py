"""Structural checks on every shared workflow and action, plus actionlint."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ghtools import config, scaffold
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
    assert set(call["secrets"]) == {"CODECOV_TOKEN", "DEPS_TOKEN"}
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
    for job in ("hacs-test",):
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
    for job in ("test", "docs", "hacs-test", "lint"):
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
    assert names.index("Setup") > names.index("Install")


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


def test_jobs_downstream_of_conditional_jobs_do_not_inherit_their_skips():
    # GitHub skips a job whenever something in its needs chain was skipped, unless its `if` uses a
    # status function. After flattening, the profile-specific CI jobs are always partly skipped, so
    # docs-deploy never ran (ios2ha-camera, 2026-09-28) until it said always().
    jobs = _jobs()

    def ancestors(name, seen=None):
        seen = set() if seen is None else seen
        needs = jobs[name].get("needs", [])
        for need in [needs] if isinstance(needs, str) else needs:
            if need not in seen:
                seen.add(need)
                ancestors(need, seen)
        return seen

    for name, job in jobs.items():
        conditional = [a for a in ancestors(name) if "if" in jobs[a]]
        if conditional:
            assert "always()" in str(job.get("if", "")), f"{name} inherits skips from {conditional}"


def test_setup_runs_for_root_legs_too():
    step = _step("test", "Setup")
    assert "if" not in step  # root legs run ci.setup; subproject legs run their own setup
    names = [s.get("name") for s in _jobs()["test"]["steps"]]
    assert names.index("Setup") > names.index("Install")


def test_private_dependency_access_comes_before_install_via_env_only():
    call = yaml.safe_load(PIPELINE.read_text())[True]["workflow_call"]
    assert call["secrets"]["DEPS_TOKEN"]["required"] is False
    for job in ("test", "docs"):
        names = [s.get("name") for s in _jobs()[job]["steps"]]
        assert names.index("Access to private git dependencies") < names.index("Install"), job
        step = _step(job, "Access to private git dependencies")
        assert step["env"]["DEPS_TOKEN"] == "${{ secrets.DEPS_TOKEN }}"
        assert "${{" not in step["run"]  # the token reaches the shell only through env


def test_codecov_uploads_only_with_a_token_and_says_so_without_one():
    # Codecov is on by default, so a repo that has not added CODECOV_TOKEN yet must neither fail
    # nor spam upload errors: the uploads are skipped and one notice says how to enable them.
    job = _jobs()["test"]
    assert job["env"]["HAS_CODECOV_TOKEN"] == "${{ secrets.CODECOV_TOKEN != '' }}"
    for name in ("Codecov uploads today", "Codecov daily limit"):
        step = _step("test", name)
        assert "env.HAS_CODECOV_TOKEN == 'true'" in step["if"], name
        assert "!cancelled()" in step["if"], name
    notice = _step("test", "Codecov token missing")
    assert "env.HAS_CODECOV_TOKEN != 'true'" in notice["if"]
    assert "::notice" in notice["run"] and "CODECOV_TOKEN" in notice["run"]


def test_hacs_and_lint_jobs_report_status():
    # Without a leg, HACS, lint-only and umbrella repos published no tests/lint card rows or
    # badges. HACS runs its gates in the test job so one leg carries both results.
    jobs = _jobs()
    assert "hacs-lint" not in jobs
    hacs = jobs["hacs-test"]
    names = [s.get("name") for s in hacs["steps"]]
    assert names.index("Gates") < names.index("Test") < names.index("Record status leg")
    assert _step("hacs-test", "Test")["if"] == "${{ !cancelled() }}"
    assert "--canonical" in _step("hacs-test", "Record status leg")["run"]
    lint = _step("lint", "Record status leg")["run"]
    assert "--lint-only" in lint
    for job in ("hacs-test", "lint"):
        upload = next(s for s in jobs[job]["steps"] if "upload-artifact" in s.get("uses", ""))
        assert upload["with"]["name"].startswith("ghtools-status-leg-"), job
    for job in ("release", "status"):
        assert "hacs-lint" not in jobs[job]["needs"], job


def test_codecov_uploads_only_when_code_changed():
    config_job = _jobs()["config"]
    assert config_job["outputs"]["code-changed"] == "${{ steps.code.outputs.code-changed }}"
    step = next(s for s in config_job["steps"] if s.get("id") == "code")
    assert "github.event.pull_request.base.sha" in str(step["env"])
    assert "ghtools ci code-changed" in step["run"]
    for name in ("Codecov uploads today", "Codecov daily limit", "Codecov token missing"):
        assert "needs.config.outputs.code-changed == 'true'" in _step("test", name)["if"], name


def test_one_leg_uploads_to_codecov_at_most_uploads_per_day_times():
    # Every upload counts against the Codecov plan's monthly allowance: one leg per run (the first,
    # or each subproject's), and only while today's count for that scope is under the limit.
    jobs = _jobs()
    assert jobs["config"]["outputs"]["codecov-day"] == "${{ steps.code.outputs.day }}"
    assert "date -u +%F" in next(s for s in jobs["config"]["steps"] if s.get("id") == "code")["run"]
    quota_prefix = jobs["test"]["env"]["CODECOV_QUOTA"]
    assert "matrix.subproject || 'root'" in quota_prefix
    assert "needs.config.outputs.codecov-day" in quota_prefix
    names = [s.get("name") for s in jobs["test"]["steps"]]
    order = [
        "Test",
        "Codecov uploads today",
        "Codecov daily limit",
        "Upload coverage to Codecov",
        "Upload test results to Codecov",
        "Record the Codecov upload",
    ]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    lookup = _step("test", "Codecov uploads today")
    assert "(strategy.job-index == 0 || matrix.subproject)" in lookup["if"]
    assert lookup["with"]["lookup-only"] is True
    assert lookup["with"]["restore-keys"] == "${{ env.CODECOV_QUOTA }}"
    limit = _step("test", "Codecov daily limit")
    assert limit["if"] == lookup["if"]
    assert "ghtools ci codecov-quota" in limit["run"] and "--github-output" in limit["run"]
    for name in ("Upload coverage to Codecov", "Upload test results to Codecov"):
        assert "steps.quota.outputs.upload == 'true'" in _step("test", name)["if"], name
    record = _step("test", "Record the Codecov upload")
    assert record["uses"].startswith("actions/cache/save@")
    assert record["with"]["key"] == "${{ steps.quota.outputs.key }}"
    assert record["with"]["path"] == lookup["with"]["path"]  # the path is part of the cache version
    assert record["continue-on-error"] is True
    assert "steps.quota.outputs.key != ''" in record["if"]


def test_should_ci_run_step_consults_skip_pr_heads():
    doc = yaml.safe_load(PIPELINE.read_text())
    step = next(s for s in doc["jobs"]["config"]["steps"] if s.get("id") == "paths")
    assert "HEAD_REF" in step["env"] and "ghtools ci skip-pr" in step["run"]


def _pipeline():
    return yaml.safe_load(PIPELINE.read_text())


def test_docs_job_waits_on_docs_paths_check():
    doc = _pipeline()
    assert "run-docs" in doc["jobs"]["config"]["outputs"]
    assert "needs.config.outputs.run-docs == 'true'" in doc["jobs"]["docs"]["if"]
    step = next(s for s in doc["jobs"]["config"]["steps"] if s.get("id") == "docs-paths")
    assert "ghtools ci docs-changed" in step["run"]


def test_reuse_is_opt_in_and_skips_ci_only_when_reused():
    doc = _pipeline()
    config_job = doc["jobs"]["config"]
    step = next(s for s in config_job["steps"] if s.get("id") == "reuse")
    assert ").reuse" in step["if"] and "default-ref" in step["if"]
    assert "force-ci != 'true'" in step["if"]  # a [ci] directive still forces tests
    assert "reused == 'true' && 'false'" in config_job["outputs"]["run-ci"]


def test_record_tested_job_is_same_repo_pr_only_and_never_fails_the_run():
    job = _pipeline()["jobs"]["record-tested"]
    assert job["continue-on-error"] is True
    assert "pull_request.head.repo.full_name == github.repository" in job["if"]
    assert "reuse" in job["if"] and "tests-passed.result == 'success'" in job["if"]
    assert job["permissions"] == {"contents": "write"}


def _select_steps(job):
    return [s for s in job["steps"] if "--deselect" in s.get("run", "")]


def test_deselect_only_with_rules():
    doc = _pipeline()
    config_job = doc["jobs"]["config"]
    step = next(s for s in config_job["steps"] if s.get("id") == "select")
    # The select step exists only for a repo with rules; the output is empty otherwise.
    assert ").select" in step["if"] and "ghtools ci select" in step["run"]
    assert config_job["outputs"]["deselect"] == "${{ steps.select.outputs.deselect }}"
    # It runs before the steps that read the selection.
    ids = [s.get("id") for s in config_job["steps"]]
    assert ids.index("select") < ids.index("paths") < ids.index("reuse")
    # Every consumer passes --deselect only when there is a selection to pass.
    for job, name in (("config", "paths"), ("config", "reuse")):
        s = next(s for s in doc["jobs"][job]["steps"] if s.get("id") == name)
        assert 'if [ -n "$DESELECT" ]' in s["run"] and "--deselect" in s["run"]
    test_run = next(s for s in doc["jobs"]["test"]["steps"] if s.get("id") == "test")["run"]
    assert '"$DESELECT" != "[]"' in test_run
    rec = next(
        s for s in doc["jobs"]["record-tested"]["steps"] if "record-tested" in s.get("run", "")
    )
    # record-tested passes "[]" too: that is a claimed full run, which reuse then accepts.
    assert 'if [ -n "$DESELECT" ]' in rec["run"] and '!= "[]"' not in rec["run"]


def test_export_flags_rules_only_when_set():
    from ghtools import config

    assert config.export(config.from_dict({}))["select"] is False
    cfg = config.from_dict({"ci": {"select": [{"marker": "tui", "changed": ["src/**"]}]}})
    assert config.export(cfg)["select"] is True


def test_release_components_job_skipped_without_components():
    jobs = _jobs()
    job = jobs["release-components"]
    assert "needs.config.outputs.components != '[]'" in job["if"]
    assert "always()" in job["if"] and "needs.config.outputs.default-ref == 'true'" in job["if"]
    assert set(jobs["release"]["needs"]) <= set(job["needs"])
    config_job = jobs["config"]
    assert config_job["outputs"]["components"].endswith("|| '[]' }}")
    step = next(s for s in config_job["steps"] if s.get("id") == "components")
    assert "fromJSON(steps.export.outputs.config).components" in step["if"]
    # Without components the export flag is false, so the step is skipped and the output is '[]'.
    assert config.export(config.from_dict({}))["components"] is False
    cfg = config.from_dict({"subprojects": [{"name": "a", "path": "a", "release": True}]})
    assert config.export(cfg)["components"] is True
    # status still runs whatever a skipped needs entry says, and reads no needs result
    status = jobs["status"]
    assert "release-components" in status["needs"]
    assert "always()" in status["if"] and "needs.*" not in status["if"]
    for s in status["steps"]:
        assert "needs.*.result" not in str(s)


def test_release_components_values_reach_shell_through_env_only():
    job = _jobs()["release-components"]
    for step in job["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")
    gh = next(s for s in job["steps"] if s.get("name") == "GitHub releases")
    assert '"Release $tag"' in str(
        _step("release-components", "Prepare the release commit and tags locally")["run"]
    )
    assert '--title "$name $version"' in gh["run"] and "--verify-tag" in gh["run"]
    assert "ghtools release components decide" in _step("release-components", "Decide")["run"]


def test_race_handling_present():
    job = _jobs()["release-components"]
    assert "release" in job["needs"]  # the root release lands first; never two pushes at once
    push = _step("release-components", "Push release commit and tags")["run"]
    assert "git fetch --quiet origin" in push and "FETCH_HEAD" in push
    assert 'echo "pushed=false"' in push and "moved since this run started" in push
    assert "git push --atomic" in push
    publish = next(s for s in job["steps"] if s.get("name") == "GitHub releases")
    assert "steps.push.outputs.pushed == 'true'" in publish["if"]
    prep = _step("release-components", "Prepare the release commit and tags locally")["run"]
    assert "select(.resume | not)" in prep  # resumed rows are not re-tagged or re-committed


def test_component_job_fast_forwards_only_onto_the_root_release_commit():
    step = _step("release-components", "Continue from the root release commit")
    assert step["env"] == {"ROOT_TAG": "${{ needs.release.outputs.tag }}"}
    assert '"chore(release): $ROOT_TAG [skip ci]"' in step["run"]
    assert "FETCH_HEAD^" in step["run"] and "$GITHUB_SHA" in step["run"]
    # the subject must be what the root release job commits
    assert (
        'git commit -m "chore(release): $TAG [skip ci]"'
        in _step("release", "Prepare the release commit and tag locally")["run"]
    )


def test_component_job_publishes_only_what_it_decided():
    prep = _step("release-components", "Prepare the release commit and tags locally")
    assert 'prepare --decisions "$COMPONENTS"' in prep["run"]
    assert "GH_TOKEN" in prep["env"]
    push = _step("release-components", "Push release commit and tags")["run"]
    assert 'git push --atomic origin "${refs[@]}" "${tags[@]}"' in push
    assert "git push origin" not in push
    loop = _step("release-components", "GitHub releases")["run"]
    assert loop.count("</dev/null") == 2


def _config_step(step_id):
    return next(s for s in _pipeline()["jobs"]["config"]["steps"] if s.get("id") == step_id)


def _run_step(tmp_path, step_id, env, fake_ghtools):
    """Run a config-job step's script with a stub `ghtools`; return (output file text, stub log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "ghtools"
    stub.write_text(f'#!/bin/bash\necho "$@" >> "{tmp_path}/calls"\n{fake_ghtools}\n')
    stub.chmod(0o755)
    out = tmp_path / "out"
    out.write_text("")
    script = tmp_path / f"{step_id}.sh"
    script.write_text(_config_step(step_id)["run"])
    full = {"PATH": f"{bindir}:/usr/bin:/bin", "GITHUB_OUTPUT": str(out), **env}
    subprocess.run(["bash", "-e", str(script)], env=full, check=True, cwd=tmp_path)
    calls = tmp_path / "calls"
    return out.read_text(), calls.read_text() if calls.exists() else ""


def test_a_component_release_forces_ci_over_every_subproject():
    ids = [s.get("id") for s in _pipeline()["jobs"]["config"]["steps"]]
    # The decisions are known before anything that reads them.
    for reader in ("select", "paths", "matrix"):
        assert ids.index("components") < ids.index(reader)
        assert ids.index("decide") < ids.index(reader)
    assert ids.index("directives") < ids.index("select")


@pytest.mark.parametrize("components", ["", "[]"])
def test_no_due_component_leaves_the_run_decision_alone(tmp_path, components):
    env = {"EVENT": "push", "BEFORE": "abc", "COMPONENTS": components}
    out, calls = _run_step(tmp_path, "paths", env, 'echo "false"')
    assert "run-ci=false" in out and "should-run" in calls


def test_a_due_component_runs_ci_like_a_releasing_root(tmp_path):
    env = {"EVENT": "push", "BEFORE": "abc", "COMPONENTS": '[{"name":"a"}]'}
    out, calls = _run_step(tmp_path, "paths", env, 'echo "false"')
    assert "run-ci=true" in out and "should-run" not in calls


def test_matrix_adds_only_due_components(tmp_path):
    base = {"BASE": "a", "HEAD": "b", "FORCE_CI": ""}
    _, calls = _run_step(tmp_path, "matrix", {**base, "COMPONENTS": "[]"}, "true")
    assert "--all" not in calls and "--also" not in calls
    (tmp_path / "calls").unlink()
    due = '[{"name":"a"},{"name":"b c"}]'
    _, calls = _run_step(tmp_path, "matrix", {**base, "COMPONENTS": due}, "true")
    assert "--all" not in calls and "--also a b c" in calls


def test_forced_matrix_with_due_components_still_covers_all(tmp_path):
    env = {"BASE": "a", "HEAD": "b", "FORCE_CI": "true", "COMPONENTS": '[{"name":"a"}]'}
    _, calls = _run_step(tmp_path, "matrix", env, "true")
    assert "--all" in calls


@pytest.mark.parametrize(
    ("extra", "full"),
    [
        ({}, False),
        ({"FORCE_CI": "true"}, True),
        ({"WOULD_RELEASE": "true"}, True),
        ({"COMPONENTS": '[{"name":"a"}]'}, True),
        ({"COMPONENTS": "[]"}, False),
    ],
)
def test_selection_never_deselects_on_a_forced_or_releasing_run(tmp_path, extra, full):
    env = {"EVENT": "push", "BASE": "abc", **extra}
    out, calls = _run_step(tmp_path, "select", env, "echo '[\"tui\"]'")
    if full:
        assert "deselect=[]" in out and "ci select" not in calls
    else:
        assert 'deselect=["tui"]' in out


@pytest.mark.parametrize(
    ("extra", "flag"),
    [
        ({}, False),
        ({"WOULD_RELEASE": "false", "COMPONENTS": "[]"}, False),
        ({"WOULD_RELEASE": "true"}, True),
        ({"COMPONENTS": '[{"name":"a"}]'}, True),
    ],
)
def test_reuse_refuses_local_records_only_when_releasing(tmp_path, extra, flag):
    ids = [s.get("id") for s in _pipeline()["jobs"]["config"]["steps"]]
    assert ids.index("decide") < ids.index("reuse") and ids.index("components") < ids.index("reuse")
    env = {"MATRIX": "{}", "DESELECT": "", "RUNNER_TEMP": str(tmp_path), **extra}
    _, calls = _run_step(tmp_path, "reuse", env, 'echo "false"')
    assert ("--no-local" in calls) is flag
