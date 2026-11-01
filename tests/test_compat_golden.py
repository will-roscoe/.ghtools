"""Existing repos' configs must keep producing byte-identical output (see the script below)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update_compat_golden.py"
_spec = importlib.util.spec_from_file_location("update_compat_golden", SCRIPT)
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)

FIXTURES = sorted(golden.CONFIGS.glob("*.toml"))


@pytest.mark.parametrize("toml_path", FIXTURES, ids=lambda p: p.stem)
def test_config_output_matches_golden(toml_path, tmp_path):
    expected = json.loads(golden.golden_path(toml_path).read_text(encoding="utf-8"))
    actual = json.loads(golden.dump(golden.capture(toml_path, tmp_path)))
    assert actual == expected
