"""Released components: in-tree subprojects with ``release = true``, versioned on their own.

:func:`released` lists them as :class:`Component` rows with the optional keys resolved to their
defaults. A repo that sets no ``release = true`` gets an empty list and behaves as before.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class Component:
    """One released component.

    :ivar name: the subproject name, also ``{name}`` in `tag`.
    :ivar path: its directory, relative to the repo root.
    :ivar version_file: where its version lives (default ``<path>/pyproject.toml``).
    :ivar changelog: its changelog (default ``<path>/CHANGELOG.md``).
    :ivar tag: the tag template, using only ``{name}`` and ``{version}``.
    :ivar needs_paths: paths of every subproject it needs, transitively, dependencies first.
    """

    name: str
    path: str
    version_file: str
    changelog: str
    tag: str
    needs_paths: tuple[str, ...]

    def tag_for(self, version: str) -> str:
        """The tag name for `version`, e.g. ``cdn/0.4.1``."""
        return self.tag.format(name=self.name, version=version)


def released(cfg: Config) -> list[Component]:
    """The in-tree ``release = true`` subprojects, in config order.

    Submodule rows are never included: their own repository releases them.

    :param cfg: the loaded config.
    :returns: one :class:`Component` per released row; ``[]`` when none is configured.
    """
    rows = {row["name"]: row for row in cfg.get("subprojects")}

    def needed(name: str, seen: list[str]) -> None:
        for need in rows[name]["needs"]:
            if need not in seen:
                needed(need, seen)
                seen.append(need)

    out = []
    for row in rows.values():
        if not (row["release"] and row["kind"] == "in-tree"):
            continue
        order: list[str] = []
        needed(row["name"], order)
        out.append(
            Component(
                name=row["name"],
                path=row["path"],
                version_file=row["version-file"] or f"{row['path']}/pyproject.toml",
                changelog=row["changelog"] or f"{row['path']}/CHANGELOG.md",
                tag=row["tag"],
                needs_paths=tuple(rows[n]["path"] for n in order),
            )
        )
    return out
