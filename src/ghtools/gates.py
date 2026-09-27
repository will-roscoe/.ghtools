"""Built-in and custom gates (lint and check commands run by CI and the pre-push hook)."""

from __future__ import annotations

from dataclasses import dataclass


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
