"""The three presets shipped in the package."""
from __future__ import annotations

from importlib import resources

import pytest
import yaml

from memhub.settings import preset_names, resolve

NAMES = ["assistant", "maintenance", "support"]
FORBIDDEN = ["project_prefix", "project", "database_url", "db", "connect", "models", "llm", "embeddings", "sources",
             "database_schema", "workspace_default"]


def _load(name: str) -> dict:
    return yaml.safe_load(resources.files("memhub.presets").joinpath(f"{name}.yaml").read_text())


def test_presets_are_package_resources():
    assert preset_names() == NAMES
    for name in NAMES:
        assert resources.files("memhub.presets").joinpath(f"{name}.yaml").is_file()


@pytest.mark.parametrize("name", NAMES)
def test_presets_hold_no_project_keys_and_build_registry(name):
    data = _load(name)
    assert [k for k in FORBIDDEN if k in data] == []
    registry = resolve({"preset": name, "project": "p"}, environ={}).config.build_registry()
    assert registry is not None


def test_each_preset_defines_its_types_and_policy():
    cfg = {n: resolve({"preset": n, "project": "p"}, environ={}).config for n in NAMES}
    inc = cfg["maintenance"].types["incident"]
    assert set(inc.fields) == {"equipment", "fault_code", "fix"} and inc.scopes == ["workspace"]
    assert {"account", "preference", "ticket", "fact"} == set(cfg["support"].types)
    assert {"profile", "preference", "fact", "episode", "case", "skill"} <= set(cfg["assistant"].types)
    assert cfg["assistant"].scopes == ["user"] and "term" not in cfg["assistant"].types
    for name, c in cfg.items():
        assert c.extraction.instructions != resolve({}, environ={}).config.extraction.instructions or name == "assistant", name
        assert c.ingestion.max_candidates >= 1
    assert cfg["support"].extraction.remember_instructions and cfg["assistant"].extraction.remember_instructions
    assert cfg["support"].ingestion.segment_idle.total_seconds() == 3600 and cfg["support"].ingestion.max_candidates == 5
    assert cfg["maintenance"].extraction.retries == 4 and "Maintenance assistant" in cfg["maintenance"].extraction.instructions
