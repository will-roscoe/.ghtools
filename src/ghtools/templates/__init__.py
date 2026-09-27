"""Jinja templates shipped with ghtools. `[[ ]]` / `[% %]` delimiters keep `${{ }}` literal."""

from __future__ import annotations

from jinja2 import Environment, PackageLoader, StrictUndefined

_ENV = Environment(
    loader=PackageLoader("ghtools", "templates"),
    variable_start_string="[[",
    variable_end_string="]]",
    block_start_string="[%",
    block_end_string="%]",
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
    autoescape=False,
)


def render(template: str, **values: object) -> str:
    return _ENV.get_template(template).render(**values)
