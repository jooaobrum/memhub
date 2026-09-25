"""Areas (tickets 29-31): seed and proposed areas, area links, area-scoped reconcile, search boost, pages, summaries."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from memhub import cli
from memhub.config import AreasConfig, SeedArea
from memhub.pipeline.extract import AreaRef, NewArea, make_schema
from memhub.pipeline.summarize import Summary
from memhub.service import Actor
from tests.fakes import FakeChatModel, FakeEmbeddings, extraction, vec_at
from tests.test_ingest_e2e import NOW, Env, cand, line

SEEDS = [
    SeedArea(key="housing", title="Housing", description="renting, lease, CAF"),
    SeedArea(key="visa", title="Visa", description="permits, prefecture"),
]
Q = "I live in Lyon and I have a student visa"


class Emb(FakeEmbeddings):
    """Bag-of-words embeddings, with exact vectors for chosen texts."""

    def __init__(self, overrides=None):
        super().__init__(dims=16)
        self.overrides = overrides or {}

    def embed_query(self, text):
        return self.overrides.get(text) or super().embed_query(text)


@pytest.fixture
def area_settings(settings):
    s = settings.model_copy(deep=True)
    s.types["fact"].area = "required"
    s.areas = AreasConfig(seeds=SEEDS)
    return s


@pytest.fixture
def env(store, area_settings, registry, tmp_path, fake_embeddings):
    return Env(store, area_settings, registry, tmp_path, fake_embeddings)


def acand(*areas, quote="I live in Lyon", content="Lives in Lyon", **kw):
    refs = [AreaRef(existing=a) if isinstance(a, str) else AreaRef(new=NewArea(title=a[0], description=a[1])) for a in areas]
    return cand(quote=quote, fields={"content": content}, **kw).model_copy(update={"areas": refs})


def areas_of(env, user="u1"):
    return [m for m in env.memories(type="area", status="active") if m["user_id"] == user]


def facts(env):
    return [m for m in env.memories(type="fact", status="active")]


def test_admitted_fact_links_to_its_owners_area_and_a_fact_without_area_is_dropped(env):
    env.write(line(query=Q))
    s = env.run(FakeChatModel([extraction(
        acand("housing", "visa"), acand(quote="student visa", content="Has a student visa"),
    )]))
    [f] = facts(env)
    areas = areas_of(env)
    assert s.dropped_by_reason == {"no_area": 1} and s.created == 1 and s.areas_created == 2
    assert {a["payload"]["key"] for a in areas} == {"housing", "visa"}
    assert all(a["user_id"] == "u1" and a["status"] == "active" for a in areas)
    assert sorted(l["memory_id"] for l in f["links"]) == sorted(str(a["memory_id"]) for a in areas)
    assert {l["kind"] for l in f["links"]} == {"in_area"}


def test_a_seed_becomes_a_row_once_per_user(env):
    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing"), acand("housing", quote="student visa", content="Has a student visa"))]))
    [a] = areas_of(env)
    assert a["payload"]["title"] == "Housing" and a["payload"]["proposed"] is False
    assert len(facts(env)) == 2 and all(f["links"] == [{"kind": "in_area", "memory_id": str(a["memory_id"])}] for f in facts(env))
    env.write(line(query=Q, mid="m2", thread="c2", user="u2"))
    env.run(FakeChatModel([extraction(acand("housing"))]))
    assert len(areas_of(env)) == 1 and len(areas_of(env, "u2")) == 1


def test_unknown_area_key_is_ignored_and_leaves_a_required_fact_without_area(env):
    env.write(line())
    s = env.run(FakeChatModel([extraction(acand("nonsense"))]))
    assert s.dropped_by_reason == {"no_area": 1} and env.memories() == []


def test_reconcile_never_compares_facts_of_different_areas(env):
    same = vec_at(1.0)
    emb = Emb({"Rents a flat": same, "Has a permit": same})
    env.write(line(query=Q))
    s = env.run(FakeChatModel([extraction(
        acand("housing", content="Rents a flat"), acand("visa", quote="student visa", content="Has a permit"),
    )]), embeddings=emb)
    assert (s.created, s.merged) == (2, 0)
    # the same text in the same area is a duplicate: merged
    env.write(line(query=Q), line(mid="m2", query=Q))
    s = env.run(FakeChatModel([extraction(acand("housing", content="Rents a flat"))]), embeddings=emb, reprocess=True)
    assert s.merged == 1 and len(facts(env)) == 2


def test_merge_appends_a_new_area_link(env):
    emb = Emb({"Rents a flat": vec_at(1.0), "Rents a flat now": vec_at(1.0)})
    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing", content="Rents a flat"))]), embeddings=emb)
    env.run(FakeChatModel([extraction(acand("housing", "visa", content="Rents a flat now"))]), embeddings=emb, reprocess=True)
    [f] = facts(env)
    assert len(f["links"]) == 2


def test_prompt_lists_areas_and_schema_carries_areas_only_for_area_types(area_settings, registry):
    from memhub.pipeline.extract import _prompt

    text = _prompt(["fact", "profile"], registry, 3, False, [], "x", area_types=area_settings.area_types(),
                   areas=[("housing", "Housing", "renting, lease, CAF")])
    assert "housing: Housing - renting, lease, CAF" in text and "MUST NOT be emitted" in text
    schema = make_schema(["fact", "profile"], registry, area_settings.keyed_types(), area_settings.area_types()).model_json_schema()
    defs = schema["$defs"]
    assert "areas" in defs["Candidate_fact"]["properties"]
    closed = make_schema(["fact"], registry, {}, {"fact": "required"}, areas_open=False).model_json_schema()
    assert "new" not in json.dumps(closed["$defs"].get("ClosedAreaRef", {}))


def test_search_returns_area_titles_and_boosts_the_best_area_without_filtering(env, service):
    a_text, b_text = "Housing: renting, lease, CAF", "Visa: permits, prefecture"
    emb = Emb({a_text: vec_at(1.0), b_text: [0.0, 0.0, 1.0, *([0.0] * 13)], "query": vec_at(0.99),
               "in housing": vec_at(0.97), "in visa": vec_at(1.0)})
    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(
        acand("housing", content="in housing"), acand("visa", quote="student visa", content="in visa"),
    )]), embeddings=emb)
    service.embeddings = emb
    rows = service.search(Actor("u1"), "query", workspace_id="ws", user_id="u1")
    assert {r["type"] for r in rows} == {"fact"}  # the area rows are headers, not claims
    assert [r["content"] for r in rows] == ["in housing", "in visa"]  # housing boosted above the closer visa row
    assert [r["areas"] for r in rows] == [["Housing"], ["Visa"]]
    rows = service.search(Actor("u1"), "query", workspace_id="ws", user_id="u1", area="visa")
    assert [r["content"] for r in rows] == ["in visa", "in housing"]


def test_cli_areas_and_list_by_area(pg_dsn, env, tmp_path, monkeypatch):
    import yaml

    from tests.test_cli import _config_dict

    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing"), acand("visa", quote="student visa", content="Has a visa"))]))
    cfg = _config_dict(pg_dsn, env.store.prefix)
    cfg["workspace_default"] = "ws"
    cfg["types"]["fact"]["area"] = "required"
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(cli, "build_embeddings", lambda c: FakeEmbeddings(dims=c.dims))
    run = lambda *a: CliRunner().invoke(cli.app, [*a, "--config", str(path)])  # noqa: E731
    out = run("areas", "--user", "u1")
    assert out.exit_code == 0, out.output
    listed = json.loads(out.stdout)
    assert {(a["key"], a["count"], a["proposed"]) for a in listed} == {("housing", 1, False), ("visa", 1, False)}
    out = run("list", "--user", "u1", "--area", "visa", "--type", "fact")
    assert [r["content"] for r in json.loads(out.stdout)] == ["Has a visa"]
    assert run("list", "--user", "u1", "--area", "pets").stdout.strip() == "[]"


# --- ticket 30: proposed areas ---------------------------------------------------------------------------------


def test_a_proposed_area_merges_when_close_and_creates_a_proposed_row_otherwise(env):
    seed_text = "Housing: renting, lease, CAF"
    emb = Emb({seed_text: vec_at(1.0), "Renting: tenancy": vec_at(0.9), "Cooking: recipes": vec_at(0.5)})
    env.write(line(query=Q))
    s = env.run(FakeChatModel([extraction(
        acand("housing"),
        acand(("Renting", "tenancy"), quote="student visa", content="Has a visa"),
        acand(("Cooking", "recipes"), quote="Lyon", content="Cooks a lot"),
    )]), embeddings=emb)
    assert (s.areas_created, s.areas_merged) == (2, 1)  # housing seed + the proposed cooking
    by_key = {a["payload"]["key"]: a for a in areas_of(env)}
    assert set(by_key) == {"housing", "cooking"} and by_key["cooking"]["payload"]["proposed"] is True
    links = {f["content"]: f["links"][0]["memory_id"] for f in facts(env)}
    assert links["Has a visa"] == links["Lives in Lyon"] == str(by_key["housing"]["memory_id"])


def test_area_cap_drops_the_candidate_and_creates_no_row(env):
    env.settings.areas.max_per_user = 1
    env.write(line(query=Q))
    s = env.run(FakeChatModel([extraction(acand("housing"), acand(("Cooking", "recipes"), quote="Lyon", content="Cooks daily"))]))
    assert s.dropped_by_reason == {"area_cap": 1} and s.areas_capped == 1 and s.created == 1
    assert [a["payload"]["key"] for a in areas_of(env)] == ["housing"]


def test_closed_areas_reject_a_proposal(env):
    env.settings.areas.open = False
    env.write(line())
    s = env.run(FakeChatModel([extraction(acand(("Cooking", "recipes")))]))
    assert s.dropped_by_reason == {"invalid_fields": 1} and env.memories() == []


def test_areas_merge_moves_links_as_new_versions_and_archives_the_source(env, service):
    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing"), acand("visa", quote="student visa", content="Has a visa"))]))
    housing, visa = sorted(areas_of(env), key=lambda a: a["payload"]["key"])
    admin = Actor("admin", ["workspace_admin"])
    out = service.merge_areas(admin, visa["memory_id"], housing["memory_id"])
    assert out["moved"] == 1
    active = facts(env)
    assert len(active) == 2 and all(f["links"] == [{"kind": "in_area", "memory_id": str(housing["memory_id"])}] for f in active)
    assert max(f["version"] for f in active) == 2
    assert [a["payload"]["key"] for a in areas_of(env)] == ["housing"]
    assert [m["status"] for m in env.memories(type="area") if m["memory_id"] == visa["memory_id"]] == ["archived"]


# --- ticket 31: pages and summaries ---------------------------------------------------------------------------


def judge_for(*texts):
    return FakeChatModel([Summary(summary=t) for t in texts], unrelated_by_default=True)


def test_page_lists_active_non_stale_rows_newest_first(env, service):
    env.write(line(mid="m1", query="I live in Lyon", age=timedelta(days=3)), line(mid="m2", query="I rent a flat", age=timedelta(days=2)),
              line(mid="m3", query="I sublet a room", age=timedelta(days=1)))
    env.run(FakeChatModel([extraction(
        acand("housing", content="Lives in Lyon"),
        acand("housing", quote="I rent a flat", mid="m2", content="Rents a flat"),
        acand("housing", quote="I sublet a room", mid="m3", content="Sublets a room", valid_until=None),
    )]), judge=judge_for("Lives in Lyon and rents."))
    # expire one row: it drops out of the page
    with env.store.connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {env.store._t('memory')} SET valid_until=%s WHERE content='Sublets a room'", (NOW - timedelta(hours=1),))
    page = service.page("u1", "housing", workspace_id="ws", now=NOW)
    assert page["title"] == "Housing" and page["summary"] == "Lives in Lyon and rents."
    assert [d["content"] for d in page["details"]] == ["Rents a flat", "Lives in Lyon"]
    assert page["last_updated"] == page["details"][0]["observed_at"] == NOW - timedelta(days=2)
    assert service.page("u1", "Housing", workspace_id="ws", now=NOW)["title"] == "Housing"


def test_a_run_writes_one_summary_per_touched_area_and_a_quiet_run_none(env):
    env.write(line(mid="m1", query="I live in Lyon", age=timedelta(days=3)), line(mid="m2", query="I rent a flat", age=timedelta(days=2)))
    judge = judge_for("first")
    s = env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"))]), judge=judge)
    assert (s.summaries_written, judge.calls, (s.summary_tokens_in, s.summary_tokens_out)) == (1, 1, (100, 20))
    [a] = areas_of(env)
    assert (a["version"], a["created_by"], a["payload"]["summary"], a["payload"]["summary_label"]) == (2, "summarizer", "first", "auto-summary")
    quiet = env.run(FakeChatModel(), judge=judge)
    assert quiet.summaries_written == 0 and judge.calls == 1
    # a re-merge of the same row (row set unchanged) makes no call
    env.write(line(mid="m1", query="I live in Lyon", age=timedelta(days=3)))
    s = env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"))]), judge=judge, reprocess=True)
    assert s.merged == 1 and s.summaries_written == 0 and judge.calls == 1
    # a new row in the area: exactly one more version
    env.write(line(mid="m1", query="I live in Lyon", age=timedelta(days=3)), line(mid="m2", query="I rent a flat", age=timedelta(days=2)))
    judge2 = judge_for("second")
    s = env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"),
                                          acand("housing", quote="I rent a flat", mid="m2", content="Rents a flat"))]),
                judge=judge2, reprocess=True)
    assert s.summaries_written == 1 and judge2.calls == 1
    assert [(a["version"], a["payload"]["summary"]) for a in areas_of(env)] == [(3, "second")]


def test_summary_prompt_holds_only_titles_and_contents_and_dry_run_writes_nothing(env):
    prompts = []

    class Spy(FakeChatModel):
        def with_structured_output(self, schema, **kw):
            runnable = super().with_structured_output(schema, **kw)
            invoke = runnable.invoke
            runnable.invoke = lambda messages, *a, **k: (prompts.append(messages[0][1]), invoke(messages))[1]
            return runnable

    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"))]), judge=Spy([Summary(summary="x")]), dry_run=True)
    assert prompts == [] and env.memories() == []
    env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"))]), judge=Spy([Summary(summary="x")]))
    [prompt] = prompts
    assert "Housing" in prompt and "Lives in Lyon" in prompt and "I live in Lyon" not in prompt
    assert "student visa" not in prompt and "renting, lease" not in prompt


def test_an_area_without_active_rows_makes_no_call(env):
    from memhub.pipeline.summarize import summarize_area

    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing"))]), judge=judge_for("s"))
    [fact] = facts(env)
    [area] = areas_of(env)
    with env.store.connect() as conn, conn.cursor() as cur:
        env.store.archive(cur, fact["memory_id"])
        judge = FakeChatModel()
        assert summarize_area(judge, cur=cur, store=env.store, area_memory_id=area["memory_id"]).written is False
    assert judge.calls == 0


def test_summary_is_not_a_claim_in_search(env, service):
    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"))]), judge=judge_for("Lives in Lyon, says the summary."))
    service.embeddings = env.embeddings
    assert {r["type"] for r in service.search(Actor("u1"), "Lives in Lyon", workspace_id="ws", user_id="u1")} == {"fact"}


def test_cli_page_prints_every_page_marked_auto_summary(pg_dsn, env, tmp_path, monkeypatch):
    import yaml

    from tests.test_cli import _config_dict

    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"))]), judge=judge_for("Lives in Lyon."))
    cfg = _config_dict(pg_dsn, env.store.prefix)
    cfg["workspace_default"] = "ws"
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(cli, "build_embeddings", lambda c: FakeEmbeddings(dims=c.dims))
    out = CliRunner().invoke(cli.app, ["page", "--user", "u1", "--config", str(path)])
    assert out.exit_code == 0, out.output
    assert "# Housing" in out.stdout and "auto-summary" in out.stdout and "Lives in Lyon" in out.stdout and "Last updated" in out.stdout
    one = CliRunner().invoke(cli.app, ["page", "--user", "u1", "--area", "housing", "--config", str(path)])
    assert one.exit_code == 0 and "# Housing" in one.stdout


# --- ticket 35: middleware injects the About-the-user block and the query's area page --------------------------


def test_middleware_about_the_user_block_and_area_page(env):
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from memhub.middleware import MemoryMiddleware
    from memhub.service import MemoryService

    env.settings.types["profile"].retrieval = "always"
    env.settings.types["preference"].retrieval = "always"
    emb = Emb({"Housing: renting, lease, CAF": vec_at(1.0), "Visa: permits, prefecture": [0.0, 0.0, 1.0, *([0.0] * 13)],
               "where do I live": vec_at(0.99), "weather": [0.0] * 3 + [1.0] + [0.0] * 12})
    env.write(line(query=Q))
    env.run(FakeChatModel([extraction(acand("housing", content="Lives in Lyon"))]), embeddings=emb,
            judge=judge_for("Lives in Lyon."))
    service = MemoryService(store=env.store, settings=env.settings, registry=env.registry, embeddings=emb)
    me = Actor("u1")
    city = service.add(me, type="profile", scope="user", user_id="u1", workspace_id="ws", fields={"key": "city", "content": "lives in Lyon city"})
    stale = service.add(me, type="profile", scope="user", user_id="u1", workspace_id="ws", fields={"key": "work", "content": "old job"})
    tone = service.add(me, type="preference", scope="user", user_id="u1", workspace_id="ws", fields={"key": "tone", "content": "likes terse tone"})
    with env.store.connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {env.store._t('memory')} SET valid_until = now() - interval '1 day' WHERE memory_id = %s", [stale["memory_id"]])
    mw = MemoryMiddleware(service, workspace_from="ws", user_from="u1")
    run = lambda text, state=None: (lambda st: {**st, **mw.before_agent(st, SimpleNamespace(context=None))})(  # noqa: E731
        {**(state or {}), "messages": [HumanMessage(text)]})

    state = run("where do I live")
    about = state["memory_snapshot"]
    assert "<about_the_user>" in about and "old job" not in about
    assert about.index("lives in Lyon city") < about.index("likes terse tone")
    turn = state["memory_turn"]
    assert "<area_page>" in turn and "# Housing" in turn and "auto-summary" in turn and "Lives in Lyon." in turn
    [area] = areas_of(env)
    [f] = facts(env)
    assert turn.count("Lives in Lyon") >= 2  # the summary and the detail / search hit
    assert f"[{f['id']}|fact|unverified] Lives in Lyon" in turn
    assert {f"{area['memory_id']}@{area['version']}", f"{f['memory_id']}@{f['version']}",
            f"{city['memory_id']}@{city['version']}", f"{tone['memory_id']}@{tone['version']}"} <= set(state["memhub_injected"])

    none = run("weather", state)
    assert "<area_page>" not in none["memory_turn"]

    env.settings.areas.page_max_chars = 30  # too small for any detail: title and summary only
    small = run("where do I live")
    assert "<area_page>" in small["memory_turn"] and f"[{f['id']}|fact|unverified] Lives in Lyon" not in small["memory_turn"].split("</area_page>")[0]
