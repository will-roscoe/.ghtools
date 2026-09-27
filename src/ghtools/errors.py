"""Exception types that carry the CLI exit code (spec §5.1)."""

from __future__ import annotations


class GhtoolsError(Exception):
    """Usage or configuration problem. Exit code 1."""

    exit_code = 1


class ConfigError(GhtoolsError):
    """Invalid or missing .github/ghtools.toml. Exit code 1."""


class CheckFailed(GhtoolsError):
    """A check, gate or test run failed. Exit code 2."""

    exit_code = 2


class PreconditionError(GhtoolsError):
    """A git/gh precondition was not met (dirty file, missing tag, not a repo). Exit code 3."""

    exit_code = 3
