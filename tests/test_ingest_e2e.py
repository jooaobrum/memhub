from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from memhub.config import SignalsConfig, SourceConfig
from memhub.pipeline.extract import Candidate, Evidence, Extraction
from memhub.pipeline.ingest import ingest_source
from memhub.pipeline.reconcile import Verdict
from memhub.sources.jsonl import JSONLSource
from tests.fakes import (
    PARSE_ERROR, ExplodingModel, FakeChatModel, FakeEmbeddings, MappedEmbeddings, extraction, vec_at,
)

NOW = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
FIELDS = {
    "thread_id": "chat_id", "user_id": "user_id", "message_id": "message_id", "timestamp": "timestamp",
    "trace_id": "trace_id", "user_content": "user_query", "assistant_content": "answer", "metadata": ["intent"],
}
SIGNALS = SignalsConfig(
    path="fb.jsonl", join_on={"message_id": "message_id", "thread_id": "chat_id"},
    map={"rating": {"down": "feedback_down", "up": "feedback_up"}},
)


def line(thread="c1", mid="m1", user="u1", query="I live in Lyon", answer="Noted, you live in Paris", age=timedelta(hours=5), **extra):
    return {
        "chat_id": thread, "user_id": user, "message_id": mid, "timestamp": (NOW - age).isoformat(), "trace_id": f"tr-{mid}",
        "user_query": query, "answer": answer, **extra,
    }


def cand(type="fact", quote="I live in Lyon", mid="m1", claim="user", utility=5, scope="user", fields=None, assertion="stated", **extra):
    fields = fields or {"content": "User lives in Lyon", **extra}
    return Candidate(
        type=type, scope=scope, fields=fields, utility=utility, applies_generally=True, assertion=assertion,
        evidence=[Evidence(message_id=mid, quote=quote, claim_source=claim)],
    )


class RecordingModel(FakeChatModel):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.schemas = []

    def with_structured_output(self, schema, **kw):
        self.schemas.append(schema)
        return super().with_structured_output(schema, **kw)


@dataclass
class Env:
    store: Any
    settings: Any
    registry: Any
    tmp: Any
    embeddings: Any

    def write(self, *lines, feedback=()):
        (self.tmp / "log.jsonl").write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")
        (self.tmp / "fb.jsonl").write_text("\n".join(json.dumps(x) for x in feedback) + "\n")

    def run(self, extractor, judge=None, embeddings=None, **kw):
        cfg = SourceConfig(kind="jsonl", path="log.jsonl", one_line_per="turn", fields=FIELDS, signals=SIGNALS)
        return ingest_source(
            store=self.store, settings=self.settings, registry=self.registry,
            source=JSONLSource(cfg, workspace_id="ws", base_dir=self.tmp), source_name="jsonl",
            extractor=extractor, judge=judge or FakeChatModel(unrelated_by_default=True), embeddings=embeddings or self.embeddings,
            now=kw.pop("now", NOW), **kw,
        )

    def memories(self, **kw):
        with self.store.connect() as conn, conn.cursor() as cur:
            return self.store.list_memories(cur, **kw)

    def runs(self):
        with self.store.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self.store._t('memory_runs')} ORDER BY processed_at")
            return cur.fetchall()


@pytest.fixture
def env(store, settings, registry, tmp_path, fake_embeddings):
    return Env(store, settings, registry, tmp_path, fake_embeddings)


def test_creates_an_active_unverified_user_memory_with_quote_only_evidence(env):
    env.write(line())
    s = env.run(FakeChatModel([extraction(cand())]))
    assert (s.threads_seen, s.threads_processed, s.segments_processed, s.candidates_proposed, s.created) == (1, 1, 1, 1, 1)
    assert (s.tokens_in, s.tokens_out, s.cost_usd, s.failed_segments, s.dropped_by_reason) == (100, 20, None, 0, {})
    [m] = env.memories()
    assert (m["status"], m["verified"], m["created_by"], m["scope"], m["user_id"]) == ("active", False, "extractor", "user", "u1")
    assert m["evidence"] == [{
        "source": "jsonl", "trace_id": "tr-m1", "thread_id": "c1", "message_id": "m1",
        "observed_at": (NOW - timedelta(hours=5)).isoformat(), "quote": "I live in Lyon", "claim_source": "user",
    }]
    assert m["observed_at"] == NOW - timedelta(hours=5)
    assert m["score"] == pytest.approx(0.935, abs=1e-3)
    assert "Paris" not in json.dumps(m["evidence"]) + json.dumps(m["payload"])
    [r] = env.runs()
    assert (r["thread_id"], r["last_message_id"], r["status"], r["created"], r["tokens_in"]) == ("c1", "m1:a", "ok", 1, 100)


def test_thread_not_idle_yet_is_left_alone(env):
    env.write(line(age=timedelta(minutes=10)))
    model = FakeChatModel()
    s = env.run(model)
    assert (s.segments_processed, model.calls) == (0, 0) and env.runs() == [] and env.memories() == []


def test_second_run_makes_no_calls_and_creates_nothing(env):
    env.write(line())
    model = FakeChatModel([extraction(cand())])
    env.run(model)
    calls, embeds = model.calls, env.embeddings.calls
    s = env.run(model)
    assert (model.calls, env.embeddings.calls) == (calls, embeds)
    assert (s.segments_processed, s.created) == (0, 0) and len(env.memories()) == 1 and len(env.runs()) == 1


def test_ungrounded_and_assistant_only_are_dropped_and_recorded(env):
    env.write(line())
    model = FakeChatModel([extraction(
        cand(quote="I live in Nice"), cand(quote="live in Paris", mid="m1:a", claim="assistant"), cand(),
    )])
    s = env.run(model)
    assert s.dropped_by_reason == {"ungrounded": 1, "assistant_only": 1} and s.created == 1
    [r] = env.runs()
    assert [d["reason"] for d in r["dropped"]] == ["ungrounded", "assistant_only"]
    assert r["dropped"][0]["candidate"]["evidence"][0]["quote"] == "I live in Nice"


def test_candidates_are_capped_at_max_candidates(env):
    env.write(line())
    facts = [cand(fields={"content": f"fact {i} about Lyon"}) for i in range(5)]
    s = env.run(FakeChatModel([extraction(*facts)]), embeddings=FakeEmbeddings(16))
    assert s.candidates_proposed == 3


def test_extract_error_records_status_and_advances_watermark(env):
    env.write(line())
    model = FakeChatModel([PARSE_ERROR])
    s = env.run(model)
    assert (s.extract_errors, s.created, s.segments_processed) == (1, 0, 1)
    assert [r["status"] for r in env.runs()] == ["extract_error"]
    env.run(model)
    assert model.calls == 1


def test_llm_failure_rolls_back_and_is_retried_next_run(env):
    env.write(line(), line(thread="c2", mid="m9"))
    boom = ExplodingModel()
    s = env.run(boom)
    assert (s.failed_segments, s.segments_processed, boom.calls) == (2, 0, 2)
    assert env.memories() == [] and env.runs() == []
    s = env.run(FakeChatModel([extraction(cand()), extraction(cand(mid="m9"))]), embeddings=FakeEmbeddings(16))
    assert (s.failed_segments, s.segments_processed) == (0, 2)


def test_embedding_failure_leaves_no_partial_rows(env):
    env.write(line())

    class Flaky(FakeEmbeddings):
        def embed_query(self, text):
            if self.calls == 1:
                raise ConnectionError("boom")
            return super().embed_query(text)

    flaky = Flaky(16)
    two = extraction(cand(), cand(fields={"content": "User likes cheese"}, quote="Lyon"))
    s = env.run(FakeChatModel([two]), embeddings=flaky)
    assert s.failed_segments == 1 and env.memories() == [] and env.runs() == []


def test_prefilter_skips_without_llm_but_records_signals_and_watermark(env):
    ing = env.settings.ingestion.model_copy(update={"skip_when": {"intent": ["greeting"]}})
    env.settings = env.settings.model_copy(update={"ingestion": ing})
    env.write(line(query="oi", intent="greeting", answer="olá"), feedback=[{"chat_id": "c1", "message_id": "m1", "rating": "down"}])
    model = FakeChatModel()
    s = env.run(model)
    assert (model.calls, s.segments_processed, s.created) == (0, 1, 0)
    [r] = env.runs()
    assert r["status"] == "ok" and r["signals"] == [{"kind": "feedback_down", "message_id": "m1", "detail": "rating=down"}]
    env.run(model)
    assert len(env.runs()) == 1


def test_correction_forces_extraction_and_is_recorded(env):
    env.write(line(query="Na verdade, I live in Lyon", intent="greeting"))
    model = FakeChatModel([extraction(cand(quote="I live in Lyon"))])
    s = env.run(model)
    assert (model.calls, s.created) == (1, 1)
    [r] = env.runs()
    assert [(x["kind"], x["message_id"]) for x in r["signals"]] == [("correction", "m1")]


def test_feedback_down_lowers_the_score(env):
    env.write(line(), feedback=[{"chat_id": "c1", "message_id": "m1", "rating": "down"}])
    env.run(FakeChatModel([extraction(cand())]))
    [m] = env.memories()
    assert m["score"] == pytest.approx(0.935 - 0.025, abs=1e-3)


def test_low_score_is_dropped(env):
    env.write(line())
    weak = cand(type="episode", quote="live in Paris", mid="m1:a", claim="assistant", utility=1,
                content="c", situation="s", actions="a", outcome="o")
    s = env.run(FakeChatModel([extraction(weak)]))
    assert s.dropped_by_reason == {"low_score": 1} and env.memories() == []


def test_reprocess_reextracts_already_processed_segments(env):
    env.write(line())
    model = FakeChatModel([extraction(cand()), extraction(cand())])
    env.run(model)
    s = env.run(model, reprocess=True)
    assert model.calls == 2 and s.merged == 1 and len(env.memories()) == 1


def test_dry_run_writes_nothing(env):
    env.write(line())
    model = FakeChatModel([extraction(cand()), extraction(cand())])
    s = env.run(model, dry_run=True)
    assert (s.created, s.segments_processed, model.calls) == (1, 1, 1)
    assert env.memories() == [] and env.runs() == []
    assert env.run(model).created == 1


def test_thread_filter(env):
    env.write(line(thread="c1"), line(thread="c2", mid="m2"))
    model = FakeChatModel([extraction(cand())])
    s = env.run(model, thread_id="c2")
    assert (s.threads_seen, model.calls) == (1, 1)
    assert {r["thread_id"] for r in env.runs()} == {"c2"}


def test_final_pass_runs_once_and_offers_only_episode_types(env):
    env.write(line(age=timedelta(days=8)))
    episode = cand(type="episode", quote="I live in Lyon", content="Moved to Lyon", situation="s", actions="a", outcome="o")
    model = RecordingModel([extraction(), extraction(episode)])
    s = env.run(model)
    assert s.segments_processed == 2 and s.created == 1
    assert [r["final_pass"] for r in env.runs()] == [False, True]
    assert model.schemas[0].model_fields["candidates"].annotation != model.schemas[1].model_fields["candidates"].annotation
    final_type = model.schemas[1].model_fields["candidates"].annotation.__args__[0].model_fields["type"].annotation
    assert final_type.__args__ == ("episode",)
    env.run(model)
    assert model.calls == 2 and len(env.runs()) == 2


def test_final_pass_when_only_the_final_is_due_after_normal_processing(env):
    env.write(line(age=timedelta(days=2)))
    model = FakeChatModel([extraction(), extraction()])
    env.run(model)
    assert model.calls == 1
    env.run(model, now=NOW + timedelta(days=10))
    assert model.calls == 2 and [r["final_pass"] for r in env.runs()] == [False, True]


def test_duplicate_merges_evidence_and_counts_only_new_threads(env):
    env.write(line(thread="c1", mid="m1"))
    env.run(FakeChatModel([extraction(cand())]))
    env.write(line(thread="c1", mid="m1"), line(thread="c2", mid="m2", query="I live in Lyon, France"))
    s = env.run(FakeChatModel([extraction(cand(mid="m2", quote="I live in Lyon"))]))
    assert (s.merged, s.created) == (1, 0)
    [m] = env.memories()
    assert (m["seen_count"], len(m["evidence"])) == (2, 2)
    # a later message of an already-seen thread adds evidence but not seen_count
    env.write(line(thread="c1", mid="m1"), line(thread="c2", mid="m2"), line(thread="c2", mid="m3", age=timedelta(hours=3)))
    env.run(FakeChatModel([extraction(cand(mid="m3", quote="I live in Lyon"))]))
    [m] = env.memories()
    assert (m["seen_count"], len(m["evidence"])) == (2, 3)


@pytest.mark.parametrize("sim,verdict,judge_calls,rows,merged", [
    (0.95, None, 0, 1, 1),
    (0.85, "same", 1, 1, 1),
    (0.85, "unrelated", 1, 2, 0),
    (0.85, "conflicts", 1, 2, 0),
    (0.20, None, 0, 2, 0),
    (0.50, "updates", 1, 2, 0),
])
def test_reconcile_and_judge_only_inside_the_band(env, sim, verdict, judge_calls, rows, merged):
    emb = MappedEmbeddings({"User lives in Lyon": vec_at(1.0), "User lives in Nice": vec_at(sim)})
    env.write(line())
    env.run(FakeChatModel([extraction(cand())]), embeddings=emb)
    env.write(line(), line(thread="c2", mid="m2", query="I live in Nice"))
    judge = FakeChatModel([Verdict(verdict=verdict)] if verdict else [])
    s = env.run(
        FakeChatModel([extraction(cand(mid="m2", quote="I live in Nice", fields={"content": "User lives in Nice"}))]),
        judge=judge, embeddings=emb,
    )
    assert (judge.calls, s.merged, len(env.memories())) == (judge_calls, merged, rows)
    if verdict == "conflicts":
        old, = [m for m in env.memories() if m["content"] == "User lives in Lyon"]
        new, = [m for m in env.memories() if m["content"] == "User lives in Nice"]
        assert (new["status"], new["conflicts_with"], old["status"]) == ("candidate", old["memory_id"], "active")
        assert s.tokens_in == 200


def test_preference_with_same_key_becomes_a_new_version(env):
    def pref(text, mid):
        return cand(type="preference", quote=text, mid=mid, content=text, key="language")

    env.write(line(query="Answer in English"))
    env.run(FakeChatModel([extraction(pref("Answer in English", "m1"))]))
    env.write(line(query="Answer in English"), line(thread="c1", mid="m2", query="Answer in Portuguese", age=timedelta(hours=3)))
    judge = FakeChatModel([Verdict(verdict="updates")])
    s = env.run(FakeChatModel([extraction(pref("Answer in Portuguese", "m2"))]), judge=judge)
    assert (s.created, s.merged, judge.calls) == (1, 0, 1)
    rows = sorted(env.memories(type="preference"), key=lambda m: m["version"])
    assert [(m["version"], m["status"], m["content"]) for m in rows] == [
        (1, "superseded", "Answer in English"), (2, "active", "Answer in Portuguese"),
    ]
    assert rows[0]["memory_id"] == rows[1]["memory_id"]


def test_workspace_scope_lands_as_candidate_and_unknown_scope_falls_back(env):
    env.write(line())
    env.run(FakeChatModel([extraction(cand(scope="workspace"))]))
    [m] = env.memories()
    assert (m["status"], m["verified"], m["user_id"]) == ("candidate", False, None)

    env.settings = env.settings.model_copy(update={"scopes": ["user"]})
    env.write(line(thread="c2", mid="m2"))
    env.run(FakeChatModel([extraction(cand(scope="workspace", mid="m2", fields={"content": "Other fact"}))]), embeddings=FakeEmbeddings(16))
    fallback, = [m for m in env.memories() if m["content"] == "Other fact"]
    assert (fallback["scope"], fallback["status"]) == ("user", "active")


def test_skipped_lines_are_reported(env):
    env.write(line(), "{bad json", {"message_id": "x"})
    s = env.run(FakeChatModel([extraction()]))
    assert s.skipped_lines == 2


def test_dropped_candidates_are_purged_after_retention(env):
    env.write(line())
    env.run(FakeChatModel([extraction(cand(quote="I live in Nice"))]))
    assert env.runs()[0]["dropped"]
    with env.store.connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {env.store._t('memory_runs')} SET processed_at = now() - interval '100 days'")
    env.run(FakeChatModel())
    [r] = env.runs()
    assert r["dropped"] == []


def test_max_active_caps_new_rows_but_not_same_key_replacement(env):
    env.settings.types["preference"].max_active = 1

    def pref(text, mid, key):
        return cand(type="preference", quote=text, mid=mid, content=text, key=key)

    env.write(line(query="Answer in English"), line(mid="m2", query="Keep it short", age=timedelta(hours=4)))
    s = env.run(FakeChatModel([extraction(pref("Answer in English", "m1", "language"), pref("Keep it short", "m2", "style"))]))
    assert (s.created, s.dropped_by_reason) == (1, {"cap_reached": 1})
    # replacing the existing key is not growth, so it is still allowed at the cap
    env.write(line(query="Answer in English"), line(mid="m2", query="Keep it short", age=timedelta(hours=4)),
              line(mid="m3", query="Answer in Portuguese", age=timedelta(hours=3)))
    s = env.run(FakeChatModel([extraction(pref("Answer in Portuguese", "m3", "language"))]),
                judge=FakeChatModel([Verdict(verdict="updates")]))
    assert (s.created, s.dropped_by_reason) == (1, {})


def test_observed_at_is_the_newest_evidence_message_time_and_merges_only_move_it_forward(env):
    env.write(line(mid="m1", age=timedelta(days=30)), line(mid="m2", query="I live in Lyon still", age=timedelta(days=20)))
    env.run(FakeChatModel([extraction(Candidate(
        type="fact", scope="user", fields={"content": "User lives in Lyon"}, utility=5, applies_generally=True,
            assertion="stated",
        evidence=[Evidence(message_id="m1", quote="I live in Lyon", claim_source="user"),
                  Evidence(message_id="m2", quote="I live in Lyon", claim_source="user")],
    )), extraction()]))  # the second response is the thread's final pass
    [m] = env.memories()
    assert m["observed_at"] == NOW - timedelta(days=20)  # never the ingestion time (NOW)
    assert [e["observed_at"] for e in m["evidence"]] == [(NOW - timedelta(days=d)).isoformat() for d in (30, 20)]
    # a duplicate from an earlier message leaves it alone, one from a later message moves it forward
    env.write(line(mid="m1", age=timedelta(days=30)), line(mid="m2", age=timedelta(days=20)),
              line(thread="c2", mid="m3", age=timedelta(days=40)), line(thread="c3", mid="m4", age=timedelta(days=10)))
    env.run(FakeChatModel([
        extraction(cand(mid="m3", quote="I live in Lyon")), extraction(),  # c2: segment, final pass
        extraction(cand(mid="m4", quote="I live in Lyon")), extraction(),  # c3: segment, final pass
    ]))
    [m] = env.memories()
    assert (m["observed_at"], len(m["evidence"])) == (NOW - timedelta(days=10), 4)


def _dated(**kw):
    c = cand()
    return c.model_copy(update=kw)


def test_the_extractors_assertion_is_stored(env):
    env.settings.guardrails.allow_inferred = True
    env.write(line(mid="m1"))
    env.run(FakeChatModel([extraction(_dated(assertion="inferred")), extraction()]))
    [m] = env.memories()
    assert m["assertion"] == "inferred"


def test_valid_until_is_chosen_text_then_type_ttl_then_durability_ttl(env):
    seen = NOW - timedelta(hours=5)
    env.write(line(mid="m1"))
    env.run(FakeChatModel([extraction(_dated(durability="temporary")), extraction()]))
    [m] = env.memories()
    assert (m["durability"], m["valid_from"], m["valid_until"]) == ("temporary", None, seen + timedelta(days=30))

    env.write(line(thread="c2", mid="m2", user="u2"))
    text_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
    env.run(FakeChatModel([extraction(_dated(durability="temporary", valid_until=text_date)), extraction()]))
    [m2] = env.memories(user_id="u2")
    assert m2["valid_until"] == text_date  # an explicit date wins over any config

    env.write(line(thread="c3", mid="m3", user="u3"))
    env.run(FakeChatModel([extraction(_dated(durability="stable")), extraction()]))
    [m3] = env.memories(user_id="u3")
    assert m3["valid_until"] is None


def test_duplicate_merge_renews_valid_until(env):
    env.write(line(thread="c1", mid="m1", age=timedelta(days=20)))
    env.run(FakeChatModel([extraction(_dated(durability="temporary")), extraction()]))
    [m] = env.memories()
    first = m["valid_until"]
    env.write(line(thread="c1", mid="m1", age=timedelta(days=20)), line(thread="c2", mid="m2", age=timedelta(days=2)))
    env.run(FakeChatModel([extraction(_dated(durability="temporary", mid="m2")), extraction()]))
    [m] = env.memories()
    assert m["valid_until"] == NOW - timedelta(days=2) + timedelta(days=30) > first
    env.write(line(thread="c1", mid="m1", age=timedelta(days=20)), line(thread="c2", mid="m2", age=timedelta(days=2)),
              line(thread="c3", mid="m3", age=timedelta(days=1)))
    env.run(FakeChatModel([extraction(_dated(durability="stable", mid="m3")), extraction()]))
    [m] = env.memories()
    assert m["valid_until"] is None  # a claim that never expires wins


def test_duplicate_of_a_stale_memory_renews_it_and_it_is_searchable_again(env):
    def search(now):
        with env.store.connect() as conn, conn.cursor() as cur:
            return env.store.search(
                cur, query_embedding=env.embeddings.embed_query("User lives in Lyon"), workspace_id="ws", user_id="u1", now=now,
                type="fact",
            )

    env.write(line(thread="c1", mid="m1", age=timedelta(days=40)))
    env.run(FakeChatModel([extraction(_dated(durability="temporary")), extraction()]))
    [m] = env.memories()
    assert m["valid_until"] == NOW - timedelta(days=10) and search(NOW) == []  # expired: out of search, still active
    assert m["status"] == "active"

    env.write(line(thread="c1", mid="m1", age=timedelta(days=40)), line(thread="c2", mid="m2", age=timedelta(days=1)))
    env.run(FakeChatModel([extraction(_dated(durability="temporary", mid="m2")), extraction()]))
    [renewed] = env.memories()  # merged, not duplicated
    assert renewed["id"] == m["id"] and renewed["seen_count"] == 2
    assert renewed["valid_until"] == NOW - timedelta(days=1) + timedelta(days=30)
    assert [r["id"] for r in search(NOW)] == [m["id"]]


def _city(content, quote, **kw):
    return cand(fields={"content": content}, quote=quote, **kw)


def _city_then_update(env, scope="user", approve_first=False):
    emb = MappedEmbeddings({"Lives in Lyon": vec_at(1.0), "Lives in Paris": vec_at(0.85)})
    env.write(line(query="I live in Lyon"))
    first = _city("Lives in Lyon", "I live in Lyon", scope=scope).model_copy(update={
        "valid_until": datetime(2027, 12, 31, tzinfo=timezone.utc), "durability": "temporary"})
    env.run(FakeChatModel([extraction(first), extraction()]), embeddings=emb)
    if approve_first:
        [m] = env.memories()
        with env.store.connect() as conn, conn.cursor() as cur:
            env.store.approve(cur, m["id"], reviewed_by="t")
    env.write(line(query="I live in Lyon"),
              line(thread="c2", mid="m2", query="I moved to Paris", age=timedelta(hours=2)))
    second = _city("Lives in Paris", "I moved to Paris", scope=scope, mid="m2").model_copy(
        update={"durability": "stable"})
    judge = FakeChatModel([Verdict(verdict="updates")])
    summary = env.run(FakeChatModel([extraction(second), extraction()]), judge=judge, embeddings=emb)
    assert judge.calls == 1
    return summary


def test_updates_verdict_makes_a_new_version_that_takes_its_own_time_fields(env):
    s = _city_then_update(env)
    assert (s.created, s.merged) == (1, 0)
    rows = sorted(env.memories(type="fact"), key=lambda m: m["version"])
    assert [(m["version"], m["status"], m["conflicts_with"]) for m in rows] == [(1, "superseded", None), (2, "active", None)]
    old, new = rows
    assert old["memory_id"] == new["memory_id"] and new["content"] == "Lives in Paris"
    assert (old["valid_until"], old["durability"], old["assertion"]) == (datetime(2027, 12, 31, tzinfo=timezone.utc), "temporary", "stated")
    # the old window must not leak into the new version: everything comes from the new candidate
    assert (new["valid_until"], new["valid_from"], new["durability"], new["assertion"]) == (None, None, "stable", "stated")
    assert new["observed_at"] == NOW - timedelta(hours=2)
    assert len(new["evidence"]) == 2  # evidence still accumulates


def test_an_approved_workspace_memory_is_protected_so_an_update_becomes_a_conflict_for_review(env):
    _city_then_update(env, scope="workspace", approve_first=True)  # approval verifies the row
    old, new = sorted(env.memories(type="fact"), key=lambda m: m["created_at"])
    assert (old["status"], old["verified"], new["status"], new["conflicts_with"]) == (
        "active", True, "candidate", old["memory_id"])
    with env.store.connect() as conn, conn.cursor() as cur:
        env.store.approve(cur, new["id"], reviewed_by="t", resolve="replace")
    old, new = sorted(env.memories(type="fact"), key=lambda m: m["created_at"])
    assert (old["status"], new["status"], new["content"]) == ("archived", "active", "Lives in Paris")


# --- conservative defaults ------------------------------------------------------

def test_an_inferred_candidate_is_dropped_by_default_and_recorded(env):
    env.write(line())
    s = env.run(FakeChatModel([extraction(cand(assertion="inferred"))]))
    assert env.memories() == [] and s.dropped_by_reason == {"inferred": 1}
    [r] = env.runs()
    assert [d["reason"] for d in r["dropped"]] == ["inferred"]


def test_an_inferred_candidate_follows_the_normal_path_when_allowed(env):
    env.settings.guardrails.allow_inferred = True
    env.write(line())
    s = env.run(FakeChatModel([extraction(cand(assertion="inferred"))]))
    assert (s.created, s.dropped_by_reason) == (1, {})
    [m] = env.memories()
    assert m["assertion"] == "inferred"


# --- no automatic context Episode ---------------------------------------------

def test_a_segment_with_claims_stores_only_those_claims(env):
    env.write(line(query="I live in Lyon and I like cheese"))
    two = extraction(cand(quote="I live in Lyon"), cand(quote="like cheese", fields={"content": "User likes cheese"}))
    s = env.run(FakeChatModel([two]), embeddings=FakeEmbeddings(16))
    assert (s.candidates_proposed, s.created) == (2, 2)
    assert {m["type"] for m in env.memories()} == {"fact"}
    assert all(m["links"] == [] for m in env.memories())
    assert "no_claims" not in s.dropped_by_reason


def test_an_extraction_has_no_context_field():
    from memhub.pipeline.extract import Extraction

    assert "context" not in Extraction.model_fields


def test_an_episode_candidate_with_all_fields_is_stored_like_any_memory(env):
    env.write(line())
    episode = cand(type="episode", fields={
        "content": "Was shown two flats and chose one", "situation": "Looking for a flat",
        "actions": "Viewed two flats", "outcome": "Chose the second"})
    s = env.run(FakeChatModel([extraction(episode)]))
    assert (s.candidates_proposed, s.created) == (1, 1)
    [m] = env.memories(type="episode")
    assert m["payload"]["outcome"] == "Chose the second" and m["links"] == []




# --- profile slots ----------------------------------------------------------------

def _slot(key, content, quote, type="profile", **kw):
    return cand(type=type, fields={"key": key, "content": content}, quote=quote, **kw)


def test_an_unknown_key_is_dropped_as_unknown_key(env):
    env.write(line(query="I am Brazilian"))
    s = env.run(FakeChatModel([extraction(_slot("hobby", "Is Brazilian", "I am Brazilian"))]))
    assert env.memories() == [] and s.dropped_by_reason == {"unknown_key": 1}


def test_the_same_identity_said_twice_is_one_active_row_with_accumulated_evidence(env):
    env.write(line(thread="c1", mid="m1", query="I am Brazilian"))
    env.run(FakeChatModel([extraction(_slot("nationality", "Is Brazilian", "I am Brazilian"))]))
    env.write(line(thread="c1", mid="m1", query="I am Brazilian"), line(thread="c2", mid="m2", query="Sou Brazilian, aqui"))
    s = env.run(FakeChatModel([extraction(_slot("nationality", "is  brazilian", "Sou Brazilian", mid="m2"))]))
    assert (s.created, s.merged) == (0, 1)
    [m] = env.memories(type="profile")
    assert (m["status"], m["version"], m["seen_count"], len(m["evidence"])) == ("active", 1, 2, 2)


def test_a_new_city_supersedes_the_city_row_and_keeps_the_old_version(env):
    env.write(line(query="I live in Grenoble"))
    env.run(FakeChatModel([extraction(_slot("city", "Lives in Grenoble", "I live in Grenoble"))]))
    env.write(line(query="I live in Grenoble"), line(thread="c2", mid="m2", query="I moved to Paris", age=timedelta(hours=2)))
    judge = FakeChatModel([Verdict(verdict="updates")])
    s = env.run(FakeChatModel([extraction(_slot("city", "Lives in Paris", "I moved to Paris", mid="m2"))]), judge=judge)
    assert (s.created, s.merged, judge.calls) == (1, 0, 1)  # no similarity search; the judge decides a slot
    rows = sorted(env.memories(type="profile"), key=lambda m: m["version"])
    assert [(m["version"], m["status"], m["content"]) for m in rows] == [
        (1, "superseded", "Lives in Grenoble"), (2, "active", "Lives in Paris")]
    assert rows[0]["memory_id"] == rows[1]["memory_id"]


def test_different_keys_of_a_profile_do_not_replace_each_other(env):
    env.write(line(query="I am Brazilian and I live in Grenoble"))
    env.run(FakeChatModel([extraction(
        _slot("nationality", "Is Brazilian", "I am Brazilian"), _slot("city", "Lives in Grenoble", "I live in Grenoble"))]))
    assert sorted(m["payload"]["key"] for m in env.memories(type="profile")) == ["city", "nationality"]


def test_a_keyless_profile_is_stored_and_never_replaces_a_slot(env):
    env.settings.types["profile"].strict_keys = False
    env.write(line(query="Tenho CNH brasileira e faço doutorado"))
    env.run(FakeChatModel([extraction(
        cand(type="profile", fields={"content": "Faz doutorado na França"}, quote="faço doutorado"),
        _slot("documents", "Tem CNH brasileira", "Tenho CNH brasileira"),
    )]))
    rows = env.memories(type="profile")
    assert sorted(r["content"] for r in rows) == ["Faz doutorado na França", "Tem CNH brasileira"]
    assert sorted(r["payload"].get("key") or "" for r in rows) == ["", "documents"]


def test_an_unreadable_verdict_is_sampled_again_before_it_lands_in_review(env):
    from tests.fakes import PARSE_ERROR

    emb = MappedEmbeddings({"User lives in Lyon": vec_at(1.0), "User lives in Nice": vec_at(0.85)})
    env.write(line())
    env.run(FakeChatModel([extraction(cand())]), embeddings=emb)
    env.write(line(), line(thread="c2", mid="m2", query="I live in Nice"))
    env.settings = env.settings.model_copy(update={"extraction": env.settings.extraction.model_copy(update={"retries": 1})})
    judge = FakeChatModel([PARSE_ERROR, Verdict(verdict="unrelated")])
    s = env.run(
        FakeChatModel([extraction(cand(mid="m2", quote="I live in Nice", fields={"content": "User lives in Nice"}))]),
        judge=judge, embeddings=emb,
    )
    assert (judge.calls, s.created, s.conflicts_opened) == (2, 1, 0)


def test_an_episode_the_judge_does_not_confirm_is_dropped(env):
    from memhub.pipeline.verify import EpisodeCheck

    env.write(line())
    env.settings = env.settings.model_copy(update={"ingestion": env.settings.ingestion.model_copy(update={"verify_episodes": True})})
    episode = cand(type="episode", fields={
        "content": "Foi à prefeitura", "situation": "Precisava do titre", "actions": "Foi à prefeitura", "outcome": "Faltou um documento"})
    s = env.run(FakeChatModel([extraction(episode)]), judge=FakeChatModel([EpisodeCheck(told_by_user=False)]))
    assert (s.created, s.dropped_by_reason) == (0, {"unverified_episode": 1})
    s = env.run(FakeChatModel([extraction(episode)]), judge=FakeChatModel([EpisodeCheck(told_by_user=True)]), reprocess=True)
    assert s.created == 1


def test_a_claim_the_quote_says_about_someone_else_is_dropped(env):
    from memhub.pipeline.verify import ClaimCheck

    env.write(line())
    env.settings = env.settings.model_copy(update={"ingestion": env.settings.ingestion.model_copy(update={"verify_claims": True})})
    # only the claim with nothing in its quote is put to the judge
    two = extraction(cand(fields={"content": "User lives in Lyon"}), cand(fields={"content": "Has 68 years"}))
    s = env.run(FakeChatModel([two]), judge=FakeChatModel([ClaimCheck(supported=[False])], unrelated_by_default=True),
                embeddings=FakeEmbeddings(16))
    assert (s.created, s.dropped_by_reason) == (1, {"unsupported_claim": 1})


def test_a_remember_command_is_saved_even_when_background_extraction_would_refuse_it(env):
    env.write(line(query="Onde tem padaria?"), line(thread="c1", mid="m2", query="/remember Sou alérgico a nozes", age=timedelta(hours=4)))
    background = extraction()  # nothing worth keeping in the ordinary conversation
    told = extraction(cand(mid="m2", quote="Sou alérgico a nozes", fields={"content": "Precisa evitar nozes"}, utility=1))
    s = env.run(FakeChatModel([background]), judge=FakeChatModel([told], unrelated_by_default=True), embeddings=FakeEmbeddings(16))
    [m] = env.memories()
    assert (s.created, m["content"], m["created_by"], m["status"]) == (1, "Precisa evitar nozes", "remember", "active")
    assert m["evidence"][0]["message_id"] == "m2" and m["payload"]["content"] == m["content"]


def test_a_fact_taken_from_a_question_is_dropped_by_the_judge_only_when_the_quote_was_a_question(env):
    from memhub.pipeline.verify import AskedOnly

    env.write(line(query="Tem desconto no cinema para estudantes?"))
    env.settings = env.settings.model_copy(update={"ingestion": env.settings.ingestion.model_copy(update={"verify_claims": True})})
    q = cand(quote="Tem desconto no cinema para estudantes", fields={"content": "Tem desconto no cinema"})
    s = env.run(FakeChatModel([extraction(q)]), judge=FakeChatModel([AskedOnly(asked_only=[True])], unrelated_by_default=True))
    assert (s.created, s.dropped_by_reason) == (0, {"question_only": 1})


def test_remember_save_this_case_reads_the_messages_above_and_stores_a_case(env):
    from memhub.config import TypeConfig

    env.settings.types["case"] = TypeConfig(**{"class": "memhub.types:Case"}, extract=False, on_remember=True, scopes=["user"],
                                        content_template="Symptom: {symptom} Root cause: {root_cause} Action: {action} Outcome: {outcome}")
    env.write(
        line(mid="m1", query="A bomba faz um barulho de vibração"), line(thread="c1", mid="m2", query="Já troquei o rolamento", age=timedelta(hours=4)),
        line(thread="c1", mid="m3", query="Era o desalinhamento do eixo, realinhei e parou", age=timedelta(hours=3)),
        line(thread="c1", mid="m4", query="/remember save this case", age=timedelta(hours=2)),
    )
    seen = []

    class Spy(FakeChatModel):
        def with_structured_output(self, schema, **kw):
            runnable = super().with_structured_output(schema, **kw)
            invoke = runnable.invoke
            runnable.invoke = lambda messages, **k: (seen.append(messages[1][1]), invoke(messages, **k))[1]
            return runnable

    case = cand(type="case", mid="m3", quote="Era o desalinhamento do eixo, realinhei e parou", fields={
        "content": "x", "symptom": "Vibração e barulho na bomba", "root_cause": "Desalinhamento do eixo",
        "action": "Realinhou o eixo", "outcome": "A vibração parou"})
    s = env.run(FakeChatModel([extraction()]), judge=Spy([extraction(case)], unrelated_by_default=True), embeddings=FakeEmbeddings(16))
    [m] = env.memories(type="case")
    assert "A bomba faz um barulho" in seen[0] and "Já troquei o rolamento" in seen[0]  # the messages above are read with the command
    assert m["created_by"] == "remember" and m["payload"]["root_cause"] == "Desalinhamento do eixo"
    assert m["content"] == "Symptom: Vibração e barulho na bomba Root cause: Desalinhamento do eixo Action: Realinhou o eixo Outcome: A vibração parou"


def test_a_project_defines_its_own_remember_schema_and_instructions(env):
    from memhub.config import TypeConfig
    from memhub.types import MemoryBase

    class Incident(MemoryBase):
        equipment: str
        fault_code: str
        fix: str

    env.registry.register("incident", Incident)
    env.settings.types["incident"] = TypeConfig(**{"class": "tests.test_ingest_e2e:Incident"}, extract=False, on_remember=True,
                                                scopes=["user"], content_template="{equipment} fault {fault_code}: {fix}")
    env.settings.extraction.remember_instructions = "Only incidents."
    env.write(line(mid="m1", query="Pump P-1 shows E42"), line(thread="c1", mid="m2", query="/remember save this", age=timedelta(hours=2)))
    seen = []

    class Spy(FakeChatModel):
        def with_structured_output(self, schema, **kw):
            runnable = super().with_structured_output(schema, **kw)
            invoke = runnable.invoke
            runnable.invoke = lambda messages, **k: (seen.append(messages[0][1]), invoke(messages, **k))[1]
            return runnable

    inc = cand(type="incident", mid="m1", quote="Pump P-1 shows E42", fields={"content": "x", "equipment": "P-1", "fault_code": "E42", "fix": "reset"})
    env.run(FakeChatModel([extraction()]), judge=Spy([extraction(inc)], unrelated_by_default=True), embeddings=FakeEmbeddings(16))
    [m] = env.memories(type="incident")
    assert m["content"] == "P-1 fault E42: reset" and "Only incidents." in seen[0] and "equipment" in seen[0]


def test_two_workers_on_the_same_thread_do_not_write_it_twice(env):
    import threading
    import time

    class Slow(FakeChatModel):
        def with_structured_output(self, schema, **kw):
            runnable = super().with_structured_output(schema, **kw)
            invoke = runnable.invoke
            runnable.invoke = lambda *a, **k: (time.sleep(1.0), invoke(*a, **k))[1]
            return runnable

    env.write(line())
    results = []
    workers = [threading.Thread(target=lambda: results.append(env.run(Slow([extraction(cand())])))) for _ in range(2)]
    [w.start() for w in workers]
    [w.join() for w in workers]
    assert sorted((r.created, r.threads_locked) for r in results) == [(0, 1), (1, 0)]
    assert len(env.memories()) == 1
