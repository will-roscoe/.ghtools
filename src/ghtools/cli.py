"""The `ghtools` command line. Subcommands are added by `@registrar` functions below."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

from .errors import GhtoolsError

_REGISTRARS: list[Callable[[Any], None]] = []


def registrar(fn: Callable[[Any], None]) -> Callable[[Any], None]:
    """Register a function that adds subcommands to the top-level subparsers."""
    _REGISTRARS.append(fn)
    return fn


def _version() -> str:
    try:
        return _pkg_version("ghtools")
    except PackageNotFoundError:
        return "0.0.0"


def write_github_output(values: dict[str, str]) -> None:
    """Append `key=value` lines to $GITHUB_OUTPUT (values must be single-line)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        raise GhtoolsError("--github-output needs $GITHUB_OUTPUT, which only GitHub Actions sets")
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghtools", description="Shared GitHub tooling: CI, releases, docs and status."
    )
    parser.add_argument("--version", action="version", version=f"ghtools {_version()}")
    parser.add_argument("-C", dest="cwd", default=".", help="run as if started in this directory")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for register in _REGISTRARS:
        register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    try:
        return int(handler(args) or 0)
    except GhtoolsError as exc:
        print(f"ghtools: error: {exc}", file=sys.stderr)
        return exc.exit_code
