"""Sphinx docs commands for the pipeline's docs job."""

from __future__ import annotations

import shlex
from pathlib import Path

from .ci import python
from .components import released
from .config import Config
from .errors import PreconditionError
from .gitutil import git


def install_command(cfg: Config) -> list[str]:
    return [python(), "-m", "pip", "install", *shlex.split(cfg.get("docs.install"))]


def html_dir(cfg: Config) -> str:
    return f"{cfg.get('docs.dir')}/_build/html"


def build_commands(cfg: Config) -> list[list[str]]:
    """The commands of the docs build, in order: prebuild, ``sphinx-apidoc`` runs, ``sphinx-build``.

    Besides ``docs.apidoc``, each released component's ``apidoc`` packages are documented into
    ``<docs.dir>/api/<name>/`` (``<docs.dir>/api/<name>/<package>/`` when it lists several).
    """
    root = cfg.get("docs.dir")
    commands: list[list[str]] = [
        ["bash", "-o", "pipefail", "-c", c] for c in cfg.get("docs.prebuild")
    ]
    apidoc = cfg.get("docs.apidoc")
    if apidoc:
        out = f"{root}/api/{Path(apidoc).name}"
        commands.append(
            ["sphinx-apidoc", "-f", "-o", out, apidoc, "--separate", "--module-first", "-q"]
        )
    for c in released(cfg):
        for package in c.apidoc:
            # One package keeps the component's own directory; several each get a subdirectory so
            # their modules.rst do not overwrite one another.
            out = f"{root}/api/{c.name}" + (f"/{Path(package).name}" if len(c.apidoc) > 1 else "")
            commands.append(
                ["sphinx-apidoc", "-f", "-o", out, package, "--separate", "--module-first", "-q"]
            )
    build = ["sphinx-build", "-b", "html"]
    if cfg.get("docs.strict"):
        build += ["-W", "--keep-going"]
    commands.append([*build, root, html_dir(cfg)])
    return commands


def release_subject(subject: str) -> bool:
    """Is `subject` a release commit subject, ``chore(release): <what> [skip ci]``?"""
    return subject.startswith("chore(release): ") and subject.endswith(" [skip ci]")


def release_commit(
    root: Path, base: str, head: str, pushed: list[tuple[str, str]]
) -> tuple[str | None, str]:
    """Return the release commit to build the docs from, or ``None``, and the reason.

    `pushed` lists the release commits this run pushed, oldest first, as ``(sha, subject)`` pairs:
    the root release commit, then the components' commit. The newest is returned only if the
    branch head `head` is exactly that commit and the chain from it back to `base` (the commit the
    run tested, ``GITHUB_SHA``) is exactly those commits: each has one parent, the next older
    commit in `pushed` (the oldest's parent is `base`), and the release subject given for it.
    Anything else (no commit pushed, the branch moved on, a commit that is not the expected one)
    returns ``None``.

    :param root: the repository; `head` and every sha must be present in it.
    :param base: the tested commit the release commits were made on.
    :param head: the branch head as fetched now, e.g. ``FETCH_HEAD``.
    :param pushed: ``(sha, expected subject)`` per release commit, oldest first.
    :returns: ``(sha of the newest release commit, reason)`` to build, else ``(None, reason)``.
    """
    if not pushed:
        return None, "this run pushed no release commit"

    def resolve(rev: str) -> str | None:
        try:
            return git("rev-parse", "--verify", "-q", f"{rev}^{{commit}}", cwd=root).strip()
        except PreconditionError:
            return None

    tip = resolve(head)
    newest = resolve(pushed[-1][0])
    if tip is None or newest is None:
        return None, f"cannot resolve {head if tip is None else pushed[-1][0]} in this repository"
    if tip != newest:
        return None, (
            "the branch moved past this run's release commit, so the next run's docs include "
            "the release"
        )
    expected_parent = resolve(base)
    for (sha, subject), parent in zip(
        pushed, [expected_parent, *(resolve(s) for s, _ in pushed[:-1])], strict=True
    ):
        commit = resolve(sha)
        if commit is None or parent is None:
            return None, f"commit {sha} is not in this repository"
        if not release_subject(subject):
            return None, f"{subject!r} is not a release commit subject"
        parents = git("rev-list", "--parents", "-n", "1", commit, cwd=root).split()[1:]
        actual = git("log", "-1", "--format=%s", commit, cwd=root).strip()
        if parents != [parent] or actual != subject:
            return None, (
                f"commit {commit[:12]} is not the release commit this run made "
                f"(subject {actual!r}, expected {subject!r} on {parent[:12]})"
            )
    return newest, f"building the docs from release commit {newest[:12]}"
