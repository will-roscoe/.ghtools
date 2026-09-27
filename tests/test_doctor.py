from __future__ import annotations

import subprocess

from ghtools import doctor, scaffold

TOML = """\
branch = "main"
[release]
publish = ["github", "pypi"]
[ci.coverage]
codecov = true
[docs]
enabled = true
pages = true
"""


def _setup(tmp_path, stub_version=1):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/ghtools.toml").write_text(TOML)
    stub = scaffold.render_stub("main", pypi=True).replace(
        "ghtools-stub: 1", f"ghtools-stub: {stub_version}"
    )
    (tmp_path / ".github/workflows/ghtools.yml").write_text(stub)


class FakeGh:
    def __init__(self, responses):
        self.responses = responses

    def __call__(self, args):
        return self.responses.get(" ".join(args))


GOOD = {
    "repo view --json nameWithOwner,visibility": {"nameWithOwner": "o/r", "visibility": "PUBLIC"},
    "secret list --json name": [{"name": "CODECOV_TOKEN"}],
    "api repos/o/r/rulesets": [],
    "api repos/o/r/pages": {"build_type": "workflow"},
}


def test_healthy_repo(tmp_path):
    _setup(tmp_path)
    findings = doctor.run_checks(tmp_path, FakeGh(GOOD))
    assert all(f.level in ("ok", "warn") for f in findings)
    assert [f for f in findings if f.level == "warn"][0].message.startswith("PyPI")


def test_missing_config_fails(tmp_path):
    findings = doctor.run_checks(tmp_path, FakeGh(GOOD))
    assert findings[0].level == "fail"
    assert "ghtools init" in findings[0].message


def test_old_stub_warns(tmp_path):
    _setup(tmp_path, stub_version=0)
    msgs = [f.message for f in doctor.run_checks(tmp_path, FakeGh(GOOD)) if f.level == "warn"]
    assert any("contract v0 is older than v1" in m for m in msgs)


def test_github_side_problems_warn(tmp_path):
    _setup(tmp_path)
    bad = dict(GOOD)
    bad["secret list --json name"] = []
    bad["api repos/o/r/rulesets"] = [{"id": 7}]
    bad["api repos/o/r/rulesets/7"] = {
        "name": "Protect",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
        "rules": [{"type": "pull_request"}],
    }
    bad["api repos/o/r/pages"] = {"build_type": "legacy"}
    msgs = [f.message for f in doctor.run_checks(tmp_path, FakeGh(bad)) if f.level == "warn"]
    assert any("CODECOV_TOKEN" in m for m in msgs)
    assert any("ruleset 'Protect'" in m for m in msgs)
    assert any("GitHub Pages" in m for m in msgs)


def test_gh_unavailable_is_a_single_warning(tmp_path):
    _setup(tmp_path)
    findings = doctor.run_checks(tmp_path, FakeGh({}))
    assert findings[-1] == doctor.Finding(
        "warn", "gh unavailable or not logged in: GitHub-side checks skipped"
    )


def test_status_branch_missing_and_force_push_ruleset(tmp_path):
    _setup(tmp_path)
    (tmp_path / ".github/ghtools.toml").write_text(TOML + "[status]\nenabled = true\n")
    gh = dict(GOOD)
    gh["api repos/o/r/branches/ghtools-status"] = None
    gh["api repos/o/r/rulesets"] = [{"id": 9}]
    gh["api repos/o/r/rulesets/9"] = {
        "name": "All branches",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~ALL"]}},
        "rules": [{"type": "non_fast_forward"}],
    }
    msgs = [f.message for f in doctor.run_checks(tmp_path, FakeGh(gh)) if f.level == "warn"]
    assert any("ghtools-status branch doesn't exist yet" in m for m in msgs)
    assert any("ruleset 'All branches' blocks force-pushes to ghtools-status" in m for m in msgs)


def test_codecov_flags_missing_a_subproject(tmp_path):
    _setup(tmp_path)
    toml = TOML.replace("codecov = true\n", 'codecov = true\nflags = "subproject"\n')
    (tmp_path / ".github/ghtools.toml").write_text(
        toml + '[[subprojects]]\nname = "a"\npath = "a"\n'
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "codecov.yml").write_text("flag_management:\n  individual_flags:\n    - name: b\n")
    msgs = [f.message for f in doctor.run_checks(tmp_path, FakeGh(GOOD)) if f.level == "warn"]
    assert any("codecov.yml has no individual_flags entry for: a" in m for m in msgs)


def test_nested_repo_that_is_neither_submodule_nor_ignored(tmp_path):
    _setup(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "stray/.git").mkdir(parents=True)
    msgs = [f.message for f in doctor.run_checks(tmp_path, FakeGh(GOOD)) if f.level == "warn"]
    assert any(
        "stray is a nested git repo that is neither a submodule nor gitignored" in m for m in msgs
    )
