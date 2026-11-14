"""The storage contract: one set of tests every backend must pass.

A backend's test module imports these and provides the fixtures:

    from memhub.storage.contract import *  # noqa: F401,F403

    @pytest.fixture
    def make_backend():          # schema -> a new, empty Backend, not initialized
        ...

    @pytest.fixture
    def unique_error():          # what a second active row for one memory / one keyed slot raises
        ...

and `settings` / `registry` (memhub's `tests/conftest.py` has both). `backend` below is `make_backend(None)`
after `init(embedding_model=MODEL, dims=DIMS)`. A test for a capability the backend lacks asserts the
`NotImplementedError` instead of skipping (R11).
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from memhub.storage.base import Backend, Capability
from memhub.storage.rules import (
    MEMORY_COLUMNS, RUN_COLUMNS, AllOf, AnyOf, AreaIn, ContentContains, EntityFilter, EntityIs, EntityTypeIn, NotFound,
    NotOf, TimeRange, TypeIn, area_link, as_vector_list,
)

DIMS = 16
MODEL = "contract-embed"
NOW = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


# --- fixtures and helpers -----------------------------------------------------------------------------------


@pytest.fixture
def backend(make_backend) -> Backend:
    b = make_backend(None)
    b.init(embedding_model=MODEL, dims=DIMS)
    return b


def vec(similarity: float, dims: int = DIMS) -> list[float]:
    """A unit vector whose cosine similarity to `vec(1.0)` is `similarity`."""
    v = [0.0] * dims
    v[0], v[1] = similarity, math.sqrt(max(0.0, 1 - similarity**2))
    return v


def add(uow, content: str = "c", embedding: list[float] | None = None, **kw: Any) -> dict:
    args = dict(
        type="fact", schema_version=1, scope="user", workspace_id="ws", user_id="alice", content=content,
        payload={"content": content}, entities=[], embedding=embedding if embedding is not None else vec(1.0),
        evidence=[], status="active", verified=False, created_by="contract",
    )
    return uow.memories.add_memory(**{**args, **kw})


def run_row(uow, **kw: Any) -> dict:
    args = dict(
        run_id=uuid.uuid4(), source="jsonl", thread_id="t1", workspace_id="ws", user_id="alice",
        first_message_id="m1", last_message_id="m2", last_message_at=NOW,
    )
    return uow.runs.upsert_run(**{**args, **kw})


def contents(rows: list[dict]) -> list[str]:
    return [r["content"] for r in rows]


# --- transactions -------------------------------------------------------------------------------------------


def test_commit_is_visible_and_an_exception_rolls_back(backend):
    with backend.unit_of_work() as uow:
        kept = add(uow, "kept")
    with backend.unit_of_work() as uow:
        assert uow.memories.get_by_id(kept["id"])["content"] == "kept"

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with backend.unit_of_work() as uow:
            lost = add(uow, "lost")
            run_row(uow, thread_id="t-lost")
            raise Boom()
    with backend.unit_of_work() as uow:
        assert uow.memories.get_by_id(lost["id"]) is None
        assert uow.runs.get_watermark(source="jsonl", thread_id="t-lost") is None
        assert uow.memories.get_by_id(kept["id"]) is not None


# --- row shape ----------------------------------------------------------------------------------------------


def test_rows_have_the_reference_keys_and_types(backend):
    with backend.unit_of_work() as uow:
        row = add(uow, "shape")
        run = run_row(uow)
        assert set(row) == set(MEMORY_COLUMNS)
        assert set(uow.memories.get_by_id(row["id"])) == set(MEMORY_COLUMNS)
        assert set(uow.memories.list_memories()[0]) == {*MEMORY_COLUMNS, "stale"}
        assert set(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice")[0]) == {*MEMORY_COLUMNS, "similarity"}
        assert set(uow.vectors.top_similar(
            type="fact", scope="user", workspace_id="ws", user_id="alice", query_embedding=vec(1.0),
        )[0]) == {*MEMORY_COLUMNS, "similarity"}
        assert set(run) == set(RUN_COLUMNS)
        assert set(uow.runs.get_watermark(source="jsonl", thread_id="t1")) == set(RUN_COLUMNS)
        assert uow.meta.get_meta("embedding") == {"model": MODEL, "dims": DIMS}
    assert isinstance(row["id"], uuid.UUID) and isinstance(row["memory_id"], uuid.UUID)
    for col in ("created_at", "observed_at"):
        assert isinstance(row[col], datetime) and row[col].tzinfo is not None
    assert isinstance(run["id"], uuid.UUID) and isinstance(run["run_id"], uuid.UUID)
    assert run["processed_at"].tzinfo is not None
    assert (row["version"], row["seen_count"], row["assertion"], row["links"]) == (1, 1, "stated", [])


def test_naive_datetimes_are_stored_as_utc(backend):
    naive = datetime(2026, 1, 1, 12)
    with backend.unit_of_work() as uow:
        row = add(uow, "naive", valid_until=naive, valid_from=naive, observed_at=naive)
        run = run_row(uow, last_message_at=naive)
    aware = naive.replace(tzinfo=timezone.utc)
    for col in ("valid_until", "valid_from", "observed_at"):
        assert row[col] == aware and row[col].utcoffset() == timedelta(0), col
    assert run["last_message_at"] == aware and run["last_message_at"].utcoffset() == timedelta(0)


def test_returned_rows_are_copies(backend):
    with backend.unit_of_work() as uow:
        row = add(uow, "original", evidence=[{"message_id": "m1"}])
        row["content"] = "x"
        row["evidence"].append({"message_id": "m2"})
        again = uow.memories.get_by_id(row["id"])
        again["payload"]["content"] = "y"
    with backend.unit_of_work() as uow:
        stored = uow.memories.get_by_id(row["id"])
    assert (stored["content"], stored["evidence"], stored["payload"]) == ("original", [{"message_id": "m1"}], {"content": "original"})


# --- constraints ------------------------------------------------------------------------------------------------


def test_one_active_row_per_memory_and_per_keyed_slot(backend, unique_error):
    with backend.unit_of_work() as uow:
        first = add(uow, "first")
    with pytest.raises(unique_error):
        with backend.unit_of_work() as uow:
            add(uow, "second", memory_id=first["memory_id"], version=2)
    keyed = dict(type="preference", payload={"content": "tea", "key": "drink"})
    with backend.unit_of_work() as uow:
        add(uow, "tea", **keyed)
        add(uow, "bob's tea", user_id="bob", **keyed)  # another owner: fine
        add(uow, "old tea", status="superseded", **keyed)  # history: fine
    with pytest.raises(unique_error):
        with backend.unit_of_work() as uow:
            add(uow, "coffee", type="preference", payload={"content": "coffee", "key": "drink"})
    with backend.unit_of_work() as uow:
        add(uow, "tea in ws2", workspace_id="ws2", **keyed)  # another workspace: fine
        add(uow, "the team's tea", scope="workspace", user_id=None, **keyed)  # the workspace's own slot: fine
        add(uow, "fact with the key", type="fact", payload={"content": "x", "key": "drink"})  # another type: fine
    with pytest.raises(unique_error):
        with backend.unit_of_work() as uow:
            add(uow, "the team's coffee", scope="workspace", user_id=None, **keyed)
    with backend.unit_of_work() as uow:
        assert [r["version"] for r in uow.memories.history(first["memory_id"])] == [1]
        assert uow.memories.count_active(type="preference", scope="user", workspace_id="ws", user_id="alice") == 1


def test_keyed_slot_compares_keys_as_json_text(backend, unique_error):
    """`payload->>'key'`: the string "1" and the number 1 are one slot; true and 1 are two."""
    with backend.unit_of_work() as uow:
        add(uow, "string one", type="preference", payload={"content": "a", "key": "1"})
    with pytest.raises(unique_error):
        with backend.unit_of_work() as uow:
            add(uow, "number one", type="preference", payload={"content": "b", "key": 1})
    with backend.unit_of_work() as uow:
        add(uow, "true", type="profile", payload={"content": "c", "key": True})
        add(uow, "number one", type="profile", payload={"content": "d", "key": 1})
        assert uow.memories.count_active(type="profile", scope="user", workspace_id="ws", user_id="alice") == 2


# --- capabilities -----------------------------------------------------------------------------------------------


def test_schemas_capability(make_backend):
    probe = make_backend(None)
    schema = f"contract_{uuid.uuid4().hex[:8]}"
    if Capability.SCHEMAS in probe.capabilities:
        b = make_backend(schema)
        b.init(embedding_model=MODEL, dims=DIMS)
        with b.unit_of_work() as uow:
            row = add(uow, "in a schema")
        with b.unit_of_work() as uow:
            assert uow.memories.get_by_id(row["id"])["content"] == "in a schema"
    else:
        with pytest.raises(NotImplementedError) as exc:
            make_backend(schema)
        assert str(exc.value) == f"schemas is not available on {probe.name}: remove database_schema from the config, or use Postgres"


def test_parallel_ingest_capability(backend):
    if Capability.PARALLEL_INGEST not in backend.capabilities:
        with pytest.raises(NotImplementedError, match=rf"^parallel_ingest is not available on {backend.name}: "):
            with backend.thread_lock("s", "t1"):
                pass
        return
    with backend.thread_lock("s", "t1") as first:
        assert first is True
        with backend.thread_lock("s", "t1") as second:
            assert second is False
        with backend.thread_lock("s", "t2") as other:
            assert other is True
    with backend.thread_lock("s", "t1") as again:
        assert again is True


def test_vector_search_capability(backend):
    with backend.unit_of_work() as uow:
        add(uow, "near", vec(0.9))
        if Capability.VECTOR_SEARCH not in backend.capabilities:
            for name in ("search", "top_similar", "nearest_area"):
                with pytest.raises(NotImplementedError, match=rf"^vector_search is not available on {backend.name}: "):
                    getattr(uow.vectors, name)
            return
        assert contents(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice")) == ["near"]


def test_json_lookup_capability(backend):
    area = uuid.uuid4()
    with backend.unit_of_work() as uow:
        row = add(uow, "linked", links=[area_link(area)])
        if Capability.JSON_LOOKUP not in backend.capabilities:
            for name in ("linked_rows", "move_area_links", "add_link"):
                with pytest.raises(NotImplementedError, match=rf"^json_lookup is not available on {backend.name}: "):
                    getattr(uow.memories, name)
            return
        assert contents(uow.memories.linked_rows(area)) == ["linked"] and row["links"] == [area_link(area)]


def test_transactions_capability(backend):
    assert Capability.TRANSACTIONS in backend.capabilities  # every memhub call relies on it: no backend may lack it


# --- vectors ----------------------------------------------------------------------------------------------------


def test_search_ranks_by_cosine_similarity(backend):
    with backend.unit_of_work() as uow:
        for sim in (0.50, 0.95, 0.86):
            add(uow, f"at {sim}", vec(sim))
    with backend.unit_of_work() as uow:
        rows = uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice", k=3)
    assert contents(rows) == ["at 0.95", "at 0.86", "at 0.5"]
    for row, sim in zip(rows, (0.95, 0.86, 0.50)):
        assert abs(row["similarity"] - sim) < 1e-6


def test_search_filters_owner_status_scope_and_workspace(backend):
    with backend.unit_of_work() as uow:
        add(uow, "mine", vec(0.90))
        add(uow, "shared", vec(0.80), scope="workspace", user_id=None)
        add(uow, "bob's", vec(0.99), user_id="bob")
        add(uow, "other workspace", vec(0.99), workspace_id="ws2")
        add(uow, "pending", vec(0.99), status="candidate")
        add(uow, "archived", vec(0.99), status="archived")
    with backend.unit_of_work() as uow:
        assert contents(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice")) == ["mine", "shared"]
        assert contents(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id=None)) == ["shared"]


def test_search_filters_stale_area_type_and_k(backend):
    with backend.unit_of_work() as uow:
        add(uow, "fresh", vec(0.90))
        add(uow, "expired", vec(0.99), valid_until=NOW - timedelta(days=1))
        add(uow, "an area", vec(0.98), type="area", payload={"content": "an area", "title": "A"})
        add(uow, "an episode", vec(0.70), type="episode")
    with backend.unit_of_work() as uow:
        def search(**kw):
            return contents(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice", now=NOW, **kw))

        assert search() == ["fresh", "an episode"]
        assert search(include_stale=True) == ["expired", "fresh", "an episode"]
        assert search(type="area") == ["an area"]
        assert search(type="episode") == ["an episode"]
        assert search(k=1) == ["fresh"]


def test_search_filters_stale_boundary_is_strictly_before_now(backend):
    with backend.unit_of_work() as uow:
        add(uow, "ends now", vec(0.90), valid_until=NOW)
        add(uow, "ended a second ago", vec(0.95), valid_until=NOW - timedelta(seconds=1))
    with backend.unit_of_work() as uow:
        assert contents(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice", now=NOW)) == ["ends now"]
        stale = {r["content"]: r["stale"] for r in uow.memories.list_memories(now=NOW)}
        assert stale == {"ends now": False, "ended a second ago": True}


def test_search_and_top_similar_filter_by_entities(backend):
    """Criterion 18: the `EntityFilter` keeps the same rows on every backend, before `k` is applied."""
    p101, p102, cr32 = {"type": "machine", "id": "P-101"}, {"type": "machine", "id": "P-102"}, {"type": "model", "id": "CR-32"}
    with backend.unit_of_work() as uow:
        add(uow, "p102", vec(0.99), entities=[p102])
        add(uow, "p101 and cr32", vec(0.95), entities=[p101, cr32])
        add(uow, "cr32 only", vec(0.90), entities=[cr32])
        add(uow, "tagged topic", vec(0.85), entities=[{"type": "topic", "id": "noise"}])
        add(uow, "untagged", vec(0.80))
        add(uow, "p101 low", vec(0.31), entities=[p101])
    with backend.unit_of_work() as uow:
        def search(flt, **kw):
            return contents(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice",
                                               entities=flt, **kw))

        assert search(EntityFilter(any_of=(("machine", "P-101"),))) == ["p101 and cr32", "p101 low"]
        assert search(EntityFilter(any_of=(("machine", "P-101"),)), k=1) == ["p101 and cr32"]
        assert search(EntityFilter(any_of=(("machine", "P-102"), ("model", "CR-32")))) == ["p102", "p101 and cr32", "cr32 only"]
        assert search(EntityFilter(of_type="machine")) == ["p102", "p101 and cr32", "p101 low"]
        assert search(EntityFilter(none_of_types=("machine", "model"))) == ["tagged topic", "untagged"]
        assert search(EntityFilter(of_type="model", none_of_types=("machine",))) == ["cr32 only"]
        assert search(EntityFilter(any_of=(("machine", "P-9"),))) == []
        assert len(search(None)) == 6
        similar = uow.vectors.top_similar(type="fact", scope="user", workspace_id="ws", user_id="alice",
                                          query_embedding=vec(1.0), limit=1,
                                          entities=EntityFilter(any_of=(("machine", "P-101"),)))
        assert contents(similar) == ["p101 and cr32"]


def test_search_boosts_area_text_and_entity_capped_at_one(backend):
    area_id = uuid.uuid4()
    with backend.unit_of_work() as uow:
        add(uow, "plain", vec(0.90))
        add(uow, "in the area", vec(0.88), links=[area_link(area_id)])
        add(uow, "says espresso", vec(0.87))
        add(uow, "names the press", vec(0.86), entities=[{"type": "machine", "id": "P1"}])
        add(uow, "top", vec(1.0), links=[area_link(area_id)])
    with backend.unit_of_work() as uow:
        rows = uow.vectors.search(
            query_embedding=vec(1.0), workspace_id="ws", user_id="alice", k=5, area_boost=area_id,
            text_boost=["Espresso"], entity_boost=[{"type": "machine", "id": "P1"}],
        )
    got = {r["content"]: r["similarity"] for r in rows}
    assert contents(rows) == ["top", "in the area", "says espresso", "names the press", "plain"]
    assert abs(got["in the area"] - 0.93) < 1e-6 and abs(got["says espresso"] - 0.92) < 1e-6
    assert abs(got["names the press"] - 0.91) < 1e-6 and got["top"] == 1.0


def test_search_boosts_rows_ranked_below_k(backend):
    """Boosts apply to more rows than `k`: a linked row ranked below `k` on similarity alone can still come first."""
    area_id = uuid.uuid4()
    with backend.unit_of_work() as uow:
        for i in range(60):
            add(uow, f"plain {i}", vec(0.50 + i * 0.005))  # 0.500 .. 0.795
        add(uow, "in the area", vec(0.76), links=[area_link(area_id)])  # ninth on similarity alone
    with backend.unit_of_work() as uow:
        rows = uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice", k=3, area_boost=area_id)
    assert contents(rows) == ["in the area", "plain 59", "plain 58"]
    assert abs(rows[0]["similarity"] - 0.81) < 1e-6


def test_top_similar_and_nearest_area(backend):
    with backend.unit_of_work() as uow:
        add(uow, "candidate", vec(0.95), status="candidate")
        add(uow, "active", vec(0.90))
        add(uow, "rejected", vec(0.99), status="rejected")
        add(uow, "other type", vec(0.99), type="episode")
        add(uow, "far area", vec(0.30), type="area", payload={"content": "far", "title": "Far"})
        add(uow, "near area", vec(0.80), type="area", payload={"content": "near", "title": "Near"})
    with backend.unit_of_work() as uow:
        rows = uow.vectors.top_similar(type="fact", scope="user", workspace_id="ws", user_id="alice", query_embedding=vec(1.0))
        assert contents(rows) == ["candidate", "active"]
        near = uow.vectors.nearest_area(scope="user", workspace_id="ws", user_id="alice", query_embedding=vec(1.0))
        assert near["content"] == "near area" and abs(near["similarity"] - 0.80) < 1e-6
        assert uow.vectors.nearest_area(scope="user", workspace_id="ws", user_id="bob", query_embedding=vec(1.0)) is None


# --- lifecycle --------------------------------------------------------------------------------------------------


def test_edit_supersedes_and_history_keeps_every_version(backend):
    with backend.unit_of_work() as uow:
        v1 = add(uow, "v1", valid_until=NOW)
        v2 = uow.memories.edit_memory(
            v1["memory_id"], content="v2", payload={"content": "v2"}, entities=[], embedding=vec(0.9),
            verified=True, created_by="bob",
        )
        cand = uow.memories.edit_memory(
            v1["memory_id"], content="v3?", payload={"content": "v3?"}, entities=[], embedding=vec(0.9),
            verified=False, created_by="bob", as_candidate=True,
        )
    assert (v2["version"], v2["status"], v2["valid_until"], v2["verified"]) == (2, "active", NOW, True)
    assert (cand["version"], cand["status"]) == (3, "candidate")
    with backend.unit_of_work() as uow:
        assert [(r["version"], r["status"]) for r in uow.memories.history(v1["memory_id"])] == [
            (1, "superseded"), (2, "active"), (3, "candidate")]
        with pytest.raises(NotFound):
            uow.memories.edit_memory(uuid.uuid4(), content="x", payload={}, entities=[], embedding=None, verified=False, created_by="x")


def test_review_approve_reject_archive(backend):
    with backend.unit_of_work() as uow:
        cand = add(uow, "pending", status="candidate")
        other = add(uow, "to reject", status="candidate")
        approved = uow.memories.approve(cand["id"], reviewed_by="admin", note="ok")
        rejected = uow.memories.reject(other["id"], reviewed_by="admin", note="no")
        archived = uow.memories.archive(approved["memory_id"])
    assert (approved["status"], approved["verified"], approved["reviewed_by"], approved["review_note"]) == ("active", True, "admin", "ok")
    assert approved["reviewed_at"] is not None
    assert (rejected["status"], rejected["review_note"]) == ("rejected", "no")
    assert archived["status"] == "archived"
    with backend.unit_of_work() as uow:
        assert uow.memories.queue() == []


def test_conflict_resolutions(backend):
    with backend.unit_of_work() as uow:
        old = add(uow, "old")
        keep_old = add(uow, "keep old", status="candidate", conflicts_with=old["memory_id"])
        replace = add(uow, "replace", status="candidate", conflicts_with=old["memory_id"])
        queue = uow.memories.queue(workspace_id="ws")
        assert [r["content"] for r in queue] == ["keep old", "replace"]
        assert uow.memories.approve(keep_old["id"], reviewed_by="a", resolve="keep_old")["status"] == "rejected"
        assert uow.memories.get_active(old["memory_id"])["content"] == "old"
        assert uow.memories.approve(replace["id"], reviewed_by="a", resolve="replace")["status"] == "active"
        assert uow.memories.get_by_id(old["id"])["status"] == "archived"

        slot = add(uow, "tea", type="preference", payload={"content": "tea", "key": "drink"}, evidence=[{"message_id": "m1"}])
        both = add(uow, "coffee", type="preference", payload={"content": "coffee", "key": "drink"}, status="candidate",
                   conflicts_with=slot["memory_id"], evidence=[{"message_id": "m2"}])
        merged = {"content": "tea; coffee", "payload": {"content": "tea; coffee", "key": "drink"}, "embedding": vec(0.9)}
        new = uow.memories.approve(both["id"], reviewed_by="a", resolve="keep_both", merged=merged)
        assert (new["memory_id"], new["version"], new["content"]) == (slot["memory_id"], 2, "tea; coffee")
        assert [e["message_id"] for e in new["evidence"]] == ["m1", "m2"]
        assert uow.memories.get_by_id(both["id"])["status"] == "archived"


def _edit(content: str, similarity: float = 0.5) -> dict:
    return {"content": content, "payload": {"content": content}, "entities": [{"type": "machine", "id": "P-101"}],
            "embedding": vec(similarity)}


def test_approve_with_edit_writes_the_next_version(backend):
    """An edit on review keeps the proposal as an archived row and writes the approved text as the next version."""
    with backend.unit_of_work() as uow:
        cand = add(uow, "Login uses Auth0", status="candidate", scope="workspace", workspace_id="ws2", user_id=None,
                   evidence=[{"message_id": "m1", "quote": "we use Auth0"}], run_id="run-7", agent_id="agent-3",
                   created_by="agent")
        new = uow.memories.approve(cand["id"], reviewed_by="rev", edit=_edit("Login uses Okta"))
        old = uow.memories.get_by_id(cand["id"])
        history = uow.memories.history(cand["memory_id"])
    assert (old["status"], old["review_note"], old["reviewed_by"], old["content"]) == (
        "archived", "edited on review", "rev", "Login uses Auth0")
    assert (new["memory_id"], new["version"], new["status"], new["verified"], new["reviewed_by"]) == (
        cand["memory_id"], 2, "active", True, "rev")
    assert new["reviewed_at"] is not None and new["review_note"] is None
    assert (new["content"], new["payload"], new["entities"]) == (
        "Login uses Okta", {"content": "Login uses Okta"}, [{"type": "machine", "id": "P-101"}])
    assert as_vector_list(new["embedding"]) == pytest.approx(vec(0.5))
    assert new["evidence"] == [{"message_id": "m1", "quote": "we use Auth0"}]
    assert (new["run_id"], new["agent_id"], new["type"], new["scope"], new["workspace_id"], new["user_id"]) == (
        "run-7", "agent-3", "fact", "workspace", "ws2", None)
    assert [(r["version"], r["status"]) for r in history] == [(1, "archived"), (2, "active")]


def test_approve_with_edit_supersedes_the_active_version(backend):
    with backend.unit_of_work() as uow:
        v1 = add(uow, "v1", status="superseded")
        v2 = add(uow, "v2", memory_id=v1["memory_id"], version=2)
        v3 = add(uow, "v3", status="candidate", memory_id=v1["memory_id"], version=3)
        new = uow.memories.approve(v3["id"], reviewed_by="a", edit=_edit("v3 edited"))
        history = uow.memories.history(v1["memory_id"])
    assert (new["version"], new["content"]) == (4, "v3 edited")
    assert [(r["version"], r["status"]) for r in history] == [
        (1, "superseded"), (2, "superseded"), (3, "archived"), (4, "active")]
    assert sum(r["status"] == "active" for r in history) == 1 and v2["status"] == "active"


def test_plain_candidate_version_supersedes_its_active_sibling(backend):
    with backend.unit_of_work() as uow:
        v1 = add(uow, "v1")
        v2 = add(uow, "v2", status="candidate", memory_id=v1["memory_id"], version=2)
        uow.memories.approve(v2["id"], reviewed_by="a")
        assert [(r["version"], r["status"]) for r in uow.memories.history(v1["memory_id"])] == [(1, "superseded"), (2, "active")]


def test_merge_evidence_dedups_counts_threads_and_merges_dates(backend):
    seen = (NOW - timedelta(days=2)).isoformat()
    later = (NOW - timedelta(days=1)).isoformat()
    with backend.unit_of_work() as uow:
        row = add(uow, "x", evidence=[{"message_id": "m1", "thread_id": "t1", "observed_at": seen}], valid_until=NOW)
        same = uow.memories.merge_evidence(row["memory_id"], new_evidence=[{"message_id": "m1", "thread_id": "t1"}], thread_id="t1")
        assert same["seen_count"] == 1 and len(same["evidence"]) == 1
        one = uow.memories.merge_evidence(
            row["memory_id"], new_evidence=[{"message_id": "m2", "thread_id": "t1", "observed_at": later}], thread_id="t1",
            valid_until=NOW + timedelta(days=3),
        )
        assert (one["seen_count"], one["observed_at"], one["valid_until"]) == (1, NOW - timedelta(days=1), NOW + timedelta(days=3))
        two = uow.memories.merge_evidence_row(
            row["id"], new_evidence=[{"message_id": "m3", "thread_id": "t2"}], thread_id="t2", valid_until=None,
        )
        assert (two["seen_count"], two["valid_until"], len(two["evidence"])) == (2, None, 3)
        cand = add(uow, "pending", status="candidate")
        uow.memories.amend_row(cand["id"], content="amended", payload={"content": "amended"}, embedding=vec(0.5))
        assert uow.memories.get_by_id(cand["id"])["content"] == "amended"


def test_list_count_delete(backend):
    with backend.unit_of_work() as uow:
        a = add(uow, "a", valid_until=NOW - timedelta(hours=1))
        add(uow, "b", type="episode")
        add(uow, "c", user_id="bob", status="candidate")
        run_row(uow, user_id="alice")
        run_row(uow, user_id="bob", thread_id="t2")
    with backend.unit_of_work() as uow:
        assert sorted(contents(uow.memories.list_memories(user_id="alice"))) == ["a", "b"]
        assert contents(uow.memories.list_memories(stale=True, now=NOW)) == ["a"]
        assert [r["stale"] for r in uow.memories.list_memories(type="fact", user_id="alice", now=NOW)] == [True]
        assert uow.memories.count_active(type="fact", scope="user", workspace_id="ws", user_id="alice") == 1
        assert uow.memories.count_active(type="fact", scope="user", workspace_id="ws", user_id="bob",
                                         statuses=("active", "candidate")) == 1
        uow.memories.delete_row(a["id"])
        with pytest.raises(NotFound):
            uow.memories.delete_row(a["id"])
        assert uow.memories.delete_user("bob") == {"memory": 1, "runs": 1}


def test_areas_links_and_moves(backend):
    with backend.unit_of_work() as uow:
        src = add(uow, "src area", type="area", payload={"content": "src", "title": "Src"})
    with backend.unit_of_work() as uow:  # a later transaction: a later created_at
        dst = add(uow, "dst area", type="area", payload={"content": "dst", "title": "Dst"})
    with backend.unit_of_work() as uow:
        linked = add(uow, "linked", links=[area_link(src["memory_id"])], observed_at=NOW)
        older = add(uow, "older", links=[area_link(src["memory_id"])], observed_at=NOW - timedelta(days=1))
        expired = add(uow, "expired", links=[area_link(src["memory_id"])], valid_until=NOW - timedelta(days=2),
                      observed_at=NOW - timedelta(days=3))
        pending = add(uow, "pending", status="candidate")
        uow.memories.add_link(pending["id"], area_link(src["memory_id"]))
        uow.memories.add_link(pending["id"], area_link(src["memory_id"]))  # once
        assert uow.memories.get_by_id(pending["id"])["links"] == [area_link(src["memory_id"])]
        assert [r["content"] for r in uow.memories.owner_areas(scope="user", workspace_id="ws", user_id="alice")] == ["src area", "dst area"]
        assert contents(uow.memories.linked_rows(src["memory_id"], stale_at=NOW)) == ["linked", "older"]
        assert contents(uow.memories.linked_rows(src["memory_id"], limit=1)) == ["linked"]
        by_id = uow.memories.areas_by_memory_id([str(src["memory_id"]), str(linked["memory_id"])])
        assert list(by_id) == [str(src["memory_id"])]
        assert uow.memories.move_area_links(src["memory_id"], dst["memory_id"], created_by="admin") == 4
        assert uow.memories.get_active(linked["memory_id"])["version"] == 2
        assert uow.memories.get_by_id(pending["id"])["links"] == [area_link(dst["memory_id"])]
        assert uow.memories.linked_rows(src["memory_id"]) == []
        assert sorted(contents(uow.memories.linked_rows(dst["memory_id"]))) == ["expired", "linked", "older"]
        assert expired["valid_until"] is not None and older["observed_at"] < linked["observed_at"]


# --- runs and meta ----------------------------------------------------------------------------------------------


def test_runs_watermark_summaries_and_purge(backend):
    run_id = uuid.uuid4()
    with backend.unit_of_work() as uow:
        run_row(uow, run_id=run_id, last_message_id="first", dropped=[{"reason": "low_score"}], tokens_in=10,
                candidates_proposed=2, created=1)
    with backend.unit_of_work() as uow:
        run_row(uow, run_id=run_id, last_message_id="second", final_pass=True, status="extract_error",
                dropped=[{"reason": "low_score"}, {"reason": "inferred"}], thread_id="t1")
        run_row(uow, run_id=run_id, thread_id="t2", cost_usd=0.5)
    with backend.unit_of_work() as uow:
        assert uow.runs.get_watermark(source="jsonl", thread_id="t1")["last_message_id"] == "second"
        assert uow.runs.has_final_pass(source="jsonl", thread_id="t1") is True
        assert uow.runs.has_final_pass(source="jsonl", thread_id="t2") is False
        [s] = uow.runs.list_run_summaries(last=5)
        assert (s["run_id"], s["threads"], s["segments"], s["candidates_proposed"], s["created"], s["extract_errors"]) == (
            run_id, 2, 3, 2, 1, 1)
        assert (s["tokens_in"], s["tokens_out"], abs(s["cost_usd"] - 0.5) < 1e-6) == (10, None, True)
        assert len(uow.runs.run_detail(run_id)) == 3
        assert uow.runs.dropped_by_reason(run_id) == {"low_score": 2, "inferred": 1}
        assert uow.runs.purge_dropped(retention_days=1) == 0
        assert uow.runs.purge_dropped(retention_days=-1) == 2
        assert uow.runs.dropped_by_reason(run_id) == {}


def test_meta_and_embedding_config(backend):
    from memhub.storage.rules import EmbeddingMismatch

    with backend.unit_of_work() as uow:
        uow.meta.set_meta("note", {"a": [1, 2]})
        uow.meta.check_embedding_config(model=MODEL, dims=DIMS)
        with pytest.raises(EmbeddingMismatch):
            uow.meta.check_embedding_config(model="other", dims=DIMS)
    with backend.unit_of_work() as uow:
        assert uow.meta.get_meta("note") == {"a": [1, 2]} and uow.meta.get_meta("missing") is None
    backend.init(embedding_model=MODEL, dims=DIMS)  # idempotent
    with pytest.raises(EmbeddingMismatch):
        backend.init(embedding_model=MODEL, dims=DIMS + 1)


def test_a_call_before_init_names_init(make_backend):
    from memhub.storage.rules import EmbeddingMismatch

    b = make_backend(None)
    with pytest.raises(EmbeddingMismatch, match=r"init\(\)"):
        with b.unit_of_work() as uow:
            uow.meta.check_embedding_config(model=MODEL, dims=DIMS)


# --- ingest and reembed through the pipeline --------------------------------------------------------------------


class _Structured:
    def __init__(self, model: "_ScriptedModel", schema: Any) -> None:
        self.model, self.schema = model, schema

    def invoke(self, *_a: Any, **_kw: Any) -> Any:
        from types import SimpleNamespace

        name = getattr(self.schema, "__name__", "")
        parsed = self.model.answers.pop(0) if name == "Extraction" and self.model.answers else self.schema(verdict="unrelated") \
            if name == "Verdict" else None
        if parsed is None:
            raise AssertionError(f"no scripted answer for {name}")
        return {"raw": SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 20}, response_metadata={}),
                "parsed": parsed, "parsing_error": None}


class _ScriptedModel:
    """Extractor/judge stand-in: scripted `Extraction`s in order; any reconcile question gets `unrelated`."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)

    def with_structured_output(self, schema: Any, **_kw: Any) -> _Structured:
        return _Structured(self, schema)


class _HashEmbeddings:
    def __init__(self, dims: int = DIMS, fail: int = 0) -> None:
        self.dims, self.fail = dims, fail

    def embed_query(self, text: str) -> list[float]:
        if self.fail:
            self.fail -= 1
            raise RuntimeError("embedding provider down")
        v = [0.0] * self.dims
        for word in text.lower().split():
            v[sum(map(ord, word)) % self.dims] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


def _ingest(backend, settings, registry, tmp_path, lines, answers, embeddings=None):
    from memhub.config import SourceConfig
    from memhub.pipeline.extract import Extraction
    from memhub.pipeline.ingest import ingest_source
    from memhub.sources.jsonl import JSONLSource

    (tmp_path / "log.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    fields = {"thread_id": "chat_id", "user_id": "user_id", "message_id": "message_id", "timestamp": "timestamp",
              "user_content": "q", "assistant_content": "a"}
    cfg = SourceConfig(kind="jsonl", path="log.jsonl", one_line_per="turn", fields=fields)
    model = _ScriptedModel([Extraction(candidates=a) for a in answers])
    return ingest_source(
        store=backend, settings=settings, registry=registry, source=JSONLSource(cfg, workspace_id="ws", base_dir=tmp_path),
        source_name="jsonl", extractor=model, judge=model, embeddings=embeddings or _HashEmbeddings(), now=NOW,
    )


def _line(thread: str, mid: str, q: str, hours: int = 5) -> dict:
    return {"chat_id": thread, "user_id": "u1", "message_id": mid, "timestamp": (NOW - timedelta(hours=hours)).isoformat(),
            "q": q, "a": "Noted"}


def _cand(content: str, quote: str, mid: str, assertion: str = "stated", type: str = "fact", scope: str = "user",
          **fields: Any) -> Any:
    from memhub.pipeline.extract import Candidate, Evidence

    return Candidate(type=type, scope=scope, fields={"content": content, **fields}, utility=5, applies_generally=True,
                     assertion=assertion, evidence=[Evidence(message_id=mid, quote=quote, claim_source="user")])


def _ledger(backend) -> tuple[list[tuple], list[tuple]]:
    with backend.unit_of_work() as uow:
        mems = sorted((r["type"], r["scope"], r["status"], r["content"], r["version"], r["seen_count"],
                       r["conflicts_with"] is not None) for r in uow.memories.list_memories())
        [summary] = uow.runs.list_run_summaries(last=1) or [None]
        runs = sorted(
            (r["thread_id"], r["candidates_proposed"], r["created"], sorted(d["reason"] for d in r["dropped"]))
            for r in uow.runs.run_detail(summary["run_id"])
        )
    return mems, runs


def test_ingest_writes_the_expected_rows(backend, settings, registry, tmp_path):
    lines = [
        _line("c1", "m1", "I live in Lyon"),
        _line("c2", "m2", "My dog is called Rex"),
        _line("c3", "m3", "As I said, I live in Lyon"),
        _line("c4", "m4", "Answer me in English please", hours=6),
        _line("c5", "m5", "From now on answer me in Portuguese", hours=4),
        _line("c6", "m6", "Our press line runs at night"),
    ]
    answers = [
        [_cand("User lives in Lyon", "I live in Lyon", "m1")],
        [_cand("User has a cat", "I have a cat", "m2")],  # the quote is not in the message: dropped
        [_cand("User lives in Lyon", "I live in Lyon", "m3")],  # the same fact from another thread: merged
        [_cand("Prefers answers in English", "Answer me in English", "m4", type="preference", key="lang")],
        [_cand("Prefers answers in Portuguese", "answer me in Portuguese", "m5", type="preference", key="lang")],
        [_cand("The press line runs at night", "Our press line runs at night", "m6", scope="workspace")],
    ]
    summary = _ingest(backend, settings, registry, tmp_path, lines, answers)
    assert (summary.segments_processed, summary.created, summary.merged, summary.conflicts_opened,
            summary.failed_segments) == (6, 4, 1, 1, 0)
    mems, runs = _ledger(backend)
    assert mems == [
        ("fact", "user", "active", "User lives in Lyon", 1, 2, False),  # seen in c1 and c3
        ("fact", "workspace", "candidate", "The press line runs at night", 1, 1, False),  # waits for review
        ("preference", "user", "active", "Prefers answers in English", 1, 1, False),
        ("preference", "user", "candidate", "Prefers answers in Portuguese", 1, 1, True),  # the slot's other value
    ]
    assert runs == [("c1", 1, 1, []), ("c2", 1, 0, ["ungrounded"]), ("c3", 1, 0, []), ("c4", 1, 1, []),
                    ("c5", 1, 1, []), ("c6", 1, 1, [])]


def test_failed_segment_leaves_nothing_and_is_retried(backend, settings, registry, tmp_path):
    lines = [_line("c1", "m1", "I live in Lyon")]
    answer = [[_cand("User lives in Lyon", "I live in Lyon", "m1")]]
    failed = _ingest(backend, settings, registry, tmp_path, lines, answer, embeddings=_HashEmbeddings(fail=1))
    assert failed.failed_segments == 1
    with backend.unit_of_work() as uow:
        assert uow.memories.list_memories() == []
        assert uow.runs.get_watermark(source="jsonl", thread_id="c1") is None
    again = _ingest(backend, settings, registry, tmp_path, lines, answer)
    assert (again.segments_processed, again.created, again.failed_segments) == (1, 1, 0)
    assert _ledger(backend)[0] == [("fact", "user", "active", "User lives in Lyon", 1, 1, False)]


def test_reembed_to_new_dims(backend, settings, registry):
    from memhub.config import EmbeddingConfig
    from memhub.service import MemoryService
    from memhub.storage.rules import Actor

    with backend.unit_of_work() as uow:
        v1 = add(uow, "first words")
        add(uow, "pending words", status="candidate")
        uow.memories.edit_memory(v1["memory_id"], content="second words", payload={"content": "second words"},
                                 entities=[], embedding=vec(0.5), verified=True, created_by="x")
    s = settings.model_copy(update={"embeddings": EmbeddingConfig(model="bigger", dims=20)})
    service = MemoryService(store=backend, settings=s, registry=registry, embeddings=_HashEmbeddings(dims=20))
    assert service.reembed(Actor(id="admin", roles=["workspace_admin"])) == 3
    with backend.unit_of_work() as uow:
        rows = uow.memories.list_memories()
        assert sorted(r["status"] for r in rows) == ["active", "candidate", "superseded"]
        assert all(len(as_vector_list(r["embedding"])) == 20 for r in rows)
        assert uow.meta.get_meta("embedding") == {"model": "bigger", "dims": 20}

# --- search filters (#25): the `Filter` tree inside each backend's nearest-neighbour query -------------------------


def _m(id_: str, type: str = "machine") -> dict:
    return {"type": type, "id": id_}


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc) if "+" not in text else datetime.fromisoformat(text)


def fsearch(uow, flt, *, k: int = 50, user_id: str = "alice") -> set[str]:
    rows = uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id=user_id, k=k, filter=flt,
                              include_stale=True, now=NOW)
    return set(contents(rows))


def test_filter_entity_id_prefix(backend):
    """#25 C1: `prefix` keeps ids that start with it; `_` is a plain character and case counts."""
    with backend.unit_of_work() as uow:
        for i, (name, ent) in enumerate([("prd1", _m("PRD_1")), ("prd10", _m("PRD_10")), ("prd2", _m("PRD_2")),
                                         ("prd3", _m("PRD_3")), ("prdx1", _m("PRDX1")), ("lower", _m("prd_1")),
                                         ("line", _m("LINE_1")), ("other type", _m("PRD_9", "line"))]):
            add(uow, name, vec(0.9 - i * 0.05), entities=[ent])
    with backend.unit_of_work() as uow:
        assert fsearch(uow, EntityIs("machine", "prefix", ("PRD_",))) == {"prd1", "prd10", "prd2", "prd3"}


def test_filter_entity_id_in_and_contains(backend):
    """#25 C3."""
    with backend.unit_of_work() as uow:
        for i, (name, ent) in enumerate([("prd1", "PRD_1"), ("prd12", "PRD_12"), ("prd2", "PRD_2"), ("prd3", "PRD_3"),
                                         ("lower", "prd_1")]):
            add(uow, name, vec(0.9 - i * 0.05), entities=[_m(ent)])
    with backend.unit_of_work() as uow:
        assert fsearch(uow, EntityIs("machine", "in", ("PRD_1", "PRD_2"))) == {"prd1", "prd2"}
        assert fsearch(uow, EntityIs("machine", "contains", ("D_1",))) == {"prd1", "prd12"}


def test_filter_and_or_not(backend):
    """#25 C4: AND of two subjects, OR of a subject and a subject type, NOT of a subject."""
    with backend.unit_of_work() as uow:
        add(uow, "both", vec(0.95), entities=[_m("PRD_1"), _m("PRD_2")])
        add(uow, "one", vec(0.9), entities=[_m("PRD_1")])
        add(uow, "two", vec(0.85), entities=[_m("PRD_2")])
        add(uow, "line", vec(0.8), entities=[_m("L1", "line")])
        add(uow, "untagged", vec(0.75))
    p1, p2 = EntityIs("machine", "in", ("PRD_1",)), EntityIs("machine", "in", ("PRD_2",))
    with backend.unit_of_work() as uow:
        assert fsearch(uow, AllOf((p1, p2))) == {"both"}
        rows = uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice", k=50, now=NOW,
                                  filter=AnyOf((p1, EntityTypeIn(("line",)))))
        assert sorted(contents(rows)) == ["both", "line", "one"]  # each once
        assert fsearch(uow, NotOf(p1)) == {"two", "line", "untagged"}


def test_filter_entity_type_in(backend):
    """#25 C5."""
    with backend.unit_of_work() as uow:
        add(uow, "machine", vec(0.9), entities=[_m("M1")])
        add(uow, "line", vec(0.85), entities=[_m("L1", "line")])
        add(uow, "part", vec(0.8), entities=[_m("6205", "part")])
    with backend.unit_of_work() as uow:
        assert fsearch(uow, EntityTypeIn(("machine", "line"))) == {"machine", "line"}


def test_filter_areas(backend):
    """#25 C6: rows linked `in_area` to one of the ids; an empty id set keeps none."""
    bearings, lube, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with backend.unit_of_work() as uow:
        add(uow, "in bearings", vec(0.9), links=[area_link(bearings)])
        add(uow, "in lube", vec(0.85), links=[area_link(lube)])
        add(uow, "in other", vec(0.8), links=[area_link(other)])
        add(uow, "no area", vec(0.75))
    with backend.unit_of_work() as uow:
        assert fsearch(uow, AreaIn((str(bearings),))) == {"in bearings"}
        assert fsearch(uow, AreaIn((str(bearings), str(lube)))) == {"in bearings", "in lube"}
        assert fsearch(uow, AreaIn(())) == set()
        assert fsearch(uow, NotOf(AreaIn((str(bearings),)))) == {"in lube", "in other", "no area"}


def test_filter_observed_at_bounds(backend):
    """#25 C8: a date-only `lte` keeps its whole UTC day (AD-049); a datetime bound compares exactly."""
    with backend.unit_of_work() as uow:
        for i, at in enumerate(["2026-10-31T15:00:00", "2026-10-01T00:00:00", "2026-11-01T00:00:00",
                                "2026-09-30T23:59:00", "2026-10-01T11:59:00", "2026-10-01T12:00:00"]):
            add(uow, at, vec(0.9 - i * 0.05), observed_at=_at(at))
    october = TimeRange("observed_at", gte=_at("2026-10-01T00:00:00"), lt=_at("2026-11-01T00:00:00"))
    with backend.unit_of_work() as uow:
        assert fsearch(uow, october) == {"2026-10-31T15:00:00", "2026-10-01T00:00:00", "2026-10-01T11:59:00",
                                         "2026-10-01T12:00:00"}
        assert fsearch(uow, TimeRange("observed_at", gte=_at("2026-10-01T12:00:00+00:00"))) == {
            "2026-10-31T15:00:00", "2026-11-01T00:00:00", "2026-10-01T12:00:00"}
        assert fsearch(uow, TimeRange("observed_at", lte=_at("2026-10-01T11:59:00"))) == {
            "2026-10-01T00:00:00", "2026-09-30T23:59:00", "2026-10-01T11:59:00"}


def test_filter_valid_until_bounds(backend):
    """#25 C9: a null `valid_until` never expires: it passes every lower bound and no upper one (AD-050)."""
    with backend.unit_of_work() as uow:
        add(uow, "dec 1 10:00", vec(0.9), valid_until=_at("2026-12-01T10:00:00"))
        add(uow, "nov 30", vec(0.85), valid_until=_at("2026-11-30T08:00:00"))
        add(uow, "never", vec(0.8))
    with backend.unit_of_work() as uow:
        assert fsearch(uow, TimeRange("valid_until", gte=_at("2026-12-01T00:00:00"))) == {"dec 1 10:00", "never"}
        assert fsearch(uow, TimeRange("valid_until", lt=_at("2026-12-02T00:00:00"))) == {"dec 1 10:00", "nov 30"}


def test_filter_content_contains(backend):
    """#25 C10: case-insensitive, non-ASCII included; `%` and `_` are plain characters."""
    with backend.unit_of_work() as uow:
        add(uow, "Error E47 on start", vec(0.9))
        add(uow, "Ölpumpe defekt", vec(0.85))
        add(uow, "500 rpm", vec(0.8))
        add(uow, "speed 5x0", vec(0.75))
    with backend.unit_of_work() as uow:
        assert fsearch(uow, ContentContains("e47")) == {"Error E47 on start"}
        assert fsearch(uow, ContentContains("ölpumpe")) == {"Ölpumpe defekt"}
        assert fsearch(uow, ContentContains("ÖLPUMPE")) == {"Ölpumpe defekt"}
        assert fsearch(uow, ContentContains("50%")) == set()
        assert fsearch(uow, ContentContains("5_0")) == set()


def test_filter_not_of_null_comparison(backend):
    """#25 C11: a comparison with a null field is false, so its NOT is true (no SQL three-valued logic)."""
    with backend.unit_of_work() as uow:
        add(uow, "never expires", vec(0.9))
        add(uow, "expires nov 30", vec(0.85), valid_until=_at("2026-11-30T00:00:00"))
        add(uow, "seen sept", vec(0.8), observed_at=_at("2026-09-15T00:00:00"))
    with backend.unit_of_work() as uow:
        before_dec = TimeRange("valid_until", lt=_at("2026-12-02T00:00:00"))
        assert "never expires" in fsearch(uow, NotOf(before_dec))
        assert "expires nov 30" not in fsearch(uow, NotOf(before_dec))
        assert "seen sept" in fsearch(uow, NotOf(TimeRange("observed_at", gte=_at("2026-10-01T00:00:00"))))



def test_filter_parsed_time_bounds(backend):
    """#25 C38: the bounds `memhub.filters.parse` builds - a date-only `lte` is its whole UTC day (AD-049); a datetime
    `lte` drops a null `valid_until`, and its NOT keeps it (AD-050)."""
    from memhub.filters import parse

    def tree(f):
        return parse(f, declared=[]).rest

    with backend.unit_of_work() as uow:
        add(uow, "last microsecond", vec(0.9), observed_at=_at("2026-10-31T23:59:59.999999"))
        add(uow, "next day", vec(0.85), observed_at=_at("2026-11-01T00:00:00"))
        add(uow, "first instant", vec(0.8), observed_at=_at("2026-10-01T00:00:00"))
    with backend.unit_of_work() as uow:
        assert fsearch(uow, tree({"observed_at": {"lte": "2026-10-31"}})) == {"last microsecond", "first instant"}
        assert fsearch(uow, tree({"observed_at": {"gte": "2026-10-01", "lte": "2026-10-01"}})) == {"first instant"}
    with backend.unit_of_work() as uow:
        add(uow, "never expires", vec(0.7))
        add(uow, "expires nov 30", vec(0.65), valid_until=_at("2026-11-30T00:00:00"))
    with backend.unit_of_work() as uow:
        upper = {"valid_until": {"lte": "2026-12-01T00:00:00Z"}}
        assert fsearch(uow, tree(upper)) == {"expires nov 30"}
        assert "never expires" in fsearch(uow, tree({"NOT": upper}))
        assert fsearch(uow, tree({"valid_until": {"gte": "2026-11-01T00:00:00Z"}})) >= {"never expires", "expires nov 30"}


FILTER_TABLE = [
    EntityIs("machine", "in", ("PRD_1",)),
    EntityIs("machine", "prefix", ("PRD_",)),
    EntityIs("machine", "contains", ("D_1",)),
    EntityTypeIn(("line",)),
    TypeIn(("preference",)),
    ContentContains("pump"),
    TimeRange("observed_at", gte=datetime(2026, 1, 5, tzinfo=timezone.utc)),
    TimeRange("valid_until", gte=datetime(2026, 6, 1, tzinfo=timezone.utc)),
    TimeRange("valid_until", lte=datetime(2026, 6, 1, tzinfo=timezone.utc)),
    AnyOf((EntityTypeIn(("line",)), ContentContains("PUMP"))),
    AllOf((EntityIs("machine", "prefix", ("PRD",)), NotOf(ContentContains("pump")))),
    NotOf(AnyOf((EntityIs("machine", "in", ("PRD_1",)), TimeRange("valid_until", lte=datetime(2026, 6, 1, tzinfo=timezone.utc))))),
]


def test_filter_matches_agrees_with_search(backend):
    """#25 C12: `Filter.matches` (what `service.list` uses) keeps exactly the rows the backend's query keeps, for each
    of the 12 filters of `FILTER_TABLE`."""
    rows = [
        dict(content="pump PRD_1", entities=[_m("PRD_1")], observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        dict(content="fan PRD_12", entities=[_m("PRD_12"), _m("L1", "line")], valid_until=datetime(2026, 7, 1, tzinfo=timezone.utc)),
        dict(content="Pump line", entities=[_m("L2", "line")], type="preference", valid_until=datetime(2026, 5, 1, tzinfo=timezone.utc)),
        dict(content="nothing", entities=[], observed_at=datetime(2026, 1, 9, tzinfo=timezone.utc)),
        dict(content="PRDX", entities=[_m("PRDX")]),
    ]
    with backend.unit_of_work() as uow:
        for i, r in enumerate(rows):
            add(uow, embedding=vec(0.9 - i * 0.05), **r)
    with backend.unit_of_work() as uow:
        stored = uow.memories.list_memories(workspace_id="ws")
        for i, flt in enumerate(FILTER_TABLE):
            assert fsearch(uow, flt) == {r["content"] for r in stored if flt.matches(r)}, f"FILTER_TABLE[{i}]"


def test_filter_fills_k_from_matching_rows(backend):
    """#25 C13: the filter runs inside the query, so 30 nearer non-matching rows do not crowd out the 2 that match."""
    with backend.unit_of_work() as uow:
        for i in range(30):
            add(uow, f"line {i}", vec(0.99 - i * 0.001), entities=[_m("LINE_1")])
        add(uow, "prd a", vec(0.3), entities=[_m("PRD_1")])
        add(uow, "prd b", vec(0.2), entities=[_m("PRD_1")])
    with backend.unit_of_work() as uow:
        assert fsearch(uow, EntityIs("machine", "prefix", ("PRD_",)), k=5) == {"prd a", "prd b"}


__all__ = ["backend", *sorted(n for n in dir() if n.startswith("test_"))]
