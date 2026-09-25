"""Ledger tests against a real pgvector Postgres (docker), skipped if docker is unavailable."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from memhub.store import Conflict, EmbeddingMismatch, MemoryStore, NotFound, StoreError
from tests.conftest import TEST_DIMS, TEST_EMBEDDING_MODEL


def vec(seed: float) -> list[float]:
    v = [0.0] * TEST_DIMS
    v[0] = seed
    v[1] = 1.0 - abs(seed)
    return v


def test_init_is_idempotent(store: MemoryStore):
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    with store.connect() as conn, conn.cursor() as cur:
        assert store.get_meta(cur, "embedding") == {"model": TEST_EMBEDDING_MODEL, "dims": TEST_DIMS}


def test_init_never_overwrites_the_saved_embedding_config(store: MemoryStore):
    with pytest.raises(EmbeddingMismatch):
        store.init(embedding_model="other-model", dims=TEST_DIMS)
    with store.connect() as conn, conn.cursor() as cur:
        assert store.get_meta(cur, "embedding") == {"model": TEST_EMBEDDING_MODEL, "dims": TEST_DIMS}


def test_queue_lists_user_scope_candidates_and_conflicts_first(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        plain = _add(store, cur, scope="user", status="candidate", content="plain")
        other = _add(store, cur, content="old")
        conflict = _add(store, cur, scope="user", status="candidate", content="new", conflicts_with=other["memory_id"])
        ids = [r["id"] for r in store.queue(cur)]
        assert ids == [conflict["id"], plain["id"]]


def test_reembed_retypes_the_column_and_clears_old_vectors(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, content="a")
        _add(store, cur, status="candidate", content="b")
        store.set_embedding_dims(cur, TEST_DIMS + 4)  # would fail without USING NULL
        cur.execute(f"SELECT count(*) AS n FROM {store._t('memory')} WHERE embedding IS NOT NULL")
        assert cur.fetchone()["n"] == 0
        assert sum(len(b) for b in store.iter_for_reembed(cur)) == 2  # candidates are re-embedded too
        store.set_embedding_dims(cur, TEST_DIMS)  # restore for the shared fixture
        conn.rollback()


def test_run_summary_counts_threads_and_segments_separately(store: MemoryStore):
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    run_id = uuid.uuid4()
    with store.connect() as conn, conn.cursor() as cur:
        for final in (False, True):
            store.upsert_run(
                cur, run_id=run_id, source="jsonl", thread_id="t1", workspace_id="ws1", user_id="alice",
                first_message_id="1", last_message_id="2", last_message_at=now, final_pass=final,
                status="extract_error" if final else "ok",
            )
        [summary] = store.list_run_summaries(cur, last=5)
        assert (summary["threads"], summary["segments"], summary["extract_errors"]) == (1, 2, 1)


def test_check_embedding_config_mismatch(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        store.check_embedding_config(cur, model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)  # no raise
        with pytest.raises(EmbeddingMismatch):
            store.check_embedding_config(cur, model="other", dims=TEST_DIMS)
        with pytest.raises(EmbeddingMismatch):
            store.check_embedding_config(cur, model=TEST_EMBEDDING_MODEL, dims=999)


def _add(store, cur, **overrides):
    defaults = dict(
        type="fact", schema_version=1, scope="user", workspace_id="ws1", user_id="alice",
        content="likes dark mode", payload={"content": "likes dark mode"}, entities=[],
        embedding=vec(0.1), evidence=[{"source": "manual", "actor": "alice"}],
        status="active", verified=False, created_by="alice",
    )
    defaults.update(overrides)
    return store.add_memory(cur, **defaults)


def test_add_and_get_active(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        active = store.get_active(cur, row["memory_id"])
        assert active["id"] == row["id"]
        assert active["version"] == 1


def test_edit_supersedes_old_and_activates_new(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        edited = store.edit_memory(
            cur, row["memory_id"], content="likes light mode", payload={"content": "likes light mode"},
            entities=[], embedding=vec(0.2), verified=True, created_by="alice",
        )
        assert edited["version"] == 2
        assert edited["status"] == "active"
        old = store.get_by_id(cur, row["id"])
        assert old["status"] == "superseded"
        # never more than one active version for a memory_id
        cur.execute(
            f"SELECT count(*) AS n FROM {store._t('memory')} WHERE memory_id=%s AND status='active'",
            (row["memory_id"],),
        )
        assert cur.fetchone()["n"] == 1


def test_supersede_is_a_one_shot_optimistic_lock(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        assert store._supersede(cur, row["id"]) == 1
        # a second, concurrent attempt to supersede the same (now-superseded) row loses the race
        assert store._supersede(cur, row["id"]) == 0


def test_edit_missing_memory_raises_not_found(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        with pytest.raises(NotFound):
            store.edit_memory(
                cur, uuid.uuid4(), content="x", payload={"content": "x"}, entities=[],
                embedding=vec(0.1), verified=True, created_by="alice",
            )


def test_archive(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        archived = store.archive(cur, row["memory_id"])
        assert archived["status"] == "archived"
        assert store.get_active(cur, row["memory_id"]) is None


def test_reject_only_candidate(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur, status="active")
        with pytest.raises(StoreError):
            store.reject(cur, row["id"], reviewed_by="admin")
        cand = _add(store, cur, status="candidate", verified=False, scope="workspace", user_id=None)
        rejected = store.reject(cur, cand["id"], reviewed_by="admin", note="nope")
        assert rejected["status"] == "rejected"
        assert rejected["reviewed_by"] == "admin"


def test_approve_plain_candidate(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        cand = _add(store, cur, status="candidate", verified=False, scope="workspace", user_id=None)
        approved = store.approve(cur, cand["id"], reviewed_by="admin")
        assert approved["status"] == "active"
        assert approved["verified"] is True


def test_approve_edit_candidate_supersedes_sibling(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        active = _add(store, cur, status="active", verified=True, scope="workspace", user_id=None)
        cand = store.add_memory(
            cur, type="fact", schema_version=1, scope="workspace", workspace_id="ws1", user_id=None,
            content="v2", payload={"content": "v2"}, entities=[], embedding=vec(0.2),
            evidence=[], status="candidate", verified=False, created_by="extractor",
            memory_id=active["memory_id"], version=2,
        )
        approved = store.approve(cur, cand["id"], reviewed_by="admin")
        assert approved["version"] == 2
        assert approved["status"] == "active"
        old = store.get_by_id(cur, active["id"])
        assert old["status"] == "superseded"


def test_approve_conflict_requires_resolve(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        old = _add(store, cur, status="active", verified=True, scope="workspace", user_id=None)
        cand = _add(
            store, cur, status="candidate", verified=False, scope="workspace", user_id=None,
            conflicts_with=old["memory_id"],
        )
        with pytest.raises(StoreError):
            store.approve(cur, cand["id"], reviewed_by="admin")


@pytest.mark.parametrize("resolve", ["keep_old", "replace", "keep_both"])
def test_approve_conflict_resolutions(store: MemoryStore, resolve):
    with store.connect() as conn, conn.cursor() as cur:
        old = _add(store, cur, status="active", verified=True, scope="workspace", user_id=None)
        cand = _add(
            store, cur, status="candidate", verified=False, scope="workspace", user_id=None,
            conflicts_with=old["memory_id"],
        )
        result = store.approve(cur, cand["id"], reviewed_by="admin", resolve=resolve)
        old_now = store.get_by_id(cur, old["id"])
        if resolve == "keep_old":
            assert result["status"] == "rejected"
            assert old_now["status"] == "active"
        elif resolve == "replace":
            assert result["status"] == "active"
            assert old_now["status"] == "archived"
        else:  # keep_both
            assert result["status"] == "active"
            assert old_now["status"] == "active"


def test_merge_evidence_increments_seen_count_only_for_new_thread(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur, evidence=[{"source": "trace", "thread_id": "t1", "quote": "a"}])
        merged = store.merge_evidence(
            cur, row["memory_id"], new_evidence=[{"source": "trace", "thread_id": "t1", "quote": "b"}],
            thread_id="t1",
        )
        assert merged["seen_count"] == 1
        assert len(merged["evidence"]) == 2
        merged2 = store.merge_evidence(
            cur, row["memory_id"], new_evidence=[{"source": "trace", "thread_id": "t2", "quote": "c"}],
            thread_id="t2",
        )
        assert merged2["seen_count"] == 2


def test_delete_row_and_not_found(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        store.delete_row(cur, row["id"])
        assert store.get_by_id(cur, row["id"]) is None
        with pytest.raises(NotFound):
            store.delete_row(cur, row["id"])


def test_delete_user_removes_memory_and_run_rows_only_for_that_user(store: MemoryStore):
    import datetime

    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, user_id="alice", scope="user")
        _add(store, cur, user_id="bob", scope="user")
        store.upsert_run(
            cur, run_id=uuid.uuid4(), source="jsonl", thread_id="alice", workspace_id="ws1", user_id="alice",
            first_message_id="1", last_message_id="2", last_message_at=datetime.datetime.now(datetime.timezone.utc),
        )
        result = store.delete_user(cur, "alice")
        assert result["memory"] == 1
        assert result["runs"] == 1
        remaining = store.list_memories(cur, user_id="bob")
        assert len(remaining) == 1
        assert store.list_memories(cur, user_id="alice") == []


def test_list_memories_filters(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, type="fact", user_id="alice")
        _add(store, cur, type="episode", user_id="alice", payload={"content": "x", "situation": "s", "actions": "a", "outcome": "o"})
        facts = store.list_memories(cur, type="fact")
        assert len(facts) == 1
        assert facts[0]["type"] == "fact"


def test_search_visibility_and_scoring(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, user_id="alice", scope="user", content="alice likes tea", embedding=vec(0.9))
        _add(store, cur, user_id="bob", scope="user", content="bob likes coffee", embedding=vec(-0.9))
        _add(store, cur, user_id=None, scope="workspace", verified=True, content="team meets monday", embedding=vec(0.5))
        results = store.search(cur, query_embedding=vec(0.9), workspace_id="ws1", user_id="alice", k=10)
        contents = {r["content"] for r in results}
        assert "alice likes tea" in contents
        assert "bob likes coffee" not in contents  # never another user's memory
        assert "team meets monday" in contents  # workspace visible to everyone


def test_search_only_active(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, user_id="alice", scope="user", status="candidate", verified=False)
        results = store.search(cur, query_embedding=vec(0.1), workspace_id="ws1", user_id="alice")
        assert results == []


def test_top_similar_for_reconcile(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        base = _add(store, cur, content="likes tea", embedding=vec(0.9))
        similar = store.top_similar(
            cur, type="fact", scope="user", workspace_id="ws1", user_id="alice",
            query_embedding=vec(0.9), limit=5,
        )
        assert similar[0]["id"] == base["id"]
        assert similar[0]["similarity"] > 0.99


def test_watermark_and_run_rows(store: MemoryStore):
    import datetime

    with store.connect() as conn, conn.cursor() as cur:
        assert store.get_watermark(cur, source="jsonl", thread_id="t1") is None
        now = datetime.datetime.now(datetime.timezone.utc)
        run_id = uuid.uuid4()
        store.upsert_run(
            cur, run_id=run_id, source="jsonl", thread_id="t1", workspace_id="ws1", user_id="alice",
            first_message_id="1", last_message_id="5", last_message_at=now,
            candidates_proposed=2, created=1, merged=0, tokens_in=100, tokens_out=20, cost_usd=0.01,
        )
        wm = store.get_watermark(cur, source="jsonl", thread_id="t1")
        assert wm["last_message_id"] == "5"
        summaries = store.list_run_summaries(cur, last=10)
        assert summaries[0]["run_id"] == run_id
        assert summaries[0]["candidates_proposed"] == 2


def test_dropped_by_reason_and_purge(store: MemoryStore):
    import datetime

    with store.connect() as conn, conn.cursor() as cur:
        run_id = uuid.uuid4()
        old_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=200)
        store.upsert_run(
            cur, run_id=run_id, source="jsonl", thread_id="t1", workspace_id="ws1", user_id="alice",
            first_message_id="1", last_message_id="2", last_message_at=old_time,
            dropped=[{"candidate": {"content": "x"}, "reason": "ungrounded"},
                     {"candidate": {"content": "y"}, "reason": "low_score"}],
        )
        cur.execute(f"UPDATE {store._t('memory_runs')} SET processed_at = %s", (old_time,))
        counts = store.dropped_by_reason(cur, run_id)
        assert counts == {"ungrounded": 1, "low_score": 1}
        purged = store.purge_dropped(cur, retention_days=90)
        assert purged == 1
        assert store.run_detail(cur, run_id)[0]["dropped"] == []


def test_reembed_helpers(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, content="a")
        _add(store, cur, content="b")
        batches = list(store.iter_for_reembed(cur, batch_size=1))
        assert sum(len(b) for b in batches) == 2
        row_id = batches[0][0]["id"]
        store.update_embedding(cur, row_id, vec(0.42))


T0 = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


def _evidence_at(ts: datetime, thread_id: str = "t1") -> dict:
    return {"source": "jsonl", "thread_id": thread_id, "message_id": f"m-{thread_id}-{ts.isoformat()}", "observed_at": ts.isoformat()}


def test_init_upgrades_a_ledger_created_before_observed_at(store: MemoryStore):
    mem = store._t("memory")
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        cur.execute(f"ALTER TABLE {mem} DROP COLUMN observed_at")  # what a v1 ledger looks like
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)  # second run is a no-op
    with store.connect() as conn, conn.cursor() as cur:
        upgraded = store.get_by_id(cur, row["id"])
        assert upgraded["observed_at"] == upgraded["created_at"]
        _add(store, cur)  # the column is NOT NULL and writable again


def test_add_memory_observed_at_is_the_newest_evidence_timestamp(store: MemoryStore):
    evidence = [_evidence_at(T0 - timedelta(days=3)), _evidence_at(T0 - timedelta(days=1)), _evidence_at(T0 - timedelta(days=2))]
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur, evidence=evidence)
        assert row["observed_at"] == T0 - timedelta(days=1)
        assert _add(store, cur, observed_at=T0)["observed_at"] == T0


def test_add_memory_without_dated_evidence_defaults_to_now(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        assert row["observed_at"] == row["created_at"]


def test_init_upgrades_a_ledger_created_before_assertion(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        cur.execute(f"ALTER TABLE {store._t('memory')} DROP COLUMN assertion")
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    with store.connect() as conn, conn.cursor() as cur:
        assert store.get_by_id(cur, row["id"])["assertion"] == "stated"


def test_assertion_defaults_to_stated_and_survives_edit_and_search(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        assert _add(store, cur)["assertion"] == "stated"
        row = _add(store, cur, content="maybe brazilian", assertion="inferred", embedding=vec(0.5))
        assert row["assertion"] == "inferred"
        edited = store.edit_memory(
            cur, row["memory_id"], content="new", payload={"content": "new"}, entities=[],
            embedding=vec(0.5), verified=True, created_by="alice",
        )
        assert edited["assertion"] == "inferred"
        hits = store.search(cur, query_embedding=vec(0.5), workspace_id="ws1", user_id="alice")
        assert {h["assertion"] for h in hits} == {"stated", "inferred"}


@pytest.mark.parametrize("merge", ["merge_evidence", "merge_evidence_row"])
def test_merge_moves_observed_at_forward_only(store: MemoryStore, merge: str):
    def do(cur, row, ts, thread):
        target = row["memory_id"] if merge == "merge_evidence" else row["id"]
        return getattr(store, merge)(cur, target, new_evidence=[_evidence_at(ts, thread)], thread_id=thread)

    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur, evidence=[_evidence_at(T0, "t1")])
        assert do(cur, row, T0 - timedelta(days=5), "t2")["observed_at"] == T0
        assert do(cur, row, T0 + timedelta(days=5), "t3")["observed_at"] == T0 + timedelta(days=5)


def test_edit_keeps_observed_at(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur, observed_at=T0)
        edited = store.edit_memory(
            cur, row["memory_id"], content="new", payload={"content": "new"}, entities=[],
            embedding=vec(0.2), verified=True, created_by="alice",
        )
        assert edited["observed_at"] == T0


def test_search_and_list_rows_carry_observed_at(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, observed_at=T0)
        [hit] = store.search(cur, query_embedding=vec(0.1), workspace_id="ws1", user_id="alice")
        assert hit["observed_at"] == T0
        assert store.list_memories(cur)[0]["observed_at"] == T0


def test_init_upgrades_a_ledger_created_before_validity_columns(store: MemoryStore):
    mem = store._t("memory")
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        for col in ("valid_from", "valid_until", "durability"):
            cur.execute(f"ALTER TABLE {mem} DROP COLUMN {col}")
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    with store.connect() as conn, conn.cursor() as cur:
        old = store.get_by_id(cur, row["id"])
        assert (old["valid_from"], old["valid_until"], old["durability"]) == (None, None, None)


def test_validity_columns_are_stored_and_survive_edit_and_promote(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur, valid_from=T0, valid_until=T0 + timedelta(days=30), durability="temporary")
        assert (row["valid_from"], row["valid_until"], row["durability"]) == (T0, T0 + timedelta(days=30), "temporary")
        edited = store.edit_memory(
            cur, row["memory_id"], content="new", payload={"content": "new"}, entities=[],
            embedding=vec(0.2), verified=True, created_by="alice",
        )
        assert (edited["valid_from"], edited["valid_until"], edited["durability"]) == (T0, T0 + timedelta(days=30), "temporary")


@pytest.mark.parametrize("merge", ["merge_evidence", "merge_evidence_row"])
@pytest.mark.parametrize("old,new,expected", [
    (10, 20, 20), (20, 10, 20), (10, None, None), (None, 10, None), (None, None, None),
])
def test_merge_moves_valid_until_to_the_later_with_null_latest(store: MemoryStore, merge: str, old, new, expected):
    def at(days):
        return None if days is None else T0 + timedelta(days=days)

    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur, evidence=[_evidence_at(T0, "t1")], valid_until=at(old))
        target = row["memory_id"] if merge == "merge_evidence" else row["id"]
        merged = getattr(store, merge)(cur, target, new_evidence=[_evidence_at(T0, "t2")], thread_id="t2", valid_until=at(new))
        assert merged["valid_until"] == at(expected)
        # not passing valid_until leaves it alone
        kept = getattr(store, merge)(cur, target, new_evidence=[_evidence_at(T0, "t3")], thread_id="t3")
        assert kept["valid_until"] == at(expected)


def test_stale_is_computed_from_valid_until_and_excluded_from_search_unless_asked(store: MemoryStore):
    now = T0 + timedelta(days=31)
    with store.connect() as conn, conn.cursor() as cur:
        old = _add(store, cur, content="temp", valid_until=T0 + timedelta(days=30))
        _add(store, cur, content="stable", valid_until=None)
        _add(store, cur, content="later", valid_until=now + timedelta(days=1))
        found = lambda **kw: {r["content"] for r in store.search(cur, query_embedding=vec(0.1), workspace_id="ws1", user_id="alice", now=now, **kw)}
        assert found() == {"stable", "later"}
        assert found(include_stale=True) == {"temp", "stable", "later"}
        assert store.get_by_id(cur, old["id"])["status"] == "active"  # nothing was archived


def test_list_marks_stale_rows_and_can_filter_to_them(store: MemoryStore):
    now = T0 + timedelta(days=31)
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, content="temp", valid_until=T0 + timedelta(days=30))
        _add(store, cur, content="stable")
        _add(store, cur, content="archived", status="archived", valid_until=T0)
        rows = {r["content"]: r["stale"] for r in store.list_memories(cur, now=now)}
        assert rows == {"temp": True, "stable": False, "archived": False}
        assert [r["content"] for r in store.list_memories(cur, now=now, stale=True)] == ["temp"]


def test_top_similar_still_sees_stale_rows(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        _add(store, cur, valid_until=T0)  # long expired
        assert len(store.top_similar(
            cur, type="fact", scope="user", workspace_id="ws1", user_id="alice", query_embedding=vec(0.1),
        )) == 1


def _link(memory_id) -> dict:
    return {"kind": "derived_from", "memory_id": str(memory_id)}


def _search(store, cur, **kw):
    return {r["content"]: r for r in store.search(
        cur, query_embedding=vec(0.1), workspace_id="ws1", user_id="alice", now=T0, **kw)}


def test_init_upgrades_a_ledger_created_before_links(store: MemoryStore):
    mem = store._t("memory")
    with store.connect() as conn, conn.cursor() as cur:
        row = _add(store, cur)
        cur.execute(f"DROP INDEX {store.prefix}_memory_links_gin")
        cur.execute(f"ALTER TABLE {mem} DROP COLUMN links")
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    store.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    with store.connect() as conn, conn.cursor() as cur:
        assert store.get_by_id(cur, row["id"])["links"] == []
        cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", (f"{store.prefix}_memory_links_gin",))
        assert cur.fetchone() is not None
        assert _add(store, cur, links=[_link(row["memory_id"])])["links"] == [_link(row["memory_id"])]


def test_links_default_to_empty_and_survive_edit_and_promote_style_copies(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        episode = _add(store, cur, type="episode", content="episode")
        row = _add(store, cur, links=[_link(episode["memory_id"])])
        assert episode["links"] == []
        edited = store.edit_memory(
            cur, row["memory_id"], content="new", payload={"content": "new"}, entities=[], embedding=vec(0.2),
            verified=True, created_by="alice",
        )
        candidate = store.edit_memory(
            cur, row["memory_id"], content="newer", payload={"content": "newer"}, entities=[], embedding=vec(0.2),
            verified=False, created_by="alice", as_candidate=True,
        )
        assert edited["links"] == candidate["links"] == [_link(episode["memory_id"])]


def test_add_link_appends_once(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        a, b, row = (_add(store, cur, content=c) for c in ("a", "b", "row"))
        for target in (a, a, b):
            store.add_link(cur, row["id"], _link(target["memory_id"]))
        assert store.get_by_id(cur, row["id"])["links"] == [_link(a["memory_id"]), _link(b["memory_id"])]


def test_search_results_carry_no_context(store: MemoryStore):
    with store.connect() as conn, conn.cursor() as cur:
        episode = _add(store, cur, type="episode", content="Chat about moving", embedding=vec(-0.9))
        _add(store, cur, content="lives in Lyon", links=[_link(episode["memory_id"])])
        hits = _search(store, cur)
        assert "context" not in hits["lives in Lyon"] and hits["lives in Lyon"]["links"] == [_link(episode["memory_id"])]


def test_the_database_rejects_a_second_active_row_for_the_same_owner_type_and_key(store: MemoryStore):
    import psycopg

    def slot(**kw):
        return _add(store, kw.pop("cur"), type="profile", payload={"content": "c", "key": "city"}, **kw)

    with store.connect() as conn, conn.cursor() as cur:
        slot(cur=cur)
        slot(cur=cur, user_id="bob")  # another owner is fine
        slot(cur=cur, status="superseded")  # history is fine
    with pytest.raises(psycopg.errors.UniqueViolation):
        with store.connect() as conn, conn.cursor() as cur:
            slot(cur=cur)
