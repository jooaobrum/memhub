"""The project's memory file (`memory: ./memory.md`): frontmatter types, areas, strict; the body is the prompt."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from memhub.config import DEFAULT_EXTRACTION_INSTRUCTIONS, ConfigError
from memhub.settings import resolve

BIRDS = """---
strict: true
types:
  sighting:     "Saw {species} at {location}[ on {seen_on?}][ (count {count:int?})]"
  species_note: "{common_name}: {note}"
areas:
  raptors:     hawks, eagles
  other_birds: pigeons, hoopoes
---

# What to remember

- A sighting has the place and the date.
- Nothing about the person.

"""
BODY = "# What to remember\n\n- A sighting has the place and the date.\n- Nothing about the person."


def _project(folder: Path, memory_text: str = BIRDS, yaml_extra: dict | None = None, name: str = "memory.md") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(memory_text)
    path = folder / "memhub.yaml"
    path.write_text(yaml.safe_dump({"project": "birds", "memory": f"./{name}", **(yaml_extra or {})}))
    return path


def test_yaml_names_the_file(tmp_path, monkeypatch):
    """C1"""
    path = _project(tmp_path / "proj")
    cfg = resolve(path, environ={}).config
    assert set(cfg.types) == {"sighting", "species_note"}
    assert cfg.strict is True and [s.key for s in cfg.areas.seeds] == ["raptors", "other_birds"]
    assert cfg.extraction.instructions == BODY
    sub = tmp_path / "proj" / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)  # the path is relative to the yaml, not to the working directory
    assert set(resolve(Path("../memhub.yaml"), environ={}).config.types) == {"sighting", "species_note"}


def test_env_and_code_name_the_file(tmp_path, monkeypatch):
    """C2"""
    monkeypatch.chdir(tmp_path)
    Path("m.md").write_text(BIRDS)
    assert set(resolve({"project": "p"}, environ={"MEMHUB_MEMORY": "./m.md"}).config.types) == {"sighting", "species_note"}
    assert set(resolve({"project": "p", "memory": "./m.md"}, environ={}).config.types) == {"sighting", "species_note"}


def test_missing_file(tmp_path):
    """C3"""
    path = tmp_path / "memhub.yaml"
    path.write_text("project: p\nmemory: ./gone.md\n")
    with pytest.raises(ConfigError, match=f"memory file not found: {(tmp_path / 'gone.md').resolve()}"):
        resolve(path, environ={})


def test_unknown_frontmatter_key(tmp_path):
    """C4"""
    path = _project(tmp_path, "---\ndb: sqlite:///x.db\ntypes:\n  fact: builtin\n---\nbody\n")
    with pytest.raises(ConfigError, match="memory.md: unknown key 'db'; allowed: types, areas, strict"):
        resolve(path, environ={})


def test_bad_yaml_frontmatter(tmp_path):
    """C37"""
    path = _project(tmp_path, "---\ntypes: [unclosed\n---\nbody\n")
    with pytest.raises(ConfigError, match="memory.md: the frontmatter is not valid yaml"):
        resolve(path, environ={})


@pytest.mark.parametrize("extra,key", [
    ({"types": {"fact": {"class": "memhub.types:Fact"}}}, "types"),
    ({"areas": {"max_per_user": 3}}, "areas"),
    ({"extraction": {"instructions": "keep it all"}}, "extraction.instructions"),
])
def test_key_in_both_places(tmp_path, extra, key):
    """C5"""
    path = _project(tmp_path, yaml_extra=extra)
    with pytest.raises(ConfigError, match=f"{key} is set in both memhub.yaml and memory.md: keep one"):
        resolve(path, environ={})


def test_instructions_in_yaml_with_an_empty_body_load(tmp_path):
    """C5: with an empty body there is nothing to clash with."""
    path = _project(tmp_path, "---\ntypes:\n  fact: builtin\n---\n\n", {"extraction": {"instructions": "keep it all"}})
    assert resolve(path, environ={}).config.extraction.instructions == "keep it all"


def test_no_frontmatter_and_empty_body(tmp_path):
    """C6"""
    path = _project(tmp_path / "a", "Only what the user says about their garden.\n")
    cfg = resolve(path, environ={}).config
    assert set(cfg.types) == {"fact", "preference", "profile", "area", "term"}
    assert cfg.extraction.instructions == "Only what the user says about their garden."
    path = _project(tmp_path / "b", "---\ntypes:\n  fact: builtin\n---\n   \n\n")
    assert resolve(path, environ={}).config.extraction.instructions == DEFAULT_EXTRACTION_INSTRUCTIONS


def test_strict_expands(tmp_path):
    """C16"""
    text = BIRDS.replace('  species_note: "{common_name}: {note}"', "  species_note: {fields: {common_name: str}, area: optional}")
    cfg = resolve(_project(tmp_path / "s", text), environ={}).config
    assert cfg.areas.open is False
    assert cfg.types["sighting"].area == "required" and cfg.types["species_note"].area == "optional"
    loose = resolve(_project(tmp_path / "l", BIRDS.replace("strict: true\n", "")), environ={}).config
    assert loose.areas.open is True and loose.types["sighting"].area is None


def test_strict_keeps_threshold(tmp_path):
    """C18"""
    assert resolve(_project(tmp_path / "a"), environ={}).config.admission.threshold == 0.5
    path = _project(tmp_path / "b", yaml_extra={"admission": {"threshold": 0.7}})
    assert resolve(path, environ={}).config.admission.threshold == 0.7
