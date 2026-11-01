"""Sphinx docs commands for the pipeline's docs job."""

from __future__ import annotations

import shlex
from pathlib import Path

from .ci import python
from .components import released
from .config import Config


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
