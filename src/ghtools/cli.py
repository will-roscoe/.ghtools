"""The `ghtools` command line. Subcommands are added by `@registrar` functions below."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from . import config as _config
from . import gitutil
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


def repo_dir(args: Any) -> Path:
    """The repository root for commands that need one; falls back to -C for non-repos."""
    try:
        return gitutil.repo_root(args.cwd)
    except GhtoolsError:
        return Path(args.cwd).resolve()


def _print_value(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            print(item if isinstance(item, str) else json.dumps(item))
    elif isinstance(value, bool):
        print("true" if value else "false")
    elif isinstance(value, dict):
        print(json.dumps(value, separators=(",", ":")))
    else:
        print(value)


def _cmd_config_check(args: Any) -> int:
    _config.load(repo_dir(args))
    print(f"{_config.CONFIG_PATH}: ok")
    return 0


def _cmd_config_export(args: Any) -> int:
    print(json.dumps(_config.export(_config.load(repo_dir(args))), separators=(",", ":")))
    return 0


def _cmd_config_get(args: Any) -> int:
    _print_value(_config.load(repo_dir(args)).get(args.key))
    return 0


@registrar
def _config_commands(sub: Any) -> None:
    p = sub.add_parser("config", help="validate or read .github/ghtools.toml")
    cs = p.add_subparsers(dest="config_cmd", required=True, metavar="<subcommand>")
    cs.add_parser("check", help="validate the settings").set_defaults(handler=_cmd_config_check)
    e = cs.add_parser("export", help="settings for pipeline.yml, as one JSON line")
    e.add_argument("--json", action="store_true", required=True)
    e.set_defaults(handler=_cmd_config_export)
    g = cs.add_parser("get", help="print one setting (lists one item per line)")
    g.add_argument("key", help="dotted key, e.g. ci.coverage.floor")
    g.set_defaults(handler=_cmd_config_get)


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
