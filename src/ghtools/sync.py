"""Keep other repositories' workflow stubs current, one pull request each."""

from __future__ import annotations

import re
from pathlib import Path

from . import scaffold
from .ci import STUB_PATH

_MARKER = re.compile(r"# ghtools-stub: (\d+)")
_PIN = re.compile(r"pipeline\.yml@(\S+)")


def stub_version(text: str) -> int:
    m = _MARKER.search(text)
    return int(m.group(1)) if m else 0


def stub_ref(text: str) -> str:
    m = _PIN.search(text)
    return m.group(1) if m else scaffold.STUB_REF


def restub(root: Path) -> str | None:
    """The repo's stub re-rendered for the current contract, or None if it is already current."""
    text = (Path(root) / STUB_PATH).read_text(encoding="utf-8")
    if stub_version(text) >= scaffold.STUB_VERSION:
        return None
    return scaffold.current_stub(Path(root))  # keeps the pin; settings from the repo's own toml
