"""The `ghtools` command line. Subcommands are added by `@registrar` functions below."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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


def _run_checked(cmd: list[str] | str, cwd: Path, what: str) -> int:
    from .errors import CheckFailed

    argv = ["bash", "-o", "pipefail", "-c", cmd] if isinstance(cmd, str) else cmd
    proc = subprocess.run(argv, cwd=cwd)
    if proc.returncode != 0:
        raise CheckFailed(f"{what} failed (exit {proc.returncode})")
    return 0


def _cmd_gates_install(args: Any) -> int:
    from . import ci, gates

    root = repo_dir(args)
    reqs = gates.pip_requirements(gates.resolve(_config.load(root), args.stage))
    if not reqs:
        return 0
    cmd = [ci.python(), "-m", "pip", "install", "--quiet", *reqs]
    return _run_checked(cmd, root, "gate install")


def _cmd_gates_run(args: Any) -> int:
    from . import gates

    root = repo_dir(args)
    gates.run(gates.resolve(_config.load_or_defaults(root), args.stage), root, args.warn_only)
    return 0


@registrar
def _gates_commands(sub: Any) -> None:
    p = sub.add_parser("gates", help="install or run lint/check gates")
    gs = p.add_subparsers(dest="gates_cmd", required=True, metavar="<subcommand>")
    for name, handler in (("install", _cmd_gates_install), ("run", _cmd_gates_run)):
        g = gs.add_parser(name)
        g.add_argument("--stage", choices=["test", "docs"], default="test")
        if name == "run":
            g.add_argument("--warn-only", action="store_true", help="report but exit 0")
        g.set_defaults(handler=handler)


def _cmd_ci_install(args: Any) -> int:
    from . import ci

    root = repo_dir(args)
    return _run_checked(ci.install_command(_config.load(root)), root, "install")


def _cmd_ci_test(args: Any) -> int:
    from . import ci

    root = repo_dir(args)
    return _run_checked(ci.test_command(_config.load(root)), root, "tests")


def _cmd_ci_should_run(args: Any) -> int:
    from . import ci

    root = repo_dir(args)
    print("true" if ci.should_run(_config.load(root), root, args.base) else "false")
    return 0


@registrar
def _ci_commands(sub: Any) -> None:
    p = sub.add_parser("ci", help="CI test-job steps")
    cs = p.add_subparsers(dest="ci_cmd", required=True, metavar="<subcommand>")
    cs.add_parser("install", help="pip install ci.install").set_defaults(handler=_cmd_ci_install)
    cs.add_parser("test", help="run ci.test with coverage/junit").set_defaults(handler=_cmd_ci_test)
    s = cs.add_parser("should-run", help="does a push since BASE touch ci.paths? (true/false)")
    s.add_argument("--base", default="")
    s.set_defaults(handler=_cmd_ci_should_run)


def _cmd_docs_install(args: Any) -> int:
    from . import docs

    root = repo_dir(args)
    return _run_checked(docs.install_command(_config.load(root)), root, "docs install")


def _cmd_docs_build(args: Any) -> int:
    from . import docs

    root = repo_dir(args)
    for cmd in docs.build_commands(_config.load(root)):
        _run_checked(cmd, root, cmd[0])
    return 0


def _cmd_docs_html_dir(args: Any) -> int:
    from . import docs

    print(docs.html_dir(_config.load(repo_dir(args))))
    return 0


@registrar
def _docs_commands(sub: Any) -> None:
    p = sub.add_parser("docs", help="Sphinx docs steps")
    ds = p.add_subparsers(dest="docs_cmd", required=True, metavar="<subcommand>")
    ds.add_parser("install").set_defaults(handler=_cmd_docs_install)
    ds.add_parser("build").set_defaults(handler=_cmd_docs_build)
    ds.add_parser("html-dir").set_defaults(handler=_cmd_docs_html_dir)


def _cmd_init(args: Any) -> int:
    from .scaffold import init_repo

    return init_repo(
        Path(args.cwd),
        yes=args.yes,
        dry_run=args.dry_run,
        keep_old=args.keep_old,
        archive_mode=args.archive,
        write=args.write,
        ref=args.ref,
    )


@registrar
def _init_command(sub: Any) -> None:
    p = sub.add_parser("init", help="set up ghtools in the repository you're in")
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p.add_argument("--yes", action="store_true", help="accept detected values and defaults")
    p.add_argument("--keep-old", action="store_true", help="don't remove replaced workflows")
    p.add_argument("--archive", choices=["snapshot", "none"], default="snapshot")
    p.add_argument("--write", action="store_true", help="on re-run, overwrite ghtools.toml")
    p.add_argument("--ref", default="v1", help="ref of .ghtools the stub pins (default v1)")
    p.set_defaults(handler=_cmd_init)


def _cmd_deinit(args: Any) -> int:
    from .scaffold import deinit_repo

    return deinit_repo(Path(args.cwd), dry_run=args.dry_run)


def _cmd_archive_prune(args: Any) -> int:
    from .archive import prune

    print("removed .github/archive" if prune(repo_dir(args)) else "no .github/archive to remove")
    return 0


@registrar
def _deinit_commands(sub: Any) -> None:
    p = sub.add_parser("deinit", help="undo ghtools init in the repository you're in")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(handler=_cmd_deinit)
    a = sub.add_parser("archive", help="manage the pre-ghtools archive")
    asub = a.add_subparsers(dest="archive_cmd", required=True, metavar="<subcommand>")
    asub.add_parser("prune", help="delete .github/archive").set_defaults(handler=_cmd_archive_prune)


def _cmd_doctor(args: Any) -> int:
    from .doctor import run_checks

    findings = run_checks(repo_dir(args))
    icons = {"ok": "✓", "warn": "!", "fail": "✗"}
    for finding in findings:
        print(f"{icons[finding.level]} {finding.message}")
    return 2 if any(f.level == "fail" for f in findings) else 0


@registrar
def _doctor_command(sub: Any) -> None:
    p = sub.add_parser("doctor", help="check this repo's ghtools setup")
    p.set_defaults(handler=_cmd_doctor)


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
