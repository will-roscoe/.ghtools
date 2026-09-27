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
