from __future__ import annotations

from pathlib import Path

import yaml

from ghtools import config

ROOT = Path(__file__).resolve().parent.parent


def test_own_settings_are_valid_and_gate_the_workflows():
    cfg = config.load(ROOT)
    assert cfg.get("profile") == "python"
    assert cfg.get("version.source") == "tag"
    assert "actionlint" in cfg.get("ci.gates")
    assert ".github/**" in cfg.get("ci.paths")


def test_self_workflow_moves_the_major_tag_only_for_final_releases():
    doc = yaml.safe_load((ROOT / ".github/workflows/self.yml").read_text())
    assert doc["jobs"]["pipeline"]["uses"] == "$/.github/workflows/pipeline.yml"
    move = doc["jobs"]["major-tag"]
    assert "!contains(needs.pipeline.outputs.tag, '-')" in move["if"]
