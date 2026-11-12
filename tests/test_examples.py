"""C28: the example projects keep what they remember in a memory.md, and resolve as before the move."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from memhub.settings import resolve

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "tests" / "fixtures" / "examples_resolved_base.json").read_text())


@pytest.mark.parametrize("name", ["habitantes", "maintenance", "support"])
def test_examples_match_base(name, monkeypatch):
    for var in ("MEMHUB_DATABASE_URL", "MAINT_DATABASE_URL", "CRM_URL", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.setenv(var, "postgresql://u@h/d")
    folder = ROOT / "examples" / name
    assert yaml.safe_load((folder / "memhub.yaml").read_text())["memory"] == "./memory.md"
    assert (folder / "memory.md").is_file()
    r = resolve(folder / "memhub.yaml", environ=dict(os.environ), strict=False)
    cfg, want = r.config, BASE[name]
    got = {t: c.model_dump(mode="json", by_alias=True) for t, c in cfg.types.items() if t not in ("area", "term")}
    assert got == want["types"]
    assert [s.model_dump() for s in cfg.areas.seeds] == want["areas"] and cfg.areas.open == want["areas_open"]
    assert cfg.extraction.instructions.strip() == want["instructions"].strip()  # a yaml literal block ends in "\n"
    assert all(src == "memory.md" for key, src in r.sources.items() if key.startswith("types."))
