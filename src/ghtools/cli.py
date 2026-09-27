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


def _cmd_version_current(args: Any) -> int:
    tag = gitutil.latest_tag(repo_dir(args))
    if tag:
        print(tag)
    return 0


def _cmd_version_next(args: Any) -> int:
    from .version import compute_next_version, explain

    root = repo_dir(args)
    cfg = _config.load_or_defaults(root)
    current = gitutil.latest_tag(root)
    messages = gitutil.log_messages(gitutil.since_range(current), root)
    if args.explain:
        for first, effect in explain(messages):
            print(f"{effect:<6} {first}", file=sys.stderr)
    nxt = compute_next_version(current, messages, cfg.get("version.zero-major-breaking"))
    if nxt:
        print(nxt)
    return 0


@registrar
def _version_commands(sub: Any) -> None:
    p = sub.add_parser("version", help="versions from Conventional Commits")
    vs = p.add_subparsers(dest="version_cmd", required=True, metavar="<subcommand>")
    vs.add_parser("current", help="newest SemVer release tag").set_defaults(
        handler=_cmd_version_current
    )
    n = vs.add_parser("next", help="version a merge would release now (nothing if none)")
    n.add_argument("--explain", action="store_true", help="list each commit's effect on stderr")
    n.set_defaults(handler=_cmd_version_next)


def _cmd_release_decide(args: Any) -> int:
    from . import release

    root = repo_dir(args)
    decision = release.decide(_config.load_or_defaults(root), root)
    if args.github_output:
        write_github_output(decision.as_outputs())
    if args.summary:
        print(decision.summary())
    elif not args.github_output:
        print(decision.reason)
    return 0


def _cmd_release_prepare(args: Any) -> int:
    from . import release

    root = repo_dir(args)
    for path in release.prepare(_config.load_or_defaults(root), root, args.version.lstrip("v")):
        print(path)
    return 0


def _cmd_release_notes(args: Any) -> int:
    from . import release

    root = repo_dir(args)
    text = release.notes(_config.load_or_defaults(root), root, args.version)
    if text:
        print(text)
    return 0


def _cmd_release_build(args: Any) -> int:
    from . import release

    root = repo_dir(args)
    for path in release.build(_config.load_or_defaults(root), root, args.out):
        print(path.relative_to(root))
    return 0


@registrar
def _release_commands(sub: Any) -> None:
    p = sub.add_parser("release", help="release steps used by release.yml")
    rs = p.add_subparsers(dest="release_cmd", required=True, metavar="<subcommand>")
    d = rs.add_parser("decide", help="decide whether and what to release")
    d.add_argument("--github-output", action="store_true", help="write outputs to $GITHUB_OUTPUT")
    d.add_argument("--summary", action="store_true", help="print a markdown summary")
    d.set_defaults(handler=_cmd_release_decide)
    pr = rs.add_parser("prepare", help="write version file + changelog; print changed paths")
    pr.add_argument("version")
    pr.set_defaults(handler=_cmd_release_prepare)
    no = rs.add_parser("notes", help="print the changelog section for a version")
    no.add_argument("version")
    no.set_defaults(handler=_cmd_release_notes)
    b = rs.add_parser("build", help="build release assets")
    b.add_argument("--out", default="dist")
    b.set_defaults(handler=_cmd_release_build)


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
