"""Short forms in `types:` and `areas:`: a type as a template or `builtin`, an area as `key: description`."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memhub import Memory
from memhub.config import ConfigError
from memhub.pipeline.extract import AreaRef, Candidate, Evidence
from memhub.pipeline.push import to_interactions, to_messages
from memhub.settings import resolve
from memhub.types import BUILTIN_OPTIONS, BUILTIN_TYPES, render_template
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

ROOT = Path(__file__).resolve().parents[1]
SIGHTING = "Saw {species} at {location}[ on {seen_on?}][ (count {count:int?})]"


def _types(types: dict) -> dict:
    return resolve({"project": "p", "types": types}, environ={}).config.types


def test_template_declares_fields():
    """C8"""
    t = _types({"sighting": SIGHTING})["sighting"]
    assert list(t.fields.items()) == [("species", "str"), ("location", "str"), ("seen_on", "str?"), ("count", "int?")]
    assert t.content_template == "Saw {species} at {location}[ on {seen_on}][ (count {count})]"
    assert _types({"a": "{species} at {location}"})["a"].fields == {"species": "str", "location": "str"}
    every = _types({"all": "{a} {b:int} {c:float} {d:bool} {e:list[str]} {f:list[int]?}"})["all"]
    assert every.fields == {"a": "str", "b": "int", "c": "float", "d": "bool", "e": "list[str]", "f": "list[int]?"}
    assert every.content_template == "{a} {b} {c} {d} {e} {f}"


def test_unknown_placeholder_type():
    """C9"""
    with pytest.raises(ConfigError) as err:
        _types({"x": "{n:number}"})
    for part in ("'number'", "'n'", "bool, float, int, list[int], list[str], str"):
        assert part in str(err.value)


def test_placeholder_twice():
    """C10"""
    with pytest.raises(ConfigError, match="field 'n' of type 'x' is declared twice"):
        _types({"x": "{n:int} and {n}"})
    assert _types({"x": "{n:int} and {n:int}"})["x"].fields == {"n": "int"}


@pytest.mark.parametrize("values,expected", [
    ({"seen_on": None, "count": 2}, "Saw Heron at canal (count 2)"),
    ({"seen_on": None, "count": None}, "Saw Heron at canal"),
    ({"seen_on": "2026-11-12", "count": 2}, "Saw Heron at canal on 2026-11-12 (count 2)"),
])
def test_optional_sections_render(values, expected):
    """C11"""
    template = _types({"sighting": SIGHTING})["sighting"].content_template
    assert render_template(template, {"species": "Heron", "location": "canal", **values}) == expected


def test_literal_brackets_and_plain_templates_render():
    """C11: `[[x]]` is a literal bracket; a template with no `[` renders as before (an empty field is empty text)."""
    assert render_template(_types({"x": "[[x]] {a}"})["x"].content_template, {"a": "A"}) == "[x] A"
    assert render_template("{equipment} fault {fault_code}: {fix}", {"equipment": "RTU-3", "fault_code": None, "fix": "reset"}) \
        == "RTU-3 fault : reset"


def test_written_memory_content_uses_sections():
    """C11: the content of a memory written by extraction leaves out the empty section."""
    convo = [{"role": "user", "content": "Two grey herons at the canal this morning"}]
    mid = to_interactions(to_messages(convo), workspace_id="w", user_id="u", thread_id="t", trace_id="t", now=None)[0].message_id
    cand = Candidate(type="sighting", scope="user", fields={"content": "Two grey herons at the canal", "species": "Grey Heron", "location": "the canal", "count": 2},
                     utility=5, applies_generally=True, assertion="stated", areas=[AreaRef(existing="waterbirds")],
                     evidence=[Evidence(message_id=mid, quote="Two grey herons at the canal", claim_source="user")])
    m = Memory(db="memory://", llm=FakeChatModel([extraction(cand), extraction()], unrelated_by_default=True),
               embedder=FakeEmbeddings(16), config={"project": "p", "scopes": ["user"], "types": {"sighting": SIGHTING},
                                                    "areas": {"waterbirds": "herons, ducks"}})
    m.init()
    out = m.add(convo, infer=True, workspace_id="w", user_id="u", run_id="t")
    assert [e.content for e in out.results] == ["Saw Grey Heron at the canal (count 2)"], out.dropped


def test_builtin():
    """C12"""
    names = ["fact", "preference", "profile", "episode", "case", "skill"]
    got = _types({n: "builtin" for n in names})
    for n in names:
        assert got[n].class_path == f"memhub.types:{BUILTIN_TYPES[n].__name__}", n
        for key, value in BUILTIN_OPTIONS[n].items():
            assert getattr(got[n], key) == value, (n, key)
    with pytest.raises(ConfigError, match="sighting: builtin is not a built-in type \\(use fact, preference, profile, episode, case, skill\\)"):
        _types({"sighting": "builtin"})


def test_plain_string_type():
    """C13"""
    with pytest.raises(ConfigError, match="sighting: a type is a template with \\{fields\\}, builtin, or a mapping"):
        _types({"sighting": "a bird I saw"})


def test_area_short_form():
    """C14"""
    cfg = resolve({"project": "p", "areas": {"raptors": "hawks, eagles", "other_birds": "pigeons", "shorebirds_seabirds": "gulls"}},
                  environ={}).config
    assert [(s.key, s.title, s.description) for s in cfg.areas.seeds] == [
        ("raptors", "Raptors", "hawks, eagles"), ("other_birds", "Other birds", "pigeons"),
        ("shorebirds_seabirds", "Shorebirds seabirds", "gulls")]


def test_long_forms_unchanged(monkeypatch):
    """C15: the long-form example configs resolve as at the base snapshot."""
    base = json.loads((ROOT / "tests" / "fixtures" / "examples_resolved_base.json").read_text())
    for var in ("MEMHUB_DATABASE_URL", "MAINT_DATABASE_URL", "CRM_URL", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.setenv(var, "postgresql://u@h/d")
    long_form = ROOT / "tests" / "fixtures" / "long_form"
    for name, want in base.items():
        cfg = resolve(long_form / f"{name}.yaml", environ=dict(os.environ), strict=False).config
        got = {t: c.model_dump(mode="json", by_alias=True) for t, c in cfg.types.items() if t not in ("area", "term")}
        assert got == want["types"], name
        assert [s.model_dump() for s in cfg.areas.seeds] == want["areas"] and cfg.areas.open == want["areas_open"], name
        assert cfg.extraction.instructions == want["instructions"], name
