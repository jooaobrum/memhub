"""Subjects demo (issue #20): a plant's maintenance memory on SQLite. `memhub init` in an empty folder, a
`memory.md` declaring `machine` and `model` subjects, two technicians' shift logs about two pumps that say the
same words: two memories, never merged. Search about P-101 returns P-101 first, then its model, then general
knowledge, each hit saying what it matched; a fleet search returns both pumps. Fake models, no Docker."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.pipeline.extract import Candidate, Evidence
from memhub.pipeline.push import to_interactions, to_messages
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

PLANT = """---
types:
  fact: builtin
entities:
  machine: isolate
  model: isolate
  part: tag
---
# What to remember

Faults, fixes and readings about the plant's machines.
"""

P101, P102 = {"type": "machine", "id": "plant-a/line-2/P-101"}, {"type": "machine", "id": "plant-a/line-2/P-102"}
CR32 = {"type": "model", "id": "grundfos-cr-32"}
SAID = "Pump vibrates above 7 mm/s after the night shift"


def _cand(content: str, mid: str, entities=()) -> Candidate:
    return Candidate(type="fact", scope="user", fields={"content": content, "entities": list(entities)}, utility=5,
                     applies_generally=True, assertion="stated",
                     evidence=[Evidence(message_id=mid, quote=content, claim_source="user")])


def _shift(m: Memory, llm: FakeChatModel, content: str, *, thread: str, entities, extracted=()) -> list:
    log = [{"role": "user", "content": content}, {"role": "assistant", "content": "Logged."}]
    mid = to_interactions(to_messages(log), workspace_id="plant-a", user_id="tech-42", thread_id=thread,
                          trace_id="t", now=None)[0].message_id
    llm.queue.append(extraction(_cand(content, mid, extracted)))
    return m.add(log, infer=True, thread_id=thread, entities=entities,
                 workspace_id="plant-a", user_id="tech-42", run_id=thread).results


def test_subjects_demo(tmp_path: Path, monkeypatch):
    import langchain.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "init_embeddings", lambda spec, **kw: FakeEmbeddings(16))
    folder = tmp_path / "plant"
    folder.mkdir()
    monkeypatch.chdir(folder)
    result = CliRunner().invoke(cli.app, ["init", "--db", f"sqlite:///{folder / 'm.db'}", "--embeddings", "fake:emb"])
    assert result.exit_code == 0, result.output
    (folder / "memory.md").write_text(PLANT)

    llm = FakeChatModel(unrelated_by_default=True)
    m = Memory(llm=llm)
    assert m.settings.isolated_types() == ["machine", "model"]

    [a] = _shift(m, llm, SAID, thread="shift-1", entities=[P101, CR32], extracted=[P102, {"type": "part", "id": "6205"}])
    [b] = _shift(m, llm, SAID, thread="shift-2", entities=[P102, CR32])
    [c] = _shift(m, llm, SAID, thread="shift-3", entities=[P101, CR32])
    assert (a.event, b.event) == ("ADD", "ADD") and a.id != b.id  # same words, two pumps: two memories
    assert (c.event, c.id) == ("MERGE", a.id)                      # same pump again: one memory, more evidence
    ids = dict(workspace_id="plant-a", user_id="tech-42", run_id="search")
    assert {(e["type"], e["id"]) for e in m.get(a.id, **ids)["entities"]} == {
        ("machine", P101["id"]), ("model", CR32["id"]), ("part", "6205")}  # the model's P-102 was replaced

    m.add({"content": "CR-32 pumps need the seal checked every 4000 hours", "entities": [CR32]}, **ids)
    m.add("Vibration above 7 mm/s usually means a worn bearing", **ids)

    only = m.search("pump vibration", filters={"entities": [P101]}, top_k=10, **ids)
    assert [(str(h["memory_id"]), h["matched_on"]) for h in only] == [(a.id, f"machine:{P101['id']}")]

    hits = m.search("pump vibration", filters={"entities": [P101, CR32]}, widen=True, top_k=10, **ids)
    tiers = [h["matched_on"] for h in hits]
    assert tiers == [f"machine:{P101['id']}", f"model:{CR32['id']}", f"model:{CR32['id']}", "general"]
    assert str(hits[0]["memory_id"]) == a.id  # P-101's own memory first
    # P-102's memory comes back only as knowledge about the model, and says so
    assert {str(h["memory_id"]): h["matched_on"] for h in hits}[b.id] == f"model:{CR32['id']}"
    assert hits[-1]["content"] == "Vibration above 7 mm/s usually means a worn bearing"

    fleet = m.search("pump vibration", filters={"entity_type": "machine"}, top_k=10, **ids)
    assert {str(h["memory_id"]) for h in fleet} == {a.id, b.id}
    assert [r["content"] for r in m.get_all(filters={"entities": [P102]}, **ids)] == [SAID]
