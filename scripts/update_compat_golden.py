"""Record (or check) the compatibility goldens: what ghtools produces for each real repo config.

Run on code known to be good::

    python scripts/update_compat_golden.py

It rewrites ``tests/fixtures/golden/<repo>.json`` from ``tests/fixtures/configs/<repo>.toml``.
``tests/test_compat_golden.py`` imports :func:`capture` and compares it with those files, so a
change that alters existing repos' output fails the test. Never regenerate to make it pass.

A step the fixture cannot complete in a throwaway repo is recorded as ``"error: <message>"``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ghtools import ci, config, release, scaffold, subprojects  # noqa: E402
from ghtools.status import model  # noqa: E402

CONFIGS = ROOT / "tests" / "fixtures" / "configs"
GOLDEN = ROOT / "tests" / "fixtures" / "golden"

# A fixed status fragment (no dates beyond a literal string, no host paths).
FRAGMENT: dict[str, dict[str, Any]] = {
    "project": {
        "name": "demo",
        "description": "A demo project used to pin the status card model.",
        "version": "2.3.0",
        "released": "2026-09-24",
        "open_issues": 1,
        "links": {
            "pypi": "https://pypi.org/project/demo/",
            "docs": "https://x.github.io/demo",
            "repo": "https://github.com/x/demo",
        },
    },
    "ci": {
        "tests": {"passed": 100, "failed": 0, "skipped": 2, "total": 102},
        "coverage": 96.8,
        "docstrings": 91.0,
        "lint": {"status": "passing", "gates": ["ruff"]},
        "python": [{"version": v, "status": "passing"} for v in ["3.11", "3.12"]],
        "builds": {"Linux": {"x86_64": "passing"}},
    },
    "docs": {"build": "passing", "coverage": 100.0},
}


def _try(root: Path, fn: Any) -> Any:
    """Run ``fn``; return its value, or ``"error: <message>"`` with the temp path masked."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - the error text is the golden value
        return f"error: {type(exc).__name__}: {str(exc).replace(str(root), '<root>')}"


def _git(root: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)


def _throwaway_repo(root: Path, cfg: config.Config) -> None:
    """A git repo holding what the config's paths name, tagged v1.0.0 then feat/fix commits."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.gpgsign", "false")
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.0.0"\n')
    if cfg.get("docs.enabled"):
        docs = root / cfg.get("docs.dir")
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "conf.py").write_text("")
    for row in config.in_tree(cfg):
        (root / row["path"]).mkdir(parents=True, exist_ok=True)
        (root / row["path"] / ".keep").write_text("")
    zip_dir = root / cfg.get("release.zip")
    if "github-zip" in cfg.get("release.publish"):
        zip_dir.mkdir(parents=True, exist_ok=True)
        (zip_dir / ".keep").write_text("")
    if cfg.get("status.logo"):
        logo = root / cfg.get("status.logo")
        logo.parent.mkdir(parents=True, exist_ok=True)
        logo.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (root / "README.md").write_text("demo\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "chore: init")
    _git(root, "tag", "-a", "v1.0.0", "-m", "Release v1.0.0")
    for name, msg in (("a.txt", "feat: a"), ("b.txt", "fix: b")):
        (root / name).write_text(msg)
        _git(root, "add", name)
        _git(root, "commit", "-q", "-m", msg)


def _decision(d: release.Decision) -> dict[str, Any]:
    return {**d.as_outputs(), "current": d.current}


def capture(toml_path: Path, tmp: Path) -> dict[str, Any]:
    """The golden record for one config fixture; ``tmp`` is an empty scratch directory."""
    raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    cfg = config.from_dict(raw)
    root = tmp / "repo"
    _throwaway_repo(root, cfg)
    names = [row["name"] for row in cfg.get("subprojects")]
    py = ci.python()

    def norm(cmd: list[str]) -> list[str]:
        return ["<python>" if c == py else c for c in cmd]

    out: dict[str, Any] = {}
    out["stub"] = scaffold.render_stub(
        cfg.get("branch"),
        "pypi" in cfg.get("release.publish"),
        dispatch=bool(cfg.get("directives.dispatch")),
        private_deps=cfg.get("ci.private-deps"),
    )
    out["test_command"] = {"": _try(root, lambda: ci.test_command(cfg))}
    out["install_command"] = {"": _try(root, lambda: norm(ci.install_command(cfg)))}
    for n in names:
        out["test_command"][n] = _try(root, lambda n=n: ci.test_command(cfg, n))
        out["install_command"][n] = _try(root, lambda n=n: norm(ci.install_command(cfg, n)))
    out["setup_commands"] = {
        "": ci.setup_commands(cfg, None),
        **{n: _try(root, lambda n=n: ci.setup_commands(cfg, n)) for n in names},
    }
    out["select"] = {
        "src/x.py": _try(root, lambda: subprojects.select(cfg, ["src/x.py"])),
        "none": _try(root, lambda: subprojects.select(cfg, None)),
    }
    out["release_decide"] = _try(root, lambda: _decision(release.decide(cfg, root)))
    out["status_model"] = _try(root, lambda: model.build_model(FRAGMENT, cfg, root))
    return out


def golden_path(toml_path: Path) -> Path:
    return GOLDEN / f"{toml_path.stem}.json"


def dump(record: dict[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for toml_path in sorted(CONFIGS.glob("*.toml")):
        with tempfile.TemporaryDirectory() as tmp:
            golden_path(toml_path).write_text(dump(capture(toml_path, Path(tmp))), encoding="utf-8")
        print(f"wrote {golden_path(toml_path).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
