"""Subject-scoped memory (issue #20): memories about different subjects never merge, and search filters by subject.

Runs on the in-memory backend with scripted models - no Docker. A subject is an entity whose type `isolate`s
(`machine`, `model`, `patient`); a `tag` (`topic`, `part`) is a plain label."""
from __future__ import annotations

from pathlib import Path

import pytest

from memhub import Memory, ValidationError
from memhub.config import ConfigError, EntityConfig
from memhub.pipeline.reconcile import Verdict
from memhub.settings import resolve
from tests.fakes import FakeChatModel, LookupEmbeddings, ScriptedJudge, extraction, vec_at
from tests.test_push import IDS, MEM, cand, conv, msg_ids

P101, P102 = {"type": "machine", "id": "P-101"}, {"type": "machine", "id": "P-102"}
CR32 = {"type": "model", "id": "CR-32"}
TOPIC = {"type": "topic", "id": "noise"}
PART = {"type": "part", "id": "6205"}
PATIENT7, PATIENT8 = {"type": "patient", "id": "7"}, {"type": "patient", "id": "8"}


@pytest.fixture
def subjects(settings):
    return settings.model_copy(update={"entity_types": [], "entities": {
        "machine": EntityConfig(), "model": EntityConfig(), "patient": EntityConfig(require_filter=True),
        "topic": EntityConfig(isolate=False), "part": EntityConfig(isolate=False),
    }})


def _memory(settings, llm=None, embedder=None, judge=None) -> Memory:
    m = Memory(db=MEM, llm=llm or FakeChatModel(unrelated_by_default=True),
               embedder=embedder or LookupEmbeddings({}, vec_at(0.0)), judge=judge, config=settings)
    m.init()
    return m


def _keys(entities) -> set[tuple[str, str]]:
    return {(e["type"], e["id"]) for e in entities}


def _push(m, llm, content, *, thread, entities=None, cand_entities=None, **fields):
    """One pushed message saying `content`, extracted as one candidate, bound to `entities`."""
    said = conv(content, "Noted")
    llm.queue.append(extraction(cand(content, content, msg_ids(said, thread_id=thread)[0],
                                     entities=cand_entities or [], **fields)))
    return m.add(said, infer=True, thread_id=thread, entities=entities, **IDS).results


# --- S1: config ---------------------------------------------------------------------------------------------------

def _resolve(tmp_path: Path, front: str, yaml_extra: str = "") -> object:
    (tmp_path / "memory.md").write_text(f"---\n{front}\n---\nWhat to keep.\n")
    (tmp_path / "memhub.yaml").write_text(f"project: t\nworkspace_default: ws\nmemory: ./memory.md\n{yaml_extra}")
    return resolve(tmp_path / "memhub.yaml").config


def test_entities_short_forms_in_memory_md(tmp_path):
    """Criterion 1."""
    cfg = _resolve(tmp_path, "entities:\n  machine: isolate\n  topic: tag\n  patient: {isolate: true, require_filter: true}")
    assert cfg.entity_rules() == {"machine": EntityConfig(isolate=True, require_filter=False),
                                  "topic": EntityConfig(isolate=False),
                                  "patient": EntityConfig(isolate=True, require_filter=True)}
    assert cfg.isolated_types() == ["machine", "patient"] and cfg.guarded_types() == ["patient"]
    assert _resolve(tmp_path, "entities: [machine]").entity_rules() == {"machine": EntityConfig()}
    shorthand = _resolve(tmp_path, "types:\n  fact: builtin", "entity_types: [machine]\n")
    assert shorthand.entity_rules() == {"machine": EntityConfig()} and shorthand.declared_entity_types() == ["machine"]


def test_entity_type_declared_twice_is_an_error(tmp_path):
    """Criterion 2."""
    with pytest.raises(ConfigError, match=r"'machine' is declared in both entity_types and entities"):
        _resolve(tmp_path, "entities: [machine]", "entity_types: [machine]\n")


@pytest.mark.parametrize("front, message", [
    ("entities:\n  machine: strict", r"entities.machine: 'strict' is not isolate, tag"),
    ("entities:\n  machine: {broader: [model]}", r"entities.machine: unknown key 'broader'"),
])
def test_bad_entity_short_form_is_an_error(tmp_path, front, message):
    """Criterion 3."""
    with pytest.raises(ConfigError, match=message):
        _resolve(tmp_path, front)


# --- S1: reconcile ------------------------------------------------------------------------------------------------

def test_same_text_about_another_subject_is_a_new_memory(subjects):
    """Criterion 4: the same words about P-102 do not merge into P-101's memory; about P-101 again they do."""
    llm = FakeChatModel(unrelated_by_default=True)
    m = _memory(subjects, llm, judge=ScriptedJudge())
    [first] = _push(m, llm, "Pump vibrates above 7 mm/s", thread="t1", entities=[P101])
    [other] = _push(m, llm, "Pump vibrates above 7 mm/s", thread="t2", entities=[P102])
    [again] = _push(m, llm, "Pump vibrates above 7 mm/s", thread="t3", entities=[P101])
    assert first.event == "ADD" and other.event == "ADD" and other.id != first.id
    assert (again.event, again.id) == ("MERGE", first.id)


def test_near_duplicate_about_another_subject_is_a_new_memory(subjects):
    """Criterion 5: similarity 0.99 across subjects -> create; 0.95 on the same subject -> merge."""
    texts = {"Pump vibrates above 7 mm/s": vec_at(1.0), "Pump vibrates above 7.1 mm/s": vec_at(0.99),
             "Pump vibrates above 7.2 mm/s": vec_at(0.95)}
    llm = FakeChatModel(unrelated_by_default=True)
    m = _memory(subjects, llm, embedder=LookupEmbeddings(texts, vec_at(0.0)), judge=ScriptedJudge())
    [first] = _push(m, llm, "Pump vibrates above 7 mm/s", thread="t1", entities=[P101])
    [other] = _push(m, llm, "Pump vibrates above 7.1 mm/s", thread="t2", entities=[P102])
    [same] = _push(m, llm, "Pump vibrates above 7.2 mm/s", thread="t3", entities=[P101])
    assert other.event == "ADD" and other.id != first.id
    assert (same.event, same.id) == ("MERGE", first.id)


@pytest.mark.parametrize("stored, new, asked", [
    ([P101], [P101, P102], 0),
    ([P101], [], 0),
    ([P101, TOPIC], [P101], 1),  # a tag does not keep two memories apart
])
def test_judge_sees_only_rows_about_the_same_subjects(subjects, stored, new, asked):
    """Criterion 6."""
    texts = {"Bearing was replaced in May": vec_at(1.0), "Bearing noise came back": vec_at(0.5)}
    llm = FakeChatModel(unrelated_by_default=True)
    judge = ScriptedJudge([Verdict(verdict="unrelated")] * asked)
    m = _memory(subjects, llm, embedder=LookupEmbeddings(texts, vec_at(0.0)), judge=judge)
    m.add({"content": "Bearing was replaced in May", "entities": stored}, **IDS)
    [event] = _push(m, llm, "Bearing noise came back", thread="t1", cand_entities=new)
    assert event.event == "ADD" and judge.asked == asked


def test_keyed_slot_of_another_subject_is_a_conflict(subjects):
    """Criterion 7: one slot per owner; another subject's value never supersedes it."""
    llm = FakeChatModel(unrelated_by_default=True)
    m = _memory(subjects, llm, judge=ScriptedJudge())
    [first] = _push(m, llm, "Prefers answers in English", thread="t1", entities=[P101], type="preference", key="language")
    [other] = _push(m, llm, "Prefers answers in Portuguese", thread="t2", entities=[P102], type="preference", key="language")
    assert first.event == "ADD"
    assert (other.event, other.status, other.conflicts_with) == ("CONFLICT", "candidate", first.id)


# --- S1: binding on write -----------------------------------------------------------------------------------------

def test_bound_entities_replace_given_ones_of_their_type(subjects):
    """Criterion 8 (infer=False)."""
    m = _memory(subjects)
    [event] = m.add({"content": "Seal leaks", "entities": [{"type": "machine", "id": "P-9"}, TOPIC]},
                    entities=[P101], **IDS).results
    assert _keys(m.get(event.id, **IDS)["entities"]) == {("machine", "P-101"), ("topic", "noise")}


def test_bound_entities_replace_extracted_ones_of_their_type(subjects):
    """Criterion 9 (infer=True, AD-032)."""
    llm = FakeChatModel(unrelated_by_default=True)
    m = _memory(subjects, llm, judge=ScriptedJudge())
    [event] = _push(m, llm, "Same fault as the other pump", thread="t1", entities=[P101], cand_entities=[P102, PART])
    assert _keys(m.get(event.id, **IDS)["entities"]) == {("machine", "P-101"), ("part", "6205")}


@pytest.mark.parametrize("entities, message", [
    ([{"type": "vehicle", "id": "V1"}], r"unknown entity type 'vehicle'"),
    ([{"type": "machine"}], r'an entity is \{"type": str, "id": str\}'),
    ([{"type": "machine", "id": ""}], r'an entity is \{"type": str, "id": str\}'),
    ({"type": "machine", "id": "P-101"}, r"entities must be a list"),
])
def test_bad_bound_entities_are_refused_before_any_call(subjects, entities, message):
    """Criterion 10: refused before the model or the database (the store is never initialised)."""
    llm = FakeChatModel()
    m = Memory(db=MEM, llm=llm, embedder=LookupEmbeddings({}, vec_at(0.0)), config=subjects)
    with pytest.raises(ValidationError, match=message):
        m.add(conv("hi", "ok"), infer=True, entities=entities, **IDS)
    with pytest.raises(ValidationError, match=message):
        m.add("text", entities=entities, **IDS)
    assert llm.calls == 0


# --- S2: search ---------------------------------------------------------------------------------------------------

@pytest.fixture
def fleet(subjects):
    """Memories at known similarities to the query `vibration`."""
    rows = [
        ("P-102 vibration at 0.99", [P102], 0.99),
        ("general vibration advice at 0.95", [], 0.95),
        ("CR-32 vibration at 0.90", [CR32], 0.90),
        ("P-101 and CR-32 vibration at 0.60", [P101, CR32], 0.60),
        ("P-101 vibration at 0.40", [P101], 0.40),
        ("P-101 vibration at 0.31", [P101], 0.31),
        ("topic-tagged vibration at 0.50", [TOPIC], 0.50),
    ]
    texts = {"vibration": vec_at(1.0), **{t: vec_at(s) for t, _, s in rows}}
    m = _memory(subjects, embedder=LookupEmbeddings(texts, vec_at(0.0)))
    for text, entities, _ in rows:
        m.add({"content": text, "entities": entities}, **IDS)
    return m


def _hits(hits) -> list[tuple[str, str | None]]:
    return [(h["content"], h.get("matched_on")) for h in hits]


def test_search_keeps_only_the_subject(fleet):
    """Criterion 11."""
    hits = fleet.search("vibration", filters={"entities": [P101]}, top_k=10, **IDS)
    assert _hits(hits) == [("P-101 and CR-32 vibration at 0.60", "machine:P-101"),
                           ("P-101 vibration at 0.40", "machine:P-101"),
                           ("P-101 vibration at 0.31", "machine:P-101")]


def test_search_widens_in_the_order_given(fleet):
    """Criterion 12 (AD-030)."""
    hits = fleet.search("vibration", filters={"entities": [P101, CR32]}, widen=True, top_k=10, **IDS)
    assert _hits(hits) == [
        ("P-101 and CR-32 vibration at 0.60", "machine:P-101"),
        ("P-101 vibration at 0.40", "machine:P-101"),
        ("P-101 vibration at 0.31", "machine:P-101"),
        ("CR-32 vibration at 0.90", "model:CR-32"),
        ("general vibration advice at 0.95", "general"),
        ("topic-tagged vibration at 0.50", "general"),
    ]
    cut = fleet.search("vibration", filters={"entities": [P101, CR32]}, widen=True, top_k=4, **IDS)
    assert [c for c, _ in _hits(cut)][-1] == "CR-32 vibration at 0.90"


def test_search_by_entity_type(fleet):
    """Criterion 13."""
    hits = fleet.search("vibration", filters={"entity_type": "machine"}, top_k=10, **IDS)
    assert _hits(hits) == [("P-102 vibration at 0.99", "machine:P-102"),
                           ("P-101 and CR-32 vibration at 0.60", "machine:P-101"),
                           ("P-101 vibration at 0.40", "machine:P-101"),
                           ("P-101 vibration at 0.31", "machine:P-101")]


def test_search_without_filters_is_unchanged(fleet):
    """Criterion 14: every memory, nearest first, no `matched_on`."""
    hits = fleet.search("vibration", top_k=10, **IDS)
    assert [h["content"] for h in hits][:3] == ["P-102 vibration at 0.99", "general vibration advice at 0.95",
                                                "CR-32 vibration at 0.90"]
    assert len(hits) == 7 and all("matched_on" not in h for h in hits)


def test_require_filter_hides_a_guarded_type_from_every_unfiltered_search(subjects):
    """Criterion 15 (AD-033): via Memory.search and via the service every other surface calls."""
    texts = {"allergies": vec_at(1.0), "Patient 7 is allergic to penicillin": vec_at(0.9),
             "Patient 8 is allergic to latex": vec_at(0.8), "Penicillin allergy is common": vec_at(0.7)}
    m = _memory(subjects, embedder=LookupEmbeddings(texts, vec_at(0.0)))
    for text, entities in [("Patient 7 is allergic to penicillin", [PATIENT7]),
                           ("Patient 8 is allergic to latex", [PATIENT8]), ("Penicillin allergy is common", [])]:
        m.add({"content": text, "entities": entities}, **IDS)

    def contents(hits):
        return [h["content"] for h in hits]

    assert contents(m.search("allergies", **IDS)) == ["Penicillin allergy is common"]
    assert contents(m.service.search(_actor(), "allergies",
                                     workspace_id=IDS["workspace_id"], user_id=IDS["user_id"])) == [
        "Penicillin allergy is common"]
    assert contents(m.search("allergies", filters={"entities": [PATIENT7]}, **IDS)) == [
        "Patient 7 is allergic to penicillin"]
    assert contents(m.search("allergies", filters={"entity_type": "patient"}, **IDS)) == [
        "Patient 7 is allergic to penicillin", "Patient 8 is allergic to latex"]
    assert contents(m.search("allergies", filters={"entities": [PATIENT7]}, widen=True, **IDS)) == [
        "Patient 7 is allergic to penicillin", "Penicillin allergy is common"]
    tool = m.tools(**IDS)
    assert [h["content"] for h in tool.search_memory("allergies")] == ["Penicillin allergy is common"]


def _actor():
    from memhub.service import Actor

    return Actor(id=IDS["user_id"])


def test_get_all_filters_by_subject(fleet):
    """Criterion 16: newest first."""
    rows = fleet.get_all(filters={"entities": [P101]}, **IDS)
    assert [r["content"] for r in rows] == ["P-101 vibration at 0.31", "P-101 vibration at 0.40",
                                            "P-101 and CR-32 vibration at 0.60"]
    assert len(fleet.get_all(filters={"entity_type": "model"}, **IDS)) == 2


@pytest.mark.parametrize("kwargs, message", [
    (dict(filters={"subject": [P101]}), r"unknown filter 'subject'"),
    (dict(widen=True), r"widen=True needs filters"),
    (dict(filters={"entity_type": "machine"}, widen=True), r"widen=True needs filters"),
    (dict(filters={"types": ["fact"]}, types=["fact"]), r"types is given both"),
    (dict(filters={"entity_type": "vehicle"}), r"unknown entity type 'vehicle'"),
    (dict(filters={"entities": [{"type": "machine"}]}), r'an entity is'),
])
def test_bad_filters_are_refused(fleet, kwargs, message):
    """Criterion 17."""
    with pytest.raises(ValidationError, match=message):
        fleet.search("vibration", **kwargs, **IDS)


def test_filters_types_is_the_types_parameter(fleet):
    hits = fleet.search("vibration", filters={"types": ["fact"], "entities": [P101]}, top_k=10, **IDS)
    assert [h["matched_on"] for h in hits] == ["machine:P-101"] * 3
    assert fleet.search("vibration", filters={"types": ["preference"]}, **IDS) == []
