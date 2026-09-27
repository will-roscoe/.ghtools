"""Load, validate and export .github/ghtools.toml (spec §3.1).

The schema is a registry so later pieces (status, readme, subprojects, directives)
add their own sections with `register_section` instead of editing this module.
Unknown keys are always rejected, with the allowed keys listed.
"""

from __future__ import annotations

import copy
import json
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
        "install": Field(str, ".[dev]"),
        "test": Field(str, "pytest -q"),
        "paths": Field(list, ["**.py", "pyproject.toml"]),
        "gates": Field(list, ["ruff", "ruff-format"]),
        "docker": Field(bool, False),
    },
    "ci.coverage": {
        "package": Field(str, ""),
        "floor": Field(int, 0),
        "codecov": Field(bool, False),
        "flags": Field(str, "python-version", _c("python-version", "none")),
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
    },
}

TABLE_ARRAYS: dict[str, dict[str, Field]] = {
    "ci.gate": {
        "name": Field(str, None),
        "run": Field(str, None),
        "after": Field(str, "test", _c("test", "docs")),
    },
}


def register_section(name: str, fields: dict[str, Field]) -> None:
    SCHEMA[name] = fields


def register_table_array(name: str, fields: dict[str, Field]) -> None:
    TABLE_ARRAYS[name] = fields


def extend_choices(section: str, key: str, *values: Any) -> None:
    field = SCHEMA[section][key]
    field.choices = frozenset((field.choices or frozenset()) | set(values))


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
        "codecov": cfg.get("ci.coverage.codecov"),
        "flags": cfg.get("ci.coverage.flags"),
        "docker": cfg.get("ci.docker"),
        "docs": {"enabled": cfg.get("docs.enabled"), "pages": cfg.get("docs.pages")},
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
