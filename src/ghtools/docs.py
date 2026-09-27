"""Sphinx docs commands for docs.yml."""

from __future__ import annotations

import shlex
from pathlib import Path

from .ci import python
from .config import Config


def install_command(cfg: Config) -> list[str]:
    return [python(), "-m", "pip", "install", *shlex.split(cfg.get("docs.install"))]


def html_dir(cfg: Config) -> str:
    return f"{cfg.get('docs.dir')}/_build/html"


def build_commands(cfg: Config) -> list[list[str]]:
    root = cfg.get("docs.dir")
    commands: list[list[str]] = []
    apidoc = cfg.get("docs.apidoc")
    if apidoc:
        out = f"{root}/api/{Path(apidoc).name}"
        commands.append(
            ["sphinx-apidoc", "-f", "-o", out, apidoc, "--separate", "--module-first", "-q"]
        )
    build = ["sphinx-build", "-b", "html"]
    if cfg.get("docs.strict"):
        build += ["-W", "--keep-going"]
    commands.append([*build, root, html_dir(cfg)])
    return commands
