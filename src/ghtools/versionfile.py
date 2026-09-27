"""Read and write the file that records a repo's version (pyproject.toml or manifest.json)."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .config import Config
from .errors import GhtoolsError
from .gitutil import latest_tag

_TABLE = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*(?:#.*)?$")
_VERSION = re.compile(r"""^(\s*version\s*=\s*)(["'])([^"']*)\2(.*)$""")


def version_file(cfg: Config) -> str:
    source = cfg.get("version.source")
    if source == "pyproject":
        return "pyproject.toml"
    if source == "manifest":
        return cfg.get("version.manifest")
    return ""


def set_pyproject_version(text: str, version: str) -> str:
    """Rewrite `version = "…"` inside the [project] table only, keeping everything else."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    table = None
    for i, line in enumerate(lines):
        header = _TABLE.match(line)
        if header:
            table = header.group(1).strip()
            continue
        if table == "project":
            match = _VERSION.match(line)
            if match:
                quote = match.group(2)
                lines[i] = f"{match.group(1)}{quote}{version}{quote}{match.group(4)}"
                return newline.join(lines)
    raise GhtoolsError(
        'pyproject.toml has no static `version = "..."` in [project]; '
        "use version.source = 'tag' or add one"
    )


def read_version(cfg: Config, root: Path) -> str | None:
    source = cfg.get("version.source")
    if source == "tag":
        tag = latest_tag(root)
        return tag[1:] if tag else None
    path = Path(root) / version_file(cfg)
    if source == "pyproject":
        return tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}).get("version")
    return json.loads(path.read_text(encoding="utf-8")).get("version")


def _json_indent(raw: str) -> int:
    match = re.search(r"\n( +)\S", raw.replace("\r\n", "\n"))
    return len(match.group(1)) if match else 2


def write_version(cfg: Config, root: Path, version: str) -> str:
    source = cfg.get("version.source")
    if source == "tag":
        raise GhtoolsError("version.source = 'tag' has no version file to write")
    rel = version_file(cfg)
    path = Path(root) / rel
    raw = path.read_bytes().decode("utf-8")
    if source == "pyproject":
        new = set_pyproject_version(raw, version)
    else:
        data = json.loads(raw)
        data["version"] = version
        new = json.dumps(data, indent=_json_indent(raw), ensure_ascii=False) + "\n"
        if "\r\n" in raw:
            new = new.replace("\n", "\r\n")
    path.write_bytes(new.encode("utf-8"))
    return rel
