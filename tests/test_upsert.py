"""Upsert and conflict handling over time (ticket 34): scenario tests S1-S15 and a seeded randomised test.

Real Postgres, a fake extractor and judge, message timestamps and `now` under the test's control. The ledger
invariants are checked after every ingest."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from memhub.config import SeedArea, TypeConfig
from memhub.pipeline.extract import AreaRef
from memhub.pipeline.ingest import ingest_source
from memhub.pipeline.reconcile import Verdict
from memhub.service import Actor, MemoryService
from memhub.sources.base import Interaction
from memhub.store import MemoryStore
from memhub.types import term_content
from tests.fakes import FakeChatModel, FakeEmbeddings, LookupEmbeddings, ScriptedJudge, extraction, vec_at
from tests.test_ingest_e2e import cand

AUG10 = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
SEP10 = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
OCT10 = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
ADMIN = Actor(id="admin-1", roles=["workspace_admin"])
USER = "u1"


class ListSource:
    def __init__(self, messages):
        self.messages, self.skipped = messages, 0

    def read(self):
        return iter(self.messages)


def msg(thread, mid, text, when, user=USER):
    return Interaction(
        thread_id=thread, user_id=user, workspace_id="ws", message_id=mid, role="user", content=text,
        timestamp=when, trace_id=f"tr-{mid}",
    )


def slot(key, content, quote, mid, *, type="profile", **update):
    return cand(type=type, quote=quote, mid=mid, fields={"content": content, "key": key}).model_copy(update=update)


def fact(content, quote, mid, area="documents", **update):
    return cand(type="fact", quote=quote, mid=mid, fields={"content": content}).model_copy(
        update={"areas": [AreaRef(existing=area)], **update})


def term(quote, mid, name="CNH", aliases=("carteira de motorista",)):
    fields = {"content": term_content(name, None, list(aliases)), "term": name, "aliases": list(aliases)}
    return cand(type="term", scope="workspace", quote=quote, mid=mid, fields=fields)


# --- the ledger invariants -------------------------------------------------------------------------------


def check_invariants(store: MemoryStore, owner: str | None = USER) -> None:
    """The ledger of one owner (a user id; None = the workspace) must be consistent."""
    mem = store._t("memory")
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {mem} WHERE user_id IS NOT DISTINCT FROM %s AND type <> 'area' ORDER BY memory_id, version", (owner,))
        rows = cur.fetchall()
        cur.execute(f"SELECT memory_id FROM {mem} WHERE type = 'area' AND status = 'active'")
        live_areas = {str(r["memory_id"]) for r in cur.fetchall()}
    by_memory: dict[Any, list[dict]] = {}
    for r in rows:
        by_memory.setdefault(r["memory_id"], []).append(r)
    slots: set[tuple] = set()
    for memory_id, versions_ in by_memory.items():
        numbers = [v["version"] for v in versions_]
        assert numbers == list(range(1, len(numbers) + 1)), f"versions of {memory_id} are not contiguous: {numbers}"
        assert sum(v["status"] == "active" for v in versions_) <= 1, f"two active versions of {memory_id}"
    for r in rows:
        ids = [e["message_id"] for e in r["evidence"] if e.get("message_id")]
        assert len(ids) == len(set(ids)), f"evidence duplicated in {r['id']}: {ids}"
        if r["status"] == "active":
            assert any(e.get("quote") for e in r["evidence"]), f"active row {r['id']} has no quote"
            stamps = [datetime.fromisoformat(e["observed_at"]) for e in r["evidence"] if e.get("observed_at")]
            assert r["observed_at"] == max(stamps), f"observed_at of {r['id']} is not its newest evidence"
            if r["payload"].get("key") is not None:
                slot_id = (r["type"], r["payload"]["key"])
                assert slot_id not in slots, f"two active rows for the slot {slot_id}"
                slots.add(slot_id)
        if r["status"] in ("active", "candidate"):
            for link in r["links"]:
                assert link["memory_id"] in live_areas, f"{r['id']} links to a missing or archived area"
    superseded = [r for r in rows if r["status"] == "superseded"]
    if superseded:
        with store.connect() as conn, conn.cursor() as cur:
            for r in superseded:
                assert r["id"] in {h["id"] for h in store.history(cur, r["memory_id"])}, f"{r['id']} is not in history"


# --- the harness -----------------------------------------------------------------------------------------


@dataclass
class Ledger:
    store: MemoryStore
    settings: Any
    registry: Any
    embeddings: Any
    judge_calls: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.service = MemoryService(
            store=self.store, settings=self.settings, registry=self.registry, embeddings=self.embeddings)

    def ingest(self, thread, messages, candidates, verdicts=(), *, now=None, reprocess=False, owner=USER):
        now = now or max(m.timestamp for m in messages) + timedelta(hours=2)
        judge = ScriptedJudge([Verdict(verdict=v) if isinstance(v, str) else v for v in verdicts])
        summary = ingest_source(
            store=self.store, settings=self.settings, registry=self.registry, source=ListSource(messages),
            source_name="scenario", extractor=FakeChatModel([extraction(*candidates), extraction()]), judge=judge,
            embeddings=self.embeddings, now=now, reprocess=reprocess,
        )
        assert summary.failed_segments == 0, "a segment failed"
        assert judge.asked <= len(candidates)  # at most once per candidate
        assert not judge.queue, f"verdicts left unused: {judge.queue}"
        self.judge_calls.append(judge.asked)
        check_invariants(self.store, owner)
        return summary

    def say(self, thread, text, when, candidates, verdicts=(), **kw):
        """One user message in its own thread, and what the fake extractor returns for it."""
        return self.ingest(thread, [msg(thread, f"{thread}-1", text, when)], candidates, verdicts, **kw)

    def rows(self, type="profile", key=None, owner=USER, status=None):
        with self.store.connect() as conn, conn.cursor() as cur:
            rows = self.store.list_memories(cur, type=type, user_id=owner)
        rows = [r for r in rows if r["payload"].get("key") == key or key is None]
        rows = [r for r in rows if status is None or r["status"] == status]
        return sorted(rows, key=lambda r: (r["created_at"], r["version"]))

    def slot(self, key, type="profile"):
        return sorted(self.rows(type, key), key=lambda r: (str(r["memory_id"]), r["version"]))

    def snapshot(self):
        with self.store.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id, version, status, seen_count, evidence, observed_at, valid_until FROM "
                        f"{self.store._t('memory')} ORDER BY id")
            return cur.fetchall()


@pytest.fixture
def ledger(store, settings, registry):
    settings.types["profile"].immutable_keys = ["nationality"]
    return Ledger(store, settings, registry, FakeEmbeddings(16))


def versions(rows):
    return [(r["version"], r["status"], r["content"]) for r in rows]


# --- S1-S4: age and city over time -----------------------------------------------------------------------

AGE18 = "Born around 2008 (18 on 2026-08-10)"
AGE19 = "Born around 2007 (19 on 2026-09-10)"


def first_day(ledger):
    return ledger.say("a", "I'm 18, I live in Grenoble", AUG10, [
        slot("age", AGE18, "I'm 18", "a-1"), slot("city", "Lives in Grenoble", "I live in Grenoble", "a-1")])


def test_s1_a_new_age_updates_the_age_slot_and_leaves_the_city_alone(ledger):
    first_day(ledger)
    s = ledger.say("b", "I'm 19 now", SEP10, [slot("age", AGE19, "I'm 19 now", "b-1")], ["updates"])
    v1, v2 = ledger.slot("age")
    assert versions([v1, v2]) == [(1, "superseded", AGE18), (2, "active", AGE19)]
    assert v1["memory_id"] == v2["memory_id"] and v1["evidence"][0]["quote"] == "I'm 18"
    assert v2["evidence"][-1]["quote"] == "I'm 19 now" and v2["observed_at"] == SEP10
    [city] = ledger.slot("city")
    assert (city["version"], city["status"], len(city["evidence"])) == (1, "active", 1)
    assert (s.created, s.conflicts_opened, ledger.judge_calls[-1]) == (1, 0, 1)
    assert len(ledger.rows()) == 3  # nothing was duplicated


def test_s2_an_older_statement_ingested_later_is_outdated(ledger):
    ledger.say("b", "I'm 19 now", SEP10, [slot("age", AGE19, "I'm 19 now", "b-1")])
    s = first_day(ledger)
    [age] = ledger.slot("age")
    assert (age["version"], age["status"], age["content"], age["observed_at"]) == (1, "active", AGE19, SEP10)
    assert (s.outdated, s.dropped_by_reason) == (1, {"outdated": 1}) and ledger.judge_calls[-1] == 0
    [city] = ledger.slot("city")  # the other slot of that old message is still new information
    assert city["content"] == "Lives in Grenoble"


def test_s3_the_same_city_a_month_later_only_renews_it(ledger):
    first_day(ledger)
    s = ledger.say("b", "I live in Grenoble", SEP10, [slot("city", "Lives in Grenoble", "I live in Grenoble", "b-1")])
    [city] = ledger.slot("city")
    assert (city["version"], city["seen_count"], len(city["evidence"])) == (1, 2, 2)
    assert (city["observed_at"], city["valid_until"]) == (SEP10, SEP10 + timedelta(days=365))
    assert (s.merged, s.created, ledger.judge_calls[-1]) == (1, 0, 0)


def test_the_same_text_never_reaches_the_judge_however_it_is_spelled(ledger):
    first_day(ledger)
    ledger.say("b", "I live in grenoble", SEP10, [slot("city", "  lives   in GRENOBLE ", "I live in grenoble", "b-1")])
    assert ledger.judge_calls[-1] == 0 and len(ledger.slot("city")) == 1


def test_s4_a_new_city_is_an_update_and_the_old_one_stays_in_history(ledger):
    first_day(ledger)
    ledger.say("b", "I moved to Paris", SEP10, [slot("city", "Lives in Paris", "I moved to Paris", "b-1")], ["updates"])
    v1, v2 = ledger.slot("city")
    assert versions([v1, v2]) == [(1, "superseded", "Lives in Grenoble"), (2, "active", "Lives in Paris")]
    history = ledger.service.history(ADMIN, v1["memory_id"])
    assert [(h["version"], h["content"], h["created_by"]) for h in history] == [
        (1, "Lives in Grenoble", "extractor"), (2, "Lives in Paris", "extractor")]
    assert history[0]["observed_at"] == AUG10 and history[0]["evidence"][0]["quote"] == "I live in Grenoble"


# --- S5-S7: immutable keys and `extends` -----------------------------------------------------------------


def brazilian(ledger):
    ledger.say("a", "Sou brasileira", AUG10, [slot("nationality", "Is Brazilian", "Sou brasileira", "a-1")])


@pytest.mark.parametrize("resolution", ["replace", "keep_both", "keep_old"])
def test_s5_an_immutable_key_never_updates_it_becomes_a_conflict_for_review(ledger, resolution):
    brazilian(ledger)
    s = ledger.say("b", "sou portuguesa", SEP10, [slot("nationality", "Is Portuguese", "sou portuguesa", "b-1")], ["updates"])
    old = ledger.rows(key="nationality", status="active")[0]
    [pending] = ledger.rows(key="nationality", status="candidate")
    assert (old["content"], old["version"]) == ("Is Brazilian", 1)
    assert pending["conflicts_with"] == old["memory_id"] and s.conflicts_opened == 1
    # the same statement again waits in the same candidate instead of piling up
    s = ledger.say("c", "sou portuguesa", OCT10, [slot("nationality", "Is Portuguese", "sou portuguesa", "c-1")])
    [pending] = ledger.rows(key="nationality", status="candidate")
    assert (len(pending["evidence"]), s.conflicts_opened, s.merged, ledger.judge_calls[-1]) == (2, 0, 1, 0)

    ledger.service.approve(ADMIN, pending["id"], resolve=resolution)
    check_invariants(ledger.store)
    [active] = ledger.rows(key="nationality", status="active")
    if resolution == "replace":
        assert active["content"] == "Is Portuguese" and active["verified"]
        assert ledger.rows(key="nationality", status="archived")[0]["content"] == "Is Brazilian"
    elif resolution == "keep_old":
        assert active["content"] == "Is Brazilian"
    else:  # one slot holds one row: both values become one new version with both quotes
        assert (active["memory_id"], active["version"]) == (old["memory_id"], 2)
        assert active["content"] == "Is Brazilian; Is Portuguese" and active["verified"]
        assert [e["quote"] for e in active["evidence"]] == ["Sou brasileira", "sou portuguesa", "sou portuguesa"]


def test_s6_also_portuguese_extends_the_nationality(ledger):
    brazilian(ledger)
    verdict = Verdict(verdict="extends", content="Is Brazilian and Portuguese")
    s = ledger.say("b", "também sou portuguesa", SEP10, [slot("nationality", "Is Portuguese", "também sou portuguesa", "b-1")], [verdict])
    v1, v2 = ledger.slot("nationality")
    assert versions([v1, v2]) == [(1, "superseded", "Is Brazilian"), (2, "active", "Is Brazilian and Portuguese")]
    assert [e["quote"] for e in v2["evidence"]] == ["Sou brasileira", "também sou portuguesa"]
    assert (s.extends_applied, s.conflicts_opened, v2["payload"]["content"]) == (1, 0, "Is Brazilian and Portuguese")


def test_an_extension_that_uses_other_words_than_the_two_statements_is_not_trusted(ledger):
    brazilian(ledger)
    verdict = Verdict(verdict="extends", content="Is Brazilian and Portuguese and a citizen of Mars")
    s = ledger.say("b", "também sou portuguesa", SEP10, [slot("nationality", "Is Portuguese", "também sou portuguesa", "b-1")], [verdict])
    assert (s.extends_applied, s.conflicts_opened) == (0, 1) and len(ledger.rows(key="nationality", status="active")) == 1


def test_s7_a_daughter_extends_the_family_statement(ledger):
    ledger.say("a", "I arrived with my French husband", AUG10,
               [slot("family", "Arrived with a French husband", "I arrived with my French husband", "a-1")])
    verdict = Verdict(verdict="extends", content="Arrived with a French husband and has a daughter aged 5")
    ledger.say("b", "my daughter is 5", SEP10, [slot("family", "Has a daughter aged 5", "my daughter is 5", "b-1")], [verdict])
    v1, v2 = ledger.slot("family")
    assert versions([v1, v2]) == [
        (1, "superseded", "Arrived with a French husband"),
        (2, "active", "Arrived with a French husband and has a daughter aged 5")]
    assert len(ledger.slot("family")) == 2  # one memory, two versions: not two rows, not a replacement


# --- S8-S10: verified rows, stale rows, inferred vs stated -----------------------------------------------


def test_s8_a_verified_city_is_protected_and_replace_keeps_it_in_history(ledger):
    first_day(ledger)
    [city] = ledger.slot("city")
    ledger.service.edit(Actor(id=USER, roles=[]), city["memory_id"], {"content": "Lives in Lyon"})
    s = ledger.say("b", "I live in Paris", SEP10, [slot("city", "Lives in Paris", "I live in Paris", "b-1")])
    assert (s.conflicts_opened, ledger.judge_calls[-1]) == (1, 0)  # a verified row needs no judge
    [active] = ledger.rows(key="city", status="active")
    [pending] = ledger.rows(key="city", status="candidate")
    assert (active["content"], active["version"], active["verified"]) == ("Lives in Lyon", 2, True)
    assert pending["content"] == "Lives in Paris" and pending["conflicts_with"] == active["memory_id"]
    ledger.service.approve(ADMIN, pending["id"], resolve="replace")
    check_invariants(ledger.store)
    [now_active] = ledger.rows(key="city", status="active")
    assert now_active["content"] == "Lives in Paris"
    history = ledger.service.history(ADMIN, active["memory_id"])
    assert [(h["version"], h["content"]) for h in history] == [(1, "Lives in Grenoble"), (2, "Lives in Lyon")]
    # and the verified row accepts the same value as evidence
    ledger.say("c", "I live in Paris", OCT10, [slot("city", "Lives in Paris", "I live in Paris", "c-1")])
    assert ledger.rows(key="city", status="active")[0]["seen_count"] == 2


STUDENT = "Has a student visa"


def temporary(content, quote, mid):
    return slot("residence_status", content, quote, mid, durability="temporary")


@pytest.mark.parametrize("same", [False, True])
def test_s9_a_stale_row_is_superseded_by_a_new_value_and_renewed_by_the_same_one(ledger, same):
    ledger.say("a", "I have a student visa", AUG10, [temporary(STUDENT, "I have a student visa", "a-1")])
    [row] = ledger.slot("residence_status")
    assert row["valid_until"] == AUG10 + timedelta(days=30)
    if same:
        ledger.say("b", "I have a student visa", OCT10, [temporary(STUDENT, "I have a student visa", "b-1")])
        [row] = ledger.slot("residence_status")
        assert (row["version"], row["valid_until"], row["observed_at"]) == (1, OCT10 + timedelta(days=30), OCT10)
    else:
        ledger.say("b", "I have a work visa", OCT10, [temporary("Has a work visa", "I have a work visa", "b-1")])
        v1, v2 = ledger.slot("residence_status")
        assert versions([v1, v2]) == [(1, "superseded", STUDENT), (2, "active", "Has a work visa")]
    assert ledger.judge_calls[-1] == 0  # a stale row needs no judge


def test_s10_an_inferred_statement_never_supersedes_a_stated_one(ledger):
    ledger.settings.guardrails.allow_inferred = True
    first_day(ledger)
    s = ledger.say("b", "I went to the Paris prefecture", SEP10, [
        slot("city", "Lives in Paris", "I went to the Paris prefecture", "b-1", assertion="inferred")])
    [city] = ledger.slot("city")
    assert (city["content"], city["version"], len(city["evidence"])) == ("Lives in Grenoble", 1, 1)
    assert (s.dropped_by_reason, ledger.judge_calls[-1]) == ({"inferred": 1}, 0)
    # a stated statement may replace an inferred row
    ledger.say("c", "I live in Nice", SEP10 + timedelta(days=1), [
        slot("work", "Works as a nurse", "I live in Nice", "c-1", assertion="inferred")])
    ledger.say("d", "I work as a nurse in Nice", OCT10, [slot("work", "Is a nurse in Nice", "I work as a nurse in Nice", "d-1")], ["updates"])
    v1, v2 = ledger.slot("work")
    assert (v1["assertion"], v1["status"], v2["assertion"], v2["status"]) == ("inferred", "superseded", "stated", "active")


# --- S11-S12: one segment, and processing it again ------------------------------------------------------


def test_s11_two_values_for_a_slot_in_one_segment_keep_the_later_message(ledger):
    messages = [msg("a", "a-1", "moro em Lyon", AUG10), msg("a", "a-2", "na verdade moro em Paris", AUG10 + timedelta(minutes=5))]
    s = ledger.ingest("a", messages, [
        slot("city", "Lives in Paris", "na verdade moro em Paris", "a-2"), slot("city", "Lives in Lyon", "moro em Lyon", "a-1")])
    [city] = ledger.slot("city")
    assert (city["content"], city["version"]) == ("Lives in Paris", 1)
    assert (s.same_slot_in_segment, s.dropped_by_reason) == (1, {"same_slot_in_segment": 1}) and ledger.judge_calls[-1] == 0


def history_of_three(ledger):
    """Age and city on Aug 10, an age update on Sep 10, a family extension on Oct 10: every kind of write."""
    steps = [
        ("a", "I'm 18, I live in Grenoble", AUG10, [slot("age", AGE18, "I'm 18", "a-1"), slot("city", "Lives in Grenoble", "I live in Grenoble", "a-1")], []),
        ("b", "I'm 19 now, sou portuguesa", SEP10, [slot("age", AGE19, "I'm 19 now", "b-1"), slot("nationality", "Is Portuguese", "sou portuguesa", "b-1")], ["updates"]),
        ("c", "I arrived with my French husband", OCT10, [slot("family", "Arrived with a French husband", "I arrived with my French husband", "c-1")], []),
        ("d", "sou brasileira", OCT10 + timedelta(days=1), [slot("nationality", "Is Brazilian", "sou brasileira", "d-1")], ["conflicts"]),
    ]
    for thread, text, when, candidates, verdicts in steps:
        ledger.say(thread, text, when, candidates, verdicts)
    return steps


def test_s12_processing_the_same_segments_again_changes_nothing(ledger):
    steps = history_of_three(ledger)
    before = ledger.snapshot()
    for thread, text, when, candidates, verdicts in steps:
        ledger.say(thread, text, when, candidates, [], reprocess=True)
        assert ledger.judge_calls[-1] == 0  # a conflicting candidate already waiting is recognised, not re-judged
    assert ledger.snapshot() == before


def test_s12_an_extension_and_a_conflict_are_recognised_when_reprocessed(ledger):
    brazilian(ledger)
    verdict = Verdict(verdict="extends", content="Is Brazilian and Portuguese")
    ledger.say("b", "também sou portuguesa", SEP10, [slot("nationality", "Is Portuguese", "também sou portuguesa", "b-1")], [verdict])
    before = ledger.snapshot()
    ledger.say("b", "também sou portuguesa", SEP10, [slot("nationality", "Is Portuguese", "também sou portuguesa", "b-1")], reprocess=True)
    assert ledger.snapshot() == before and ledger.judge_calls[-1] == 0


# --- S13: facts in areas ---------------------------------------------------------------------------------


@pytest.fixture
def areas(ledger):
    ledger.settings.types["fact"].area = "required"
    ledger.settings.areas.seeds = [
        SeedArea(key="documents", title="Documents", description="documents"),
        SeedArea(key="housing", title="Housing", description="housing"),
    ]
    ledger.embeddings = LookupEmbeddings({
        "Has a Brazilian driving licence": vec_at(1.0), "Exchanged it for a French one": vec_at(0.85),
        "Rents a flat near the station": vec_at(0.85),
    }, default=vec_at(0.1))
    ledger.__post_init__()
    return ledger


def test_s13_a_fact_update_replaces_the_fact_in_its_area_and_never_one_in_another_area(areas):
    ledger = areas
    ledger.say("a", "Tenho CNH brasileira", AUG10, [fact("Has a Brazilian driving licence", "Tenho CNH brasileira", "a-1")])
    ledger.say("b", "troquei a minha por uma francesa", SEP10,
               [fact("Exchanged it for a French one", "troquei a minha por uma francesa", "b-1")], ["updates"])
    v1, v2 = sorted(ledger.rows("fact"), key=lambda r: r["version"])
    assert versions([v1, v2]) == [
        (1, "superseded", "Has a Brazilian driving licence"), (2, "active", "Exchanged it for a French one")]
    assert v1["memory_id"] == v2["memory_id"]
    # the same similarity in another area is not compared: no judge, a new row
    ledger.say("c", "alugo um apartamento perto da estação", OCT10,
               [fact("Rents a flat near the station", "alugo um apartamento perto da estação", "c-1", area="housing")])
    assert ledger.judge_calls[-1] == 0 and len(ledger.rows("fact", status="active")) == 2


def test_a_verified_fact_is_protected_like_a_slot(areas):
    ledger = areas
    ledger.say("a", "Tenho CNH brasileira", AUG10, [fact("Has a Brazilian driving licence", "Tenho CNH brasileira", "a-1")])
    [row] = ledger.rows("fact")
    ledger.service.edit(Actor(id=USER, roles=[]), row["memory_id"], {"content": "Has a Brazilian driving licence"})
    s = ledger.say("b", "troquei a minha por uma francesa", SEP10,
                   [fact("Exchanged it for a French one", "troquei a minha por uma francesa", "b-1")], ["updates"])
    [pending] = ledger.rows("fact", status="candidate")
    assert (s.conflicts_opened, pending["conflicts_with"]) == (1, row["memory_id"])


def test_an_older_fact_that_the_judge_calls_an_update_is_outdated(areas):
    ledger = areas
    ledger.say("b", "troquei a minha por uma francesa", SEP10,
               [fact("Exchanged it for a French one", "troquei a minha por uma francesa", "b-1")])
    s = ledger.say("a", "Tenho CNH brasileira", AUG10, [fact("Has a Brazilian driving licence", "Tenho CNH brasileira", "a-1")], ["updates"])
    assert (s.outdated, len(ledger.rows("fact"))) == (1, 1)


# --- S14: terms --------------------------------------------------------------------------------------------


def test_s14_terms_gain_aliases_up_to_the_cap_and_a_clashing_alias_is_a_conflict(ledger):
    ledger.settings.types["term"] = TypeConfig(**{"class": "memhub.types:Term"}, type_prior=0.5, scopes=["workspace"])
    ledger.settings.terms.max_aliases = 2
    say = lambda *a, **kw: ledger.say(*a, owner=None, **kw)
    say("a", "CNH significa carteira de motorista", AUG10, [term("CNH significa carteira de motorista", "a-1")])
    [first] = ledger.rows("term", owner=None)
    ledger.service.approve(ADMIN, first["id"])
    q = "CNH é o mesmo que permis de conduire e também licence"
    s = say("b", q, SEP10, [term(q, "b-1", aliases=("permis de conduire", "licence"))])
    v1, v2 = sorted(ledger.rows("term", owner=None), key=lambda r: r["version"])
    assert (v1["status"], v2["status"], v2["payload"]["aliases"]) == ("active", "candidate", ["carteira de motorista", "permis de conduire"])
    ledger.service.approve(ADMIN, v2["id"])
    q = "PERMIS significa carteira de motorista"
    s = say("c", q, OCT10, [term(q, "c-1", name="PERMIS", aliases=("carteira de motorista",))])
    [clash] = ledger.rows("term", owner=None, status="candidate")
    assert (s.conflicts_opened, clash["conflicts_with"], clash["payload"]["term"]) == (1, v1["memory_id"], "PERMIS")
    assert ledger.judge_calls[-1] == 0 and len(ledger.rows("term", owner=None, status="active")) == 1


# --- S15: areas merged while memories link to them ---------------------------------------------------------


def test_s15_no_memory_points_to_an_archived_area_after_a_merge(areas):
    ledger = areas
    ledger.say("a", "Tenho CNH brasileira", AUG10, [fact("Has a Brazilian driving licence", "Tenho CNH brasileira", "a-1")])
    ledger.say("b", "alugo um apartamento perto da estação", SEP10,
               [fact("Rents a flat near the station", "alugo um apartamento perto da estação", "b-1", area="housing")])
    docs, housing = [a for a in ledger.service.areas(ADMIN, user_id=USER, workspace_id="ws")]
    ledger.service.merge_areas(ADMIN, docs["memory_id"], housing["memory_id"])
    check_invariants(ledger.store)
    active = ledger.rows("fact", status="active")
    assert {tuple(l["memory_id"] for l in r["links"]) for r in active} == {(str(housing["memory_id"]),)}
    # a later fact for the archived seed gets a fresh area row, and the invariants still hold
    ledger.say("c", "preciso renovar o meu titre", OCT10, [fact("Has a residence permit to renew", "preciso renovar o meu titre", "c-1")])
    assert len(ledger.rows("fact", status="active")) == 3


# --- randomised: 200 seeded sequences against an oracle ----------------------------------------------------

SLOTS = {"city": ["Lyon", "Paris", "Nice"], "work": ["nurse", "chef"], "nationality": ["Brazilian", "Portuguese"]}
BASE = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def oracle_apply(state, key, value, when, immutable):
    """What the policy says one statement does to one slot (independent of the pipeline)."""
    cur = state.get(key)
    if cur is None:
        state[key] = [value, when, False]
        return
    if value == cur[0]:
        cur[1] = max(cur[1], when)  # the same value: evidence, `observed_at` never moves back
    elif when < cur[1] or cur[2] or key in immutable:
        return  # outdated, protected by a person, or immutable: the slot does not change
    else:
        state[key] = [value, when, False]


def run_sequences(store, settings, registry, seed, count, first=0):
    settings.types["profile"].immutable_keys = ["nationality"]
    settings.types["profile"].keys = {**settings.types["profile"].keys, **{k: k for k in SLOTS}}
    ledger = Ledger(store, settings, registry, FakeEmbeddings(16))
    for n in range(first, first + count):
        rng = random.Random(f"{seed}-{n}")
        owner = f"user-{seed}-{n}"
        state: dict[str, list] = {}  # slot -> [value, observed_at, verified]: the oracle
        steps: list[tuple] = []
        used: set[datetime] = set()
        try:
            for i in range(rng.randint(5, 15)):
                action = rng.choices(["say", "again", "edit"], [6, 2, 1.5])[0]
                if action == "edit" and state:  # a person edits a slot: verified, `observed_at` inherited
                    key = rng.choice(sorted(state))
                    value = rng.choice(SLOTS[key])
                    [row] = ledger.rows(key=key, owner=owner, status="active")
                    ledger.service.edit(Actor(id=owner, roles=[]), row["memory_id"], {"content": f"{key} is {value}"})
                    state[key] = [value, state[key][1], True]
                elif action == "again" and steps:  # the same segment processed again: nothing may change
                    message, candidate, key, value, when = rng.choice(steps)
                    ingest_step(ledger, message, candidate, owner, reprocess=True)
                    oracle_apply(state, key, value, when, {"nationality"})  # only ever adds evidence
                else:
                    key = rng.choice(sorted(SLOTS))
                    value = rng.choice(SLOTS[key])
                    when = BASE + timedelta(days=rng.randint(0, 200), minutes=i)
                    while when in used:
                        when += timedelta(minutes=30)
                    used.add(when)
                    message = msg(f"t{n}-{i}", f"t{n}-{i}-1", f"{key} is {value}", when, user=owner)
                    candidate = slot(key, f"{key} is {value}", f"{key} is {value}", message.message_id, durability="stable")
                    ingest_step(ledger, message, candidate, owner)
                    steps.append((message, candidate, key, value, when))
                    oracle_apply(state, key, value, when, {"nationality"})
                check_invariants(store, owner)
                _compare(ledger, state, owner)
        except AssertionError as exc:
            raise AssertionError(f"randomised sequence failed (seed={seed!r}, sequence={n}): {exc}") from exc


def ingest_step(ledger, message, candidate, owner, reprocess=False):
    """One message in its own thread. The judge answers `updates` whenever it is asked, and is asked at most once."""
    judge = ScriptedJudge([Verdict(verdict="updates")])
    summary = ingest_source(
        store=ledger.store, settings=ledger.settings, registry=ledger.registry, source=ListSource([message]),
        source_name="random", extractor=FakeChatModel([extraction(candidate), extraction()]), judge=judge,
        embeddings=ledger.embeddings, now=message.timestamp + timedelta(hours=2), reprocess=reprocess,
    )
    assert summary.failed_segments == 0, "a segment failed"


def _compare(ledger, state, owner):
    for key, (value, when, verified) in state.items():
        rows = ledger.rows(key=key, owner=owner, status="active")
        assert len(rows) == 1, f"{key}: {len(rows)} active rows"
        assert rows[0]["content"] == f"{key} is {value}", f"{key}: {rows[0]['content']!r} != {value!r}"
        assert rows[0]["observed_at"] == when and rows[0]["verified"] == verified, f"{key}: observed_at/verified"
    assert {r["payload"]["key"] for r in ledger.rows(owner=owner, status="active")} == set(state)


def test_randomised_sequences_keep_the_ledger_consistent_and_match_the_oracle(store, settings, registry):
    run_sequences(store, settings, registry, seed=20260925, count=200)


def test_a_broken_supersede_is_caught_by_the_randomised_test(store, settings, registry, monkeypatch):
    monkeypatch.setattr(MemoryStore, "_supersede", lambda self, cur, row_id: 1)  # the old version stays active
    with pytest.raises(AssertionError, match="seed=20260925"):
        run_sequences(store, settings, registry, seed=20260925, count=30)
