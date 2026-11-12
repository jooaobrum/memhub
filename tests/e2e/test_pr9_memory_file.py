"""PR 9 demo: a birdwatching club's memory in one `memory.md`. `memhub init` in an empty folder, the birds file in
place of the starter, one mixed conversation pushed: only bird memories are written, everything else comes back
in `dropped` with its reason, and the database is the same as with no memory file. Fake models, SQLite, no Docker."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.pipeline.extract import AreaRef, Candidate, Evidence
from memhub.pipeline.push import to_interactions, to_messages
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction

BIRDS = """---
strict: true
types:
  sighting:     "Saw {species} at {location}[ on {seen_on?}][ (count {count:int?})]"
  species_note: "{common_name}: {note}"
areas:
  raptors:     hawks, eagles, falcons, kites, vultures, ospreys
  owls:        owls of all kinds
  waterbirds:  ducks, geese, swans, herons, egrets, grebes, rails
  songbirds:   warblers, finches, thrushes, tits, larks
  other_birds: pigeons, woodpeckers, hoopoes, kingfishers, cuckoos
---
# What to remember

Only bird species knowledge and the user's sightings.

- A sighting has the place and an absolute date.
- Never store anything about the person: job, family, travel, devices, opinions.
"""

CONVERSATION = [
    {"role": "user", "content": "Morning! I'm a nurse, just finished a night shift."},
    {"role": "user", "content": "I saw two Eurasian Hoopoes at the Camargue salt flats on 2026-11-12."},
    {"role": "user", "content": "The Hoopoe raises its crest when it lands."},
    {"role": "user", "content": "A Grey Heron stood still in the canal before striking a fish."},
    {"role": "user", "content": "Also a big brown bird flew over the parking lot, no idea what it was."},
]
IDS = dict(workspace_id="birdclub", user_id="lea", run_id="chat-1")


def _cand(type_, fields, quote, mid, area=None):
    return Candidate(type=type_, scope="user", fields=fields, utility=5, applies_generally=True, assertion="stated",
                     areas=[AreaRef(existing=area)] if area else [],
                     evidence=[Evidence(message_id=mid, quote=quote, claim_source="user")])


def _init(folder: Path, monkeypatch) -> None:
    folder.mkdir()
    monkeypatch.chdir(folder)
    result = CliRunner().invoke(cli.app, ["init", "--db", f"sqlite:///{folder / 'm.db'}", "--embeddings", "fake:emb"])
    assert result.exit_code == 0, result.output


def _schema(db: Path) -> set[tuple]:
    with sqlite3.connect(db) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        prefix = db.parent.name + "_"  # the table prefix is the folder's name
        return {(t.removeprefix(prefix), c[1], c[2]) for t in tables for c in conn.execute(f"PRAGMA table_info('{t}')")}


def _push(monkeypatch, *cands):
    llm = FakeChatModel([extraction(*cands), extraction()], unrelated_by_default=True)
    m = Memory(llm=llm)
    return m, m.add(CONVERSATION, infer=True, **IDS)


def test_birds_demo(tmp_path, monkeypatch):
    """C34"""
    import langchain.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "init_embeddings", lambda spec, **kw: FakeEmbeddings(16))
    _init(tmp_path / "plain", monkeypatch)  # the starter memory.md: the reference database
    plain = _schema(tmp_path / "plain" / "m.db")

    _init(tmp_path / "birds", monkeypatch)
    Path("memory.md").write_text(BIRDS)
    with open("memhub.yaml", "a") as f:  # four candidates in one segment: above the default cap of 3
        f.write("ingestion: {max_candidates: 5}\n")
    ids = [i.message_id for i in to_interactions(to_messages(CONVERSATION), workspace_id="birdclub", user_id="lea",
                                                 thread_id="chat-1", trace_id="chat-1", now=None)]
    m, out = _push(
        monkeypatch,
        _cand("sighting", {"content": "Two hoopoes", "species": "Eurasian Hoopoe", "location": "Camargue salt flats",
                           "seen_on": "2026-11-12", "count": 2},
              "I saw two Eurasian Hoopoes at the Camargue salt flats on 2026-11-12", ids[1], "other_birds"),
        _cand("species_note", {"content": "Hoopoe crest", "common_name": "Eurasian Hoopoe", "note": "raises its crest when it lands"},
              "The Hoopoe raises its crest when it lands", ids[2], "other_birds"),
        _cand("sighting", {"content": "Heron", "species": "Grey Heron", "location": "the canal"},
              "A Grey Heron stood still in the canal", ids[3], "waterbirds"),
        _cand("sighting", {"content": "Brown bird", "species": "unknown brown bird", "location": "the parking lot"},
              "a big brown bird flew over the parking lot", ids[4]),  # no bird group: off the domain
    )
    assert sorted(e.content for e in out.results) == [
        "Eurasian Hoopoe: raises its crest when it lands",
        "Saw Eurasian Hoopoe at Camargue salt flats on 2026-11-12 (count 2)",
        "Saw Grey Heron at the canal",
    ]
    assert {e.type for e in out.results} == {"sighting", "species_note"}
    assert [d.reason for d in out.dropped] == ["no_area"]  # every candidate not written comes back with its reason
    assert _schema(tmp_path / "birds" / "m.db") == plain  # N5: no table or column added


def test_strict_drops_off_domain(tmp_path, monkeypatch):
    """C17: under `strict: true` a candidate with no area is dropped as `no_area`."""
    import langchain.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "init_embeddings", lambda spec, **kw: FakeEmbeddings(16))
    _init(tmp_path / "birds", monkeypatch)
    Path("memory.md").write_text(BIRDS)
    ids = [i.message_id for i in to_interactions(to_messages(CONVERSATION), workspace_id="birdclub", user_id="lea",
                                                 thread_id="chat-1", trace_id="chat-1", now=None)]
    _, out = _push(monkeypatch, _cand(
        "sighting", {"content": "Brown bird", "species": "unknown brown bird", "location": "the parking lot"},
        "a big brown bird flew over the parking lot", ids[4]))
    assert out.results == [] and [d.reason for d in out.dropped] == ["no_area"]
