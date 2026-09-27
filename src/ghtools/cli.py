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
    root = repo_dir(args)
    _config.check_paths(_config.load(root), root)
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


def _release_checker(root: Path) -> Callable[[str], bool] | None:
    """`gh release view` as a has-release check, only when gh and a token are available."""
    import shutil

    if not shutil.which("gh") or not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        return None

    def has_release(tag: str) -> bool:
        return (
            subprocess.run(["gh", "release", "view", tag], cwd=root, capture_output=True).returncode
            == 0
        )

    return has_release


def _cmd_release_decide(args: Any) -> int:
    from . import release

    root = repo_dir(args)
    decision = release.decide(_config.load_or_defaults(root), root, _release_checker(root))
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
    return _run_checked(ci.install_command(_config.load(root), args.subproject), root, "install")


def _cmd_ci_test(args: Any) -> int:
    from . import ci

    root = repo_dir(args)
    command = ci.test_command(_config.load(root), args.subproject)
    if args.subproject:
        proc = subprocess.run(["bash", "-o", "pipefail", "-c", command], cwd=root)
        if proc.returncode == 5:  # pytest collected nothing: a subproject without tests
            prefix = "::notice::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
            print(f"{prefix}no tests collected for {args.subproject}", flush=True)
            return 0
        if proc.returncode != 0:
            from .errors import CheckFailed

            raise CheckFailed(f"tests failed (exit {proc.returncode})")
        return 0
    return _run_checked(command, root, "tests")


def _cmd_ci_should_run(args: Any) -> int:
    from . import ci

    root = repo_dir(args)
    print("true" if ci.should_run(_config.load(root), root, args.base) else "false")
    return 0


def _cmd_ci_setup(args: Any) -> int:
    from . import ci

    root = repo_dir(args)
    for command in ci.setup_commands(_config.load(root), args.subproject):
        proc = subprocess.run(["bash", "-o", "pipefail", "-c", command], cwd=root)
        if proc.returncode != 0:
            prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
            print(f"{prefix}setup command failed (exit {proc.returncode}): {command}", flush=True)
    return 0


def _cmd_ci_matrix(args: Any) -> int:
    from .subprojects import matrix

    root = repo_dir(args)
    m, any_legs = matrix(_config.load(root), root, args.base, args.head)
    if args.github_output:
        write_github_output(
            {"matrix": json.dumps(m, separators=(",", ":")), "any": str(any_legs).lower()}
        )
    else:
        print(json.dumps({"matrix": m, "any": any_legs}, separators=(",", ":")))
    return 0


@registrar
def _ci_commands(sub: Any) -> None:
    p = sub.add_parser("ci", help="CI test-job steps")
    cs = p.add_subparsers(dest="ci_cmd", required=True, metavar="<subcommand>")
    ins = cs.add_parser("install", help="pip install ci.install (or a subproject's install)")
    ins.add_argument("--subproject", default=None)
    ins.set_defaults(handler=_cmd_ci_install)
    tst = cs.add_parser("test", help="run ci.test (or a subproject's tests) with coverage/junit")
    tst.add_argument("--subproject", default=None)
    tst.set_defaults(handler=_cmd_ci_test)
    stp = cs.add_parser("setup", help="run a subproject's setup commands (failures warn)")
    stp.add_argument("--subproject", required=True)
    stp.set_defaults(handler=_cmd_ci_setup)
    s = cs.add_parser("should-run", help="does a push since BASE touch ci.paths? (true/false)")
    s.add_argument("--base", default="")
    s.set_defaults(handler=_cmd_ci_should_run)
    m = cs.add_parser("matrix", help="the CI matrix for this change, as one JSON line")
    m.add_argument("--base", default="")
    m.add_argument("--head", default="HEAD")
    m.add_argument("--github-output", action="store_true")
    m.set_defaults(handler=_cmd_ci_matrix)


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


def _cmd_resync(args: Any) -> int:
    from .localtools import resync

    return resync(repo_dir(args))


def _cmd_hooks_install(args: Any) -> int:
    from .localtools import install_pre_push

    print(f"installed {install_pre_push(repo_dir(args))}")
    return 0


@registrar
def _local_commands(sub: Any) -> None:
    r = sub.add_parser("resync", help="after a push: fetch tags, fast-forward, reinstall")
    r.set_defaults(handler=_cmd_resync)
    h = sub.add_parser("hooks", help="local git hooks")
    hs = h.add_subparsers(dest="hooks_cmd", required=True, metavar="<subcommand>")
    i = hs.add_parser("install", help="install the warn-only pre-push hook")
    i.add_argument("--pre-push", action="store_true", default=True)
    i.set_defaults(handler=_cmd_hooks_install)


def _status_write(path: str, fragment: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fragment, indent=2) + "\n", encoding="utf-8")


def _cmd_status_leg(args: Any) -> int:
    from .status import fragments as fr

    root = repo_dir(args)
    cfg = _config.load_or_defaults(root)
    docstrings = None
    if args.canonical and "interrogate" in cfg.get("ci.gates"):
        proc = subprocess.run(
            ["interrogate", "-c", "pyproject.toml", "--fail-under", "0"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        docstrings = fr.parse_interrogate(proc.stdout + proc.stderr)
    _status_write(
        args.out,
        fr.leg(
            args.python,
            args.runner,
            args.outcome,
            args.gates_outcome,
            fr.parse_junit(root / "junit.xml"),
            fr.parse_coverage_xml(root / "coverage.xml"),
            args.canonical,
            docstrings,
            subproject=args.subproject,
        ),
    )
    return 0


def _cmd_status_collect(args: Any) -> int:
    from .status import fragments as fr

    root = repo_dir(args)
    cfg = _config.load_or_defaults(root)
    if args.source == "ci":
        legs = (
            [json.loads(p.read_text()) for p in sorted(Path(args.legs).rglob("*.json"))]
            if args.legs and Path(args.legs).is_dir()
            else []
        )
        legs = [lg for lg in legs if "python" in lg]  # skip other artifacts' JSON (docs.json)
        fragment = fr.ci_fragment(legs, cfg.get("ci.gates"))
    elif args.source == "docs":
        # Only the docs-coverage gate measures documentation; other docs gates passing says nothing.
        measured = "docs-coverage" in cfg.get("ci.gates")
        fragment = fr.docs_fragment(args.result, args.coverage_gate if measured else None)
    else:
        fragment = fr.project_fragment(
            cfg, root, args.version or None, args.open_issues, args.description
        )
    if fragment:
        _status_write(args.out, fragment)
    return 0


def _cmd_status_set(args: Any) -> int:
    from .status.fragments import custom_fragment

    items = [tuple(item.split("=", 1)) for item in args.item]
    if any(len(i) != 2 for i in items):
        raise GhtoolsError("--item takes KEY=VALUE")
    _status_write(args.out, custom_fragment(args.name, args.label, items, args.state))
    return 0


def _cmd_status_publish(args: Any) -> int:
    from .status.publish import publish

    root = repo_dir(args)
    fragments: dict[str, Any] = {}
    for path in args.files:
        fragment = json.loads(Path(path).read_text())
        # Named by its source (what status.extra lists), not by whatever the file is called.
        fragments[fragment.get("source") or Path(path).stem] = fragment
    print(publish(root, fragments, _config.load(root)))
    return 0


def _cmd_status_render(args: Any) -> int:
    from .status.publish import read_published
    from .status.render import render_all

    root = repo_dir(args)
    cfg = _config.load(root)
    _, data = read_published(root, cfg.get("status.branch"))
    for path, content in render_all(data, cfg, root).items():
        target = Path(args.out) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    print(f"wrote {args.out}")
    return 0


def _cmd_status_url(args: Any) -> int:
    from .status.publish import repo_slug, status_url

    root = repo_dir(args)
    slug = repo_slug(root)
    if not slug:
        raise GhtoolsError("origin is not a GitHub URL; can't form the status URL")
    print(status_url(slug, _config.load(root).get("status.branch"), args.path))
    return 0


@registrar
def _status_commands(sub: Any) -> None:
    p = sub.add_parser("status", help="status card and badges on the status branch")
    ss = p.add_subparsers(dest="status_cmd", required=True, metavar="<subcommand>")
    leg = ss.add_parser("leg", help="record one CI matrix leg")
    for flag in ("--python", "--runner", "--outcome", "--out"):
        leg.add_argument(flag, required=True)
    leg.add_argument("--gates-outcome", default="")
    leg.add_argument("--canonical", action="store_true")
    leg.add_argument("--subproject", default="")
    leg.set_defaults(handler=_cmd_status_leg)
    col = ss.add_parser("collect", help="build a ci, docs or project fragment")
    col.add_argument("source", choices=["ci", "docs", "project"])
    col.add_argument("--out", required=True)
    col.add_argument("--legs", default="")
    col.add_argument("--result", default="success")
    col.add_argument("--coverage-gate", default=None)
    col.add_argument("--version", default="")
    col.add_argument("--open-issues", type=int, default=None)
    col.add_argument("--description", default="", help="fallback when pyproject has none")
    col.set_defaults(handler=_cmd_status_collect)
    st = ss.add_parser("set", help="write a custom fragment (listed in status.extra)")
    st.add_argument("name")
    st.add_argument("--label", required=True)
    st.add_argument("--item", action="append", default=[], help="KEY=VALUE, repeatable")
    st.add_argument("--state", choices=["ok", "warn", "fail"], default="ok")
    st.add_argument("--out", required=True)
    st.set_defaults(handler=_cmd_status_set)
    pb = ss.add_parser("publish", help="publish fragment files to the status branch")
    pb.add_argument("files", nargs="+")
    pb.set_defaults(handler=_cmd_status_publish)
    rd = ss.add_parser("render", help="render the published status locally")
    rd.add_argument("--out", default="ghtools-status-preview")
    rd.set_defaults(handler=_cmd_status_render)
    url = ss.add_parser("url", help="print the README URL of a status file")
    url.add_argument("path", nargs="?", default="status.svg")
    url.set_defaults(handler=_cmd_status_url)


def _cmd_readme_sync(args: Any) -> int:
    from . import readme
    from .errors import CheckFailed

    root = repo_dir(args)
    changed = readme.sync(root, _config.load(root), write=args.write)
    if args.check and changed:
        raise CheckFailed(
            f"README blocks out of date: {', '.join(changed)} (run: ghtools readme sync --write)"
        )
    print(f"updated: {', '.join(changed)}" if changed and args.write else "README blocks in sync")
    return 0


@registrar
def _readme_commands(sub: Any) -> None:
    p = sub.add_parser("readme", help="README blocks generated from a single source")
    rs = p.add_subparsers(dest="readme_cmd", required=True, metavar="<subcommand>")
    s = rs.add_parser("sync", help="check or regenerate README blocks")
    mode = s.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    s.set_defaults(handler=_cmd_readme_sync)


def _cmd_subprojects_status(args: Any) -> int:
    from .subprojects import submodule_status

    rows = submodule_status(repo_dir(args))
    if not rows:
        print("no submodules")
    for row in rows:
        print(f"{row['path']:30} {row['pointer'][:7]:8} {row['remote_head'][:7]:8} {row['state']}")
    return 0


@registrar
def _subprojects_commands(sub: Any) -> None:
    p = sub.add_parser("subprojects", help="subprojects and submodules")
    ss = p.add_subparsers(dest="subprojects_cmd", required=True, metavar="<subcommand>")
    st = ss.add_parser("status", help="each submodule pointer against its remote (local only)")
    st.set_defaults(handler=_cmd_subprojects_status)


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
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # An annotation shows on the run summary; %0A keeps multi-line messages in one.
            message = str(exc).replace("%", "%25").replace("\n", "%0A")
            print(f"::error::ghtools: {message}", file=sys.stderr)
        else:
            print(f"ghtools: error: {exc}", file=sys.stderr)
        return exc.exit_code
