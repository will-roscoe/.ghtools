"""Regenerate src/ghtools/data/python_builds.json from actions/python-versions.

Usage: python scripts/refresh_python_manifest.py [manifest.json]
Without an argument the manifest is fetched from GitHub; with one it is read from disk.
"""

from __future__ import annotations

import datetime
import json
import sys
import urllib.request
from pathlib import Path

from ghtools.runners import snapshot_from_manifest

URL = "https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"
OUT = Path(__file__).resolve().parents[1] / "src" / "ghtools" / "data" / "python_builds.json"


def main(argv: list[str]) -> int:
    if argv:
        manifest = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(URL, timeout=60) as resp:
            manifest = json.load(resp)
    doc = {
        "source": URL,
        "generated": datetime.date.today().isoformat(),
        "builds": snapshot_from_manifest(manifest),
    }
    lines = [
        "{",
        f'  "source": {json.dumps(URL)},',
        f'  "generated": "{doc["generated"]}",',
        '  "builds": {',
    ]
    minors = list(doc["builds"].items())
    for i, (minor, keys) in enumerate(minors):
        lines.append(f"    {json.dumps(minor)}: {{")
        items = list(keys.items())
        for j, (k, v) in enumerate(items):
            lines.append(
                f"      {json.dumps(k)}: {json.dumps(v)}{',' if j < len(items) - 1 else ''}"
            )
        lines.append("    }" + ("," if i < len(minors) - 1 else ""))
    lines += ["  }", "}", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({len(minors)} minors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
