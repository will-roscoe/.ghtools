"""Load, validate and export .github/ghtools.toml (spec §3.1).

The schema is a registry so later pieces (status, readme, subprojects, directives)
add their own sections with `register_section` instead of editing this module.
Unknown keys are always rejected, with the allowed keys listed.
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError

CONFIG_PATH = ".github/ghtools.toml"


@dataclass
class Field:
    type: type
    default: Any
    choices: frozenset[Any] | None = None


def _c(*values: Any) -> frozenset[Any]:
    return frozenset(values)


SCHEMA: dict[str, dict[str, Field]] = {
    "": {
        "schema": Field(int, 1, _c(1)),
        "branch": Field(str, "main"),
        "profile": Field(str, "python", _c("python", "hacs", "lint", "umbrella")),
    },
    "version": {
        "source": Field(str, "tag", _c("tag", "pyproject", "manifest")),
        "bump": Field(str, "write", _c("write", "require")),
        "manifest": Field(str, ""),
        "zero-major-breaking": Field(str, "minor", _c("minor", "major")),
    },
    "release": {
        "enabled": Field(bool, True),
        "publish": Field(list, ["github"], _c("github", "github-zip", "pypi")),
        "zip": Field(str, ""),
        "commit": Field(bool, True),
        "changelog": Field(str, "CHANGELOG.md"),
    },
    "ci": {
        "python": Field(list, ["3.12"]),
        "runners": Field(list, ["ubuntu-latest"]),
        # Editable, so tests import the source tree and --cov=<path> collects data.
        "install": Field(str, "-e .[dev]"),
        "test": Field(str, "pytest -q"),
        "paths": Field(list, ["**.py", "pyproject.toml"]),
        "gates": Field(list, ["ruff", "ruff-format"]),
        "docker": Field(bool, False),
        "setup": Field(list, []),  # commands run after install, before gates and tests
        "private-deps": Field(bool, False),  # DEPS_TOKEN reads private GitHub git dependencies
    },
    "ci.coverage": {
        "package": Field(str, ""),
        "floor": Field(int, 0),
        "codecov": Field(bool, False),
        "flags": Field(str, "python-version", _c("python-version", "subproject", "none")),
    },
    "docs": {
        "enabled": Field(bool, False),
        "kind": Field(str, "sphinx", _c("sphinx")),
        "dir": Field(str, "docs"),
        "install": Field(str, ".[docs]"),
        "apidoc": Field(str, ""),
        "strict": Field(bool, True),
        "pages": Field(bool, False),
        "apt": Field(list, []),
        "prebuild": Field(list, []),  # commands run before sphinx-build (e.g. generated sources)
    },
    "status": {
        "enabled": Field(bool, False),
        "branch": Field(str, "ghtools-status"),
        "card": Field(
            list,
            ["tests", "coverage", "lint", "docs"],
            _c("tests", "coverage", "lint", "docs", "docstrings", "release", "open-issues"),
        ),
        "rows": Field(list, ["python", "builds"], _c("python", "builds", "extra")),
        "links": Field(list, ["pypi", "docs", "repo"], _c("pypi", "docs", "repo")),
        "logo": Field(str, ""),
        "extra": Field(list, []),
        "badges": Field(
            list,
            ["version", "coverage", "tests", "lint", "python"],
            _c("version", "coverage", "tests", "lint", "python", "docs", "docstrings"),
        ),
        "description": Field(str, ""),
    },
    "readme": {},  # holds only the [[readme.block]] table array
    "directives": {
        "enabled": Field(bool, False),
        "dispatch": Field(dict, {}),  # directive name -> workflow file in .github/workflows/
    },
}

TABLE_ARRAYS: dict[str, dict[str, Field]] = {
    "ci.gate": {
        "name": Field(str, None),
        "run": Field(str, None),
        "after": Field(str, "test", _c("test", "docs")),
    },
    "readme.block": {
        "name": Field(str, None),
        "kind": Field(str, "sync", _c("sync", "status")),
        "source": Field(str, ""),
        "heading-offset": Field(int, 0),
    },
    "subprojects": {
        "name": Field(str, None),
        "path": Field(str, None),
        "kind": Field(str, "in-tree", _c("in-tree", "submodule")),
        "package": Field(str, ""),
        "tests": Field(str, ""),
        "install": Field(list, []),
        "needs": Field(list, []),
        "setup": Field(list, []),
    },
}


def register_section(name: str, fields: dict[str, Field]) -> None:
    SCHEMA[name] = fields


def register_table_array(name: str, fields: dict[str, Field]) -> None:
    TABLE_ARRAYS[name] = fields


def extend_choices(section: str, key: str, *values: Any) -> None:
    field = SCHEMA[section][key]
    field.choices = frozenset((field.choices or frozenset()) | set(values))


extend_choices("status", "rows", "subprojects")  # piece D: one dot per in-tree subproject


class Config:
    """Validated settings with every default filled in. `get` takes a dotted path."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def get(self, dotted: str) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ConfigError(f"unknown key '{dotted}'")
            node = node[part]
        return node


def _node(tree: dict[str, Any], dotted: str) -> dict[str, Any]:
    node = tree
    for part in [p for p in dotted.split(".") if p]:
        node = node.setdefault(part, {})
    return node


def defaults() -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for section, fields in SCHEMA.items():
        node = _node(tree, section)
        for key, field in fields.items():
            node[key] = copy.deepcopy(field.default)
    for name in TABLE_ARRAYS:
        parent, _, key = name.rpartition(".")
        _node(tree, parent)[key] = []
    return tree


def _check_value(dotted: str, field: Field, value: Any) -> Any:
    if field.type is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{dotted}: expected true or false, got {value!r}")
    elif field.type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{dotted}: expected an integer, got {value!r}")
    elif field.type is str:
        if not isinstance(value, str):
            raise ConfigError(f"{dotted}: expected a string, got {value!r}")
    elif field.type is list:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{dotted}: expected a list of strings, got {value!r}")
        for item in value:
            if field.choices is not None and item not in field.choices:
                raise ConfigError(f"{dotted}: {item!r} is not one of {sorted(field.choices)}")
        return list(value)
    elif field.type is dict:
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise ConfigError(f"{dotted}: expected a table of strings, got {value!r}")
        return dict(value)
    if field.choices is not None and value not in field.choices:
        raise ConfigError(f"{dotted}: {value!r} is not one of {sorted(field.choices)}")
    return value


def _check_row(name: str, index: int, row: dict[str, Any]) -> dict[str, Any]:
    fields = TABLE_ARRAYS[name]
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key not in fields:
            raise ConfigError(
                f"unknown key '{name}[{index}].{key}' (allowed here: {', '.join(fields)})"
            )
        out[key] = _check_value(f"{name}[{index}].{key}", fields[key], value)
    for key, field in fields.items():
        if key not in out:
            if field.default is None:
                raise ConfigError(f"{name}[{index}].{key}: required")
            out[key] = copy.deepcopy(field.default)
    return out


def _children(path: str) -> set[str]:
    names = {*SCHEMA, *TABLE_ARRAYS}
    return {n.split(".")[-1] for n in names if n and n.rpartition(".")[0] == path}


def _walk(raw: dict[str, Any], path: str, out: dict[str, Any]) -> None:
    fields = SCHEMA.get(path, {})
    for key, value in raw.items():
        dotted = f"{path}.{key}" if path else key
        if key in fields:
            out[key] = _check_value(dotted, fields[key], value)
        elif dotted in SCHEMA:
            if not isinstance(value, dict):
                raise ConfigError(f"{dotted}: expected a table ([{dotted}])")
            _walk(value, dotted, out.setdefault(key, {}))
        elif dotted in TABLE_ARRAYS:
            if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
                raise ConfigError(f"{dotted}: expected an array of tables ([[{dotted}]])")
            out[key] = [_check_row(dotted, i, row) for i, row in enumerate(value)]
        else:
            allowed = sorted(set(fields) | _children(path))
            raise ConfigError(f"unknown key '{dotted}' (allowed here: {', '.join(allowed)})")


def _cross_checks(cfg: Config) -> None:
    for key in ("ci.python", "ci.runners"):
        if not cfg.get(key):
            raise ConfigError(f"{key}: must not be empty")
    from .gates import BUILTIN  # local import: gates imports config in Task 8

    for gate in cfg.get("ci.gates"):
        if gate not in BUILTIN:
            builtins = ", ".join(sorted(BUILTIN))
            raise ConfigError(f"ci.gates: unknown gate '{gate}' (built-ins: {builtins})")
    for i, row in enumerate(cfg.get("ci.gate")):
        if row["name"] in BUILTIN:
            raise ConfigError(f"ci.gate[{i}].name: '{row['name']}' clashes with a built-in gate")
    if cfg.get("version.source") == "manifest" and not cfg.get("version.manifest"):
        raise ConfigError("version.manifest: required when version.source = 'manifest'")
    if "github-zip" in cfg.get("release.publish") and not cfg.get("release.zip"):
        raise ConfigError("release.zip: required when release.publish includes 'github-zip'")
    if (
        not cfg.get("release.commit")
        and cfg.get("version.source") != "tag"
        and cfg.get("version.bump") == "write"
    ):
        raise ConfigError(
            "release.commit = false needs version.source = 'tag' or version.bump = 'require' "
            "(a written version must be committed)"
        )
    words = cfg.get("ci.test").split()
    is_pytest = words[:1] == ["pytest"] or words[:3] == ["python", "-m", "pytest"]
    if cfg.get("ci.coverage.package") and not is_pytest:
        raise ConfigError("ci.coverage.package needs ci.test to run pytest")
    if cfg.get("status.branch") == cfg.get("branch"):
        raise ConfigError(
            "status.branch: must not be the default branch (publishing replaces the whole branch)"
        )
    seen_blocks: set[str] = set()
    for i, block in enumerate(cfg.get("readme.block")):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", block["name"]):
            raise ConfigError(
                f"readme.block[{i}].name: {block['name']!r} must match [a-z0-9][a-z0-9_-]*"
            )
        if block["name"] in seen_blocks:
            raise ConfigError(f"readme.block[{i}].name: duplicate block name {block['name']!r}")
        seen_blocks.add(block["name"])
        if not -1 <= block["heading-offset"] <= 2:  # = - ~ map to ## ### ####; keep 1..6
            raise ConfigError(f"readme.block[{i}].heading-offset: must be between -1 and 2")
        if block["kind"] == "sync" and not block["source"]:
            raise ConfigError(f"readme.block[{i}].source: required for kind = 'sync'")
    _check_subprojects(cfg)
    from .directives import BUILTIN as DIRECTIVES

    for name, workflow in cfg.get("directives.dispatch").items():
        if name in DIRECTIVES or name.removesuffix("-full") in DIRECTIVES:
            raise ConfigError(f"directives.dispatch: {name!r} is a built-in directive")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise ConfigError(f"directives.dispatch: {name!r} must match [a-z][a-z0-9-]*")
        # dispatch.yml word-splits the list, so names must be plain file names.
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*\.ya?ml", workflow):
            raise ConfigError(
                f"directives.dispatch.{name}: expected a workflow file name like ci.yml, "
                f"got {workflow!r}"
            )
    for name in cfg.get("status.extra"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ConfigError(
                f"status.extra: {name!r} is not a valid fragment name (a-z, 0-9, - or _)"
            )


def in_tree(cfg: Config) -> list[dict[str, Any]]:
    return [s for s in cfg.get("subprojects") if s["kind"] == "in-tree"]


def _check_subprojects(cfg: Config) -> None:
    rows = cfg.get("subprojects")
    names: list[str] = []
    for i, row in enumerate(rows):
        path = row["path"].strip().removeprefix("./").rstrip("/")
        parts = path.split("/")
        if not path or path.startswith("/") or ".." in parts:
            raise ConfigError(
                f"subprojects[{i}].path: {row['path']!r} must be a relative path "
                "inside the repository"
            )
        row["path"] = path  # "./c/" and "c" must select the same files
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", row["name"]):
            raise ConfigError(
                f"subprojects[{i}].name: {row['name']!r} must match [a-z0-9][a-z0-9_-]*"
            )
        if row["name"] in names:
            raise ConfigError(f"subprojects[{i}].name: duplicate subproject {row['name']!r}")
        names.append(row["name"])
    for i, row in enumerate(rows):
        for need in row["needs"]:
            if need not in names:
                raise ConfigError(f"subprojects[{i}].needs: unknown subproject {need!r}")
    graph = {row["name"]: row["needs"] for row in rows}

    def visit(node: str, trail: list[str]) -> None:
        if node in trail:
            cycle = " -> ".join([*trail[trail.index(node) :], node])
            raise ConfigError(f"subprojects: dependency cycle {cycle}")
        for nxt in graph[node]:
            visit(nxt, [*trail, node])

    for name in names:
        visit(name, [])


def check_paths(cfg: Config, root: Path) -> None:
    """Checks that need the repository: every path the settings name must exist."""
    root = Path(root)
    source = cfg.get("version.source")
    if source == "pyproject" and not (root / "pyproject.toml").is_file():
        raise ConfigError("version.source = 'pyproject' but pyproject.toml does not exist")
    manifest = cfg.get("version.manifest")
    if source == "manifest" and not (root / manifest).is_file():
        raise ConfigError(f"version.manifest: {manifest} does not exist")
    zip_dir = cfg.get("release.zip")
    if "github-zip" in cfg.get("release.publish") and not (root / zip_dir).is_dir():
        raise ConfigError(f"release.zip: {zip_dir} is not a directory")
    if cfg.get("docs.enabled") and not (root / cfg.get("docs.dir") / "conf.py").is_file():
        raise ConfigError(f"docs.dir: {cfg.get('docs.dir')}/conf.py does not exist")
    logo = cfg.get("status.logo")
    inside = (root / logo).resolve().is_relative_to(root.resolve())
    if logo and not (inside and (root / logo).is_file()):
        raise ConfigError(f"status.logo: {logo} must be a file inside the repository")
    for i, row in enumerate(cfg.get("subprojects")):
        # Submodules are skipped: CI checks out without them, and they build in their own repos.
        if row["kind"] == "in-tree" and not (root / row["path"]).is_dir():
            raise ConfigError(f"subprojects[{i}].path: {row['path']} does not exist")


def from_dict(raw: dict[str, Any]) -> Config:
    data = defaults()
    _walk(raw, "", data)
    cfg = Config(data)
    _cross_checks(cfg)
    return cfg


def load(root: Path) -> Config:
    path = Path(root) / CONFIG_PATH
    if not path.is_file():
        raise ConfigError(f"{CONFIG_PATH} not found; run `ghtools init` in the repository first")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_PATH}: {exc}") from exc
    return from_dict(raw)


def load_or_defaults(root: Path) -> Config:
    """The repo's config if it has one, else defaults (for local commands in any repo)."""
    return load(root) if (Path(root) / CONFIG_PATH).is_file() else from_dict({})


def export(cfg: Config) -> dict[str, Any]:
    """The subset pipeline.yml needs, as one JSON-serialisable object."""
    python, runners = cfg.get("ci.python"), cfg.get("ci.runners")
    include = [{"python": p, "runner": runners[0]} for p in python]
    include += [{"python": python[-1], "runner": r} for r in runners[1:]]
    publish = cfg.get("release.publish")
    return {
        "profile": cfg.get("profile"),
        "branch": cfg.get("branch"),
        "matrix": {"include": include},
        "python_latest": python[-1],
        "codecov": cfg.get("ci.coverage.codecov"),
        "flags": cfg.get("ci.coverage.flags"),
        "docker": cfg.get("ci.docker"),
        "docs": {"enabled": cfg.get("docs.enabled"), "pages": cfg.get("docs.pages")},
        "status": {"enabled": cfg.get("status.enabled")},
        "subprojects": bool(in_tree(cfg)),
        "release": {
            "enabled": cfg.get("release.enabled"),
            "commit": cfg.get("release.commit"),
            "publish": publish,
            "pypi": "pypi" in publish,
        },
    }


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items()) + " }"
        )
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v) for v in value) + "]"
    return json.dumps(value)


def dump_toml(values: dict[str, Any], comments: dict[str, str] | None = None) -> str:
    """Render dotted-key `values` as ghtools.toml, sections in schema order, with comments."""
    comments = comments or {}
    lines = ["# ghtools settings: https://github.com/will-roscoe/.ghtools#configuration", ""]
    for section, fields in SCHEMA.items():
        keys = [k for k in fields if (f"{section}.{k}" if section else k) in values]
        if not keys:
            continue
        if section:
            lines.append(f"[{section}]")
        for key in keys:
            dotted = f"{section}.{key}" if section else key
            line = f"{key} = {format_value(values[dotted])}"
            if dotted in comments:
                line = f"{line:<44} # {comments[dotted]}"
            lines.append(line)
        lines.append("")
    for name, fields in TABLE_ARRAYS.items():
        for row in values.get(name, []):
            lines.append(f"[[{name}]]")
            lines += [f"{k} = {format_value(row[k])}" for k in fields if k in row]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
