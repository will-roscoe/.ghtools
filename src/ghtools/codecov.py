"""`.github/codecov.yml`, rendered from `[ci.coverage]` so Codecov policy is set in one place."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .templates import render as _render

PATH = ".github/codecov.yml"
# Where else Codecov looks. Several of these are read before .github/codecov.yml, so any one of
# them silently replaces the rendered policy.
OTHERS = tuple(
    f"{d}{n}"
    for d in ("", ".github/", "dev/")
    for n in ("codecov.yml", ".codecov.yml", "codecov.yaml", ".codecov.yaml")
    if f"{d}{n}" != PATH
)


def other_configs(root: Path) -> list[str]:
    return [rel for rel in OTHERS if (Path(root) / rel).is_file()]


def _number(value: float) -> str:
    return f"{value:g}"  # 2.0 -> "2", 1.5 -> "1.5"


def render(cfg: Config) -> str:
    return _render(
        "codecov.yml.j2",
        threshold=_number(cfg.get("ci.coverage.threshold")),
        components=cfg.get("ci.coverage.component"),
    )


def sync(root: Path, cfg: Config, write: bool) -> bool:
    """True if .github/codecov.yml differs from the rendering (and, with write, rewrite it).
    With Codecov off there is nothing to keep in step."""
    if not cfg.get("ci.coverage.codecov"):
        return False
    path = Path(root) / PATH
    want = render(cfg)
    if path.is_file() and path.read_text(encoding="utf-8") == want:
        return False
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(want, encoding="utf-8")
    return True
