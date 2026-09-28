"""Built-in and custom gates (lint and check commands run by CI and the pre-push hook)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import CheckFailed

if TYPE_CHECKING:
    from .config import Config


@dataclass(frozen=True)
class GateSpec:
    """A named check: `run` is bash run from the repo root; `{docs_dir}` is substituted."""

    name: str
    run: str
    stage: str = "test"  # "test" (in the CI test job) or "docs" (after the docs build)
    pip: tuple[str, ...] = ()


# actionlint 1.7.12 predates GitHub's `$/` self-repository syntax and rejects it, so the two
# messages it produces for `$/` are ignored, and nothing else.
_ACTIONLINT = (
    "actionlint -ignore 'reusable workflow call \"\\$/' -ignore 'specifying action \"\\$/'"
)

BUILTIN: dict[str, GateSpec] = {
    "ruff": GateSpec("ruff", "ruff check --output-format=github .", pip=("ruff",)),
    "ruff-format": GateSpec("ruff-format", "ruff format --check .", pip=("ruff",)),
    "interrogate": GateSpec("interrogate", "interrogate -c pyproject.toml", pip=("interrogate",)),
    "readme-sync": GateSpec("readme-sync", "ghtools readme sync --check"),
    "codecov-sync": GateSpec("codecov-sync", "ghtools codecov --check"),
    "shellcheck": GateSpec(
        "shellcheck", "git ls-files -z '*.sh' '*.bash' | xargs -0 -r shellcheck -S warning"
    ),
    "yamllint": GateSpec("yamllint", "yamllint .", pip=("yamllint",)),
    "actionlint": GateSpec("actionlint", _ACTIONLINT, pip=("actionlint-py==1.7.12.25",)),
    "docs-coverage": GateSpec(
        "docs-coverage",
        "sphinx-build -q -b coverage {docs_dir} {docs_dir}/_build/coverage"
        " && ! grep -q '^ \\* ' {docs_dir}/_build/coverage/python.txt",
        stage="docs",
    ),
}


def resolve(cfg: Config, stage: str) -> list[GateSpec]:
    """Built-in gates listed in ci.gates, then custom [[ci.gate]]s, for one stage."""
    docs_dir = cfg.get("docs.dir")
    specs = [BUILTIN[name] for name in cfg.get("ci.gates")]
    specs = [GateSpec(s.name, s.run.replace("{docs_dir}", docs_dir), s.stage, s.pip) for s in specs]
    specs += [GateSpec(row["name"], row["run"], row["after"]) for row in cfg.get("ci.gate")]
    return [s for s in specs if s.stage == stage]


def pip_requirements(specs: list[GateSpec]) -> list[str]:
    seen: list[str] = []
    for spec in specs:
        for req in spec.pip:
            if req not in seen:
                seen.append(req)
    return seen


def run(specs: list[GateSpec], cwd: Path, warn_only: bool = False) -> list[str]:
    """Run every gate (never stopping early); return failed names, or raise CheckFailed."""
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    failed: list[str] = []
    for spec in specs:
        print(f"::group::gate {spec.name}" if in_actions else f"== gate {spec.name}", flush=True)
        proc = subprocess.run(["bash", "-o", "pipefail", "-c", spec.run], cwd=cwd)
        if in_actions:
            print("::endgroup::", flush=True)
        if proc.returncode != 0:
            failed.append(spec.name)
            if in_actions:
                label = "::warning::" if warn_only else "::error::"
            else:
                label = ""
            print(f"{label}gate {spec.name} failed (exit {proc.returncode})", flush=True)
    if failed and not warn_only:
        raise CheckFailed(f"gates failed: {', '.join(failed)}")
    return failed
