"""The memory index (#28): what the caller's memory holds, as data, as text, in the middleware, as tools and on the
CLI. Each test names the check it proves (`.specs/features/memory-index/checks.md`). In-memory ledger, and a SQLite
one where the CLI is part of the claim."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
import yaml
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from typer.testing import CliRunner

from memhub import Memory, cli
from memhub.config import ConfigError, EntityConfig, IndexConfig, load_config
from memhub.pages import INDEX_HOWTO, index_text
from memhub.service import Actor, ValidationError
from tests.fakes import FakeEmbeddings
from tests.test_pages import IDS, NOW, add_area, add_row, cfg, cli_ledger, memory  # noqa: F401

runner = CliRunner()
U2 = dict(workspace_id="w1", user_id="u2", run_id="r2")
PRD1, PRD2 = {"type": "machine", "id": "PRD_1"}, {"type": "machine", "id": "PRD_2"}


def counts(entries: list[dict], name: str = "title") -> dict:
    return {e[name] if name != "subject" else f"{e['type']} {e['id']}": e["count"] for e in entries}


def two_groups(m: Memory) -> tuple[dict, dict, dict]:
    lube, bear, team = add_area(m, "lubrication"), add_area(m, "bearings"), add_area(m, "lubrication", scope="workspace")
    for i in range(3):
        add_row(m, f"lube {i}", lube)
    for i in range(2):
        add_row(m, f"bearing {i}", bear)
    add_row(m, "team lube", team)
    return lube, bear, team


# --- S1: the index as data ----------------------------------------------------------------------------------


def test_index_two_groups(cfg):
    """C1 (AC 1)."""
    m = memory(cfg)
    two_groups(m)
    idx = m.index(**IDS)
    assert set(idx) == {"me", "team"}
    assert counts(idx["me"]["areas"]) == {"Lubrication": 3, "Bearings": 2}
    assert counts(idx["team"]["areas"]) == {"Lubrication": 1}
    for group in (idx["me"], idx["team"]):
        assert {"areas", "subjects", "types"} <= set(group)
    assert idx["me"]["areas"][0] == {"key": "lubrication", "title": "Lubrication", "count": 3}


def test_index_counts_only_active(cfg):
    """C2 (AC 2): archived, rejected, candidate, flag, stale and superseded rows are not counted."""
    m = memory(cfg)
    lube = add_area(m, "lubrication")
    for i in range(3):
        add_row(m, f"active {i}", lube)
    for status in ("archived", "rejected", "candidate", "flag"):
        add_row(m, status, lube, status=status, reviewed_by=None)
    add_row(m, "stale", lube, valid_until=NOW - timedelta(days=1))
    mid = uuid.uuid4()
    add_row(m, "old version", lube, memory_id=mid, status="superseded", version=1)
    add_row(m, "new version", lube, memory_id=mid, version=2)
    assert counts(m.index(**IDS)["me"]["areas"]) == {"Lubrication": 4}
    assert counts(m.index(**IDS)["me"]["types"], "name") == {"fact": 4}


def test_index_other_users_not_counted(cfg):
    """C3 (AC 3)."""
    m = memory(cfg)
    two_groups(m)
    theirs = add_area(m, "lubrication", user="u2")
    for i in range(5):
        add_row(m, f"u2 lube {i}", theirs, created_by="u2")
    idx = m.index(**IDS)
    assert counts(idx["me"]["areas"])["Lubrication"] == 3
    assert counts(idx["team"]["areas"]) == {"Lubrication": 1}
    assert counts(m.index(**U2)["me"]["areas"]) == {"Lubrication": 5}


def test_index_subject_counted_once_per_distinct(cfg):
    """C4 (AC 4)."""
    m = memory(cfg)
    lube = add_area(m, "lubrication")
    add_row(m, "both machines", lube, entities=[PRD1, PRD2])
    add_row(m, "PRD_1 twice", lube, entities=[PRD1, dict(PRD1)])
    me = m.index(**IDS)["me"]
    assert counts(me["subjects"], "subject") == {"machine PRD_1": 2, "machine PRD_2": 1}
    assert counts(me["types"], "name") == {"fact": 2}


def test_index_skips_internal_types(cfg):
    """C5 (AC 5): area and term rows are in no count."""
    m = memory(cfg)
    lube = add_area(m, "lubrication")
    add_row(m, "a fact", lube)
    add_row(m, "a term", lube, type="term", entities=[PRD1])
    me = m.index(**IDS)["me"]
    names = {t["name"] for g in (me, m.index(**IDS)["team"]) for t in g["types"]}
    assert names == {"fact"}
    assert counts(me["areas"]) == {"Lubrication": 1}
    assert me["subjects"] == []


def test_index_guarded_subjects(cfg):
    """C6 (AC 6)."""
    cfg.entity_types = []
    cfg.entities = {"patient": EntityConfig(require_filter=True), "machine": EntityConfig()}
    m = memory(cfg)
    ward = add_area(m, "ward")
    add_row(m, "p1 allergy", ward, entities=[{"type": "patient", "id": "p1"}])
    add_row(m, "p1 diet", ward, entities=[{"type": "patient", "id": "p1"}])
    add_row(m, "machine note", ward, entities=[PRD1])
    me = m.index(**IDS)["me"]
    assert counts(me["subjects"], "subject") == {"machine PRD_1": 1}
    assert counts(me["areas"]) == {"Ward": 3} and counts(me["types"], "name") == {"fact": 3}
    assert "patient" not in index_text(m.index(**IDS), cfg.index.show) and "p1" not in m.render(None, **IDS)
    m.settings.index.show_guarded = ["patient"]
    assert counts(m.index(**IDS)["me"]["subjects"], "subject") == {"patient p1": 2, "machine PRD_1": 1}


def test_index_order_and_empty_areas(cfg):
    """C7 (AC 7): count descending, then name; an area with no counted row is absent."""
    m = memory(cfg)
    alpha, zeta, beta, empty = (add_area(m, k) for k in ("alpha", "zeta", "beta", "empty"))
    add_row(m, "z1", zeta), add_row(m, "z2", zeta)
    add_row(m, "b1", beta, entities=[PRD2]), add_row(m, "a1", alpha, entities=[PRD1])
    add_row(m, "archived", empty, status="archived")
    add_row(m, "s1", zeta, type="symptom", entities=[PRD2])
    me = m.index(**IDS)["me"]
    assert [a["title"] for a in me["areas"]] == ["Zeta", "Alpha", "Beta"]
    assert [f"{s['type']} {s['id']}" for s in me["subjects"]] == ["machine PRD_2", "machine PRD_1"]
    assert [t["name"] for t in me["types"]] == ["fact", "symptom"]


def test_index_without_user(cfg):
    """C8 (AC 8): the service with no user has `me` None; a `Memory` call still needs the ids (AD-001)."""
    m = memory(cfg)
    two_groups(m)
    idx = m.service.index(Actor(id="cli"), workspace_id="w1", user_id=None)
    assert idx["me"] is None and counts(idx["team"]["areas"]) == {"Lubrication": 1}
    with pytest.raises(ValidationError):
        m.index(workspace_id="w1", user_id=None, run_id="r1")


# --- S2: the index as text ----------------------------------------------------------------------------------


def test_readme_lists_subjects_and_types(cfg):
    """C10 (AC 10)."""
    m = memory(cfg)
    lube = add_area(m, "lubrication")
    add_row(m, "one", lube, entities=[PRD1]), add_row(m, "two", lube, entities=[PRD1]), add_row(m, "three", lube)
    body = m.render(None, **IDS).split("## Areas\n", 1)[1]
    assert body == ("- lubrication.md: Lubrication (3 memories)\n\n## Subjects\n- machine PRD_1 (2)\n\n"
                    "## Types\n- fact (3)\n")
    m2 = memory(cfg)
    add_row(m2, "plain", add_area(m2, "travel"))
    assert "## Subjects\n<!-- no subjects yet -->" in m2.render(None, **IDS)


def test_index_text_groups(cfg):
    """C11 (AC 11)."""
    m = memory(cfg)
    lube, _, team = two_groups(m)
    add_row(m, "subject row", lube, entities=[PRD1])
    text = index_text(m.index(**IDS), cfg.index.show, cfg.index.max_chars)
    assert text.splitlines() == [
        "<memory_index>",
        "Yours (users/u1):",
        "Areas: Lubrication [lubrication] (4) · Bearings [bearings] (2)",
        "Subjects: machine PRD_1 (1)",
        "Types: fact (6)",
        "Team (team/w1):",
        "Areas: Lubrication [lubrication] (1)",
        "Types: fact (1)",
        INDEX_HOWTO,
        "</memory_index>",
    ]
    assert INDEX_HOWTO == ('Open an area with read_memory_page(area, owner); a subject with search_memory '
                           'filters={"entities": {"type": .., "id": ..}}.')
    only_me = memory(cfg)
    add_row(only_me, "x", add_area(only_me, "travel"))
    assert "Team (" not in index_text(only_me.index(**IDS), cfg.index.show)


def test_index_text_budget(cfg):
    """C12 (AC 12): 2,000 machines in 1200 characters; a title longer than the budget is left out whole."""
    group = {"folder": "users/u1", "areas": [{"key": "a", "title": "A", "count": 3000}],
             "subjects": [{"type": "machine", "id": f"M{i:04d}", "count": 2000 - i} for i in range(2000)],
             "types": [{"name": "fact", "count": 2000}]}
    text = index_text({"me": group, "team": None}, ["areas", "subjects", "types"], 1200)
    assert len(text) <= 1200
    subjects = next(ln for ln in text.splitlines() if ln.startswith("Subjects: "))
    shown = subjects.count("machine M")
    assert shown > 10 and subjects.endswith(f" · and {2000 - shown} more")
    assert "machine M0000 (2000)" in subjects  # the largest first
    assert "Types: fact (2000)" in text and "Areas: A [a] (3000)" in text  # one long section starves no other
    long = {"folder": "users/u1", "areas": [{"key": "k", "title": "T" * 2000, "count": 1}], "subjects": [],
            "types": [{"name": "fact", "count": 1}]}
    text = index_text({"me": long, "team": None}, ["areas", "subjects", "types"], 1200)
    assert len(text) <= 1200 and "Areas: and 1 more" in text and "T" * 50 not in text
    for budget in (200, 300, 700):
        assert len(index_text({"me": group, "team": {**group, "folder": "team/w1"}},
                              ["areas", "subjects", "types"], budget)) <= budget


def test_index_show_sections(cfg):
    """C13 (AC 13)."""
    cfg.index = IndexConfig(show=["areas"])
    m = memory(cfg)
    add_row(m, "one", add_area(m, "lubrication"), entities=[PRD1])
    text = index_text(m.index(**IDS), cfg.index.show, cfg.index.max_chars)
    assert "Areas: Lubrication" in text and "Subjects:" not in text and "Types:" not in text
    readme = m.render(None, **IDS)
    assert "## Areas" in readme and "## Subjects" not in readme and "## Types" not in readme


def test_index_text_has_no_content(cfg):
    """C14 (AC 14)."""
    m = memory(cfg)
    lube = add_area(m, "lubrication")
    add_row(m, "grease the PRD_1 bearings weekly with lithium", lube, entities=[PRD1])
    text = index_text(m.index(**IDS), cfg.index.show)
    assert "grease" not in text and "lithium" not in text and "weekly" not in text


# --- S3: the middleware block -------------------------------------------------------------------------------


def turn(mw, text, state=None):
    state = {**(state or {}), "messages": [HumanMessage(text)]}
    return {**state, **mw.before_agent(state, SimpleNamespace(context=None))}


def test_block_after_about_the_user(cfg):
    """C15 (AC 15)."""
    m = memory(cfg)
    lube, _, _ = two_groups(m)
    snapshot = turn(m.middleware(**IDS), "hello")["memory_snapshot"]
    assert snapshot.startswith("<memory_index>\nYours (users/u1):")
    m.settings.types["preference"].retrieval = "always"
    add_row(m, "answers in French", lube, type="preference")
    snapshot = turn(m.middleware(**IDS), "hello")["memory_snapshot"]
    assert "</about_the_user>\n<memory_index>\n" in snapshot


def test_no_block_without_areas_or_subjects(cfg):
    """C16 (AC 16): type counts alone inject nothing."""
    m = memory(cfg)
    m.add({"content": "a plain fact"}, type="fact", **IDS)
    assert m.index(**IDS)["me"]["types"] and not m.index(**IDS)["me"]["areas"]
    assert "<memory_index>" not in turn(m.middleware(**IDS), "hello").get("memory_snapshot", "")


def test_no_block_when_disabled(cfg):
    """C17 (AC 17)."""
    cfg.index = IndexConfig(enabled=False)
    m = memory(cfg)
    two_groups(m)
    assert "<memory_index>" not in turn(m.middleware(**IDS), "hello").get("memory_snapshot", "")


def test_block_fixed_for_thread(cfg):
    """C18 (AC 18)."""
    m = memory(cfg)
    lube, _, _ = two_groups(m)
    mw = m.middleware(**IDS)
    first = turn(mw, "hello")
    add_row(m, "added between turns", lube, entities=[PRD1])
    second = turn(mw, "again", first)
    assert second["memory_snapshot"] == first["memory_snapshot"]
    assert "PRD_1" not in second["memory_snapshot"]
    assert "PRD_1" in turn(m.middleware(**IDS), "new thread")["memory_snapshot"]


def test_block_injects_no_ids(cfg):
    """C19 (AC 19)."""
    m = memory(cfg)
    two_groups(m)
    on = turn(m.middleware(**IDS), "lube 1")
    m.settings.index.enabled = False
    off = turn(m.middleware(**IDS), "lube 1")
    assert "<memory_index>" in on["memory_snapshot"]
    assert on["memhub_injected"] == off["memhub_injected"]


class ScriptedModel(BaseChatModel):
    """Answers with scripted messages, recording what it was sent."""

    script: list[AIMessage]
    seen: list[list] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def test_agent_opens_area_in_one_call(cfg):
    """C20 (AC 20)."""
    m = memory(cfg)
    two_groups(m)
    model = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[{"name": "read_memory_page", "args": {"area": "lubrication"}, "id": "c1"}]),
        AIMessage(content="done"),
    ], seen=[])
    agent = create_agent(model, tools=[], middleware=[m.middleware(**IDS)])
    out = agent.invoke({"messages": [HumanMessage("what do you know about lubrication?")]})
    assert "Lubrication [lubrication] (3)" in model.seen[0][0].content
    results = [msg for msg in out["messages"] if isinstance(msg, ToolMessage)]
    assert len(results) == 1
    for i in range(3):
        assert f"lube {i}" in results[0].content
    assert "bearing" not in results[0].content and "team lube" not in results[0].content


# --- S4: tools on every surface ------------------------------------------------------------------------------


def test_toolkit_specs_list_four(cfg):
    """C21 (AC 21)."""
    specs = memory(cfg).tools(**IDS).tool_specs()
    assert [s["function"]["name"] for s in specs] == [
        "search_memory", "propose_memory", "list_memory_index", "read_memory_page"]
    page = specs[3]["function"]["parameters"]
    assert page["required"] == ["area"] and page["properties"]["owner"]["enum"] == ["me", "team"]


def test_list_memory_index_tool(cfg):
    """C22 (AC 22): the budgeted text and the data, built at each call."""
    m = memory(cfg)
    lube, _, _ = two_groups(m)
    kit = m.tools(**IDS)
    first = json.loads(kit.call("list_memory_index", {}))
    assert set(first) == {"text", "index"}
    assert first["text"] == index_text(m.index(**IDS), cfg.index.show, cfg.index.max_chars)
    assert first["index"] == json.loads(json.dumps(m.index(**IDS)))
    add_row(m, "new", lube, entities=[PRD2])
    assert "machine PRD_2 (1)" in json.loads(kit.call("list_memory_index", {}))["text"]


def test_read_memory_page_tool(cfg):
    """C23 (AC 23)."""
    m = memory(cfg)
    lube, _, _ = two_groups(m)
    for i in range(35):  # over `render.summarize_over`, so the area folds subjects into summary lines
        add_row(m, f"prd1 note {i}", lube, entities=[PRD1])
    kit = m.tools(**IDS)
    strip = lambda page: page.split("\n", 2)[2]  # noqa: E731 - the header line carries the render time
    assert strip(kit.call("read_memory_page", {"area": "lubrication"})) == strip(m.render("lubrication", **IDS))
    team = kit.call("read_memory_page", {"area": "lubrication", "owner": "team"})
    assert "team lube" in team and "lube 0" not in team
    subject = kit.call("read_memory_page", {"area": "lubrication", "subject": "prd-1"})
    assert subject.splitlines()[0] == "# Memory: Lubrication / PRD_1 (u1)" and subject.count("prd1 note") == 35


def test_read_memory_page_errors(cfg):
    """C24 (AC 24): an `{"error"}` result in the toolkit, the same text in the middleware, never an exception."""
    m = memory(cfg)
    two_groups(m)
    kit = m.tools(**IDS)
    assert json.loads(kit.call("read_memory_page", {"area": "nope"})) == {"error": "no area 'nope'"}
    assert "error" in json.loads(kit.call("read_memory_page", {"area": "lubrication", "subject": "ghost"}))
    assert json.loads(kit.call("read_memory_page", {"area": "lubrication", "owner": "admin"})) == {
        "error": "owner must be me or team"}
    tool = next(t for t in m.middleware(**IDS).tools if t.name == "read_memory_page")
    call = lambda args: tool.func(runtime=SimpleNamespace(context=None), **args)  # noqa: E731
    assert call({"area": "nope"}) == "error: no area 'nope'"
    assert call({"area": "lubrication", "owner": "admin"}) == "error: owner must be me or team"


# --- S5: CLI, config ----------------------------------------------------------------------------------------


def test_cli_index(tmp_path, monkeypatch, cfg):
    """C26 (AC 26)."""
    ledger = cli_ledger(tmp_path, monkeypatch, cfg)
    two_groups(ledger.memory)
    lube = next(a for a in [add_area(ledger.memory, "pumps")])
    for i in range(1500):  # unbudgeted: more than `index.max_chars` holds
        add_row(ledger.memory, f"pump {i}", lube, entities=[{"type": "machine", "id": f"P{i:04d}"}])
    for var in ("MEMHUB_WORKSPACE", "MEMHUB_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "read_context", lambda: {"workspace": None, "user": None})
    res = runner.invoke(cli.app, ["index", "--ws", "w1", "--user", "u1", "--config", str(ledger.path)])
    assert res.exit_code == 0, res.output
    assert "Yours (users/u1):" in res.stdout and "Team (team/w1):" in res.stdout
    assert "machine P1499 (1)" in res.stdout and " more" not in res.stdout
    data = runner.invoke(cli.app, ["index", "--ws", "w1", "--user", "u1", "--json", "--config", str(ledger.path)])
    assert data.exit_code == 0 and counts(json.loads(data.stdout)["team"]["areas"]) == {"Lubrication": 1}
    team = runner.invoke(cli.app, ["index", "--ws", "w1", "--config", str(ledger.path)])
    assert team.exit_code == 0 and "Team (team/w1):" in team.stdout and "Yours" not in team.stdout


def test_cli_index_fails(tmp_path, monkeypatch):
    """C27 (AC 27): a ledger with no tables stops the command with exit 1 and the reason on stderr."""
    monkeypatch.setattr(cli, "build_embeddings", lambda c: FakeEmbeddings(16))
    path = tmp_path / "memhub.yaml"
    path.write_text(yaml.safe_dump({
        "project_prefix": "fv", "database_url": f"sqlite:///{tmp_path / 'none.db'}", "workspace_default": "w1",
        "types": {"fact": {"class": "memhub.types:Fact"}},
        "embeddings": {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16},
    }))
    res = runner.invoke(cli.app, ["index", "--ws", "w1", "--user", "u1", "--config", str(path)])
    assert res.exit_code == 1 and res.stderr.strip() and not res.stdout.strip()


def _load(tmp_path, index: dict, **extra):
    path = tmp_path / "memhub.yaml"
    path.write_text(yaml.safe_dump({"types": {"fact": {"class": "memhub.types:Fact"}}, "index": index, **extra}))
    return load_config(path)


def test_index_config_rejects(tmp_path):
    """C28 (AC 28)."""
    for bad, key in (({"show": ["areas", "people"]}, "show"), ({"max_chars": 199}, "max_chars"),
                     ({"show_guarded": ["ghost"]}, "show_guarded")):
        with pytest.raises(ConfigError, match=key):
            _load(tmp_path, bad)
    assert _load(tmp_path, {"max_chars": 200}).index.max_chars == 200
    ok = _load(tmp_path, {"show_guarded": ["patient"]}, entities={"patient": {"require_filter": True}})
    assert ok.index.show_guarded == ["patient"]
    assert IndexConfig() == IndexConfig(enabled=True, max_chars=1200, show=["areas", "subjects", "types"],
                                        show_guarded=[])


def test_config_show_lists_index(tmp_path, monkeypatch, cfg):
    """C29 (AC 29)."""
    ledger = cli_ledger(tmp_path, monkeypatch, cfg)
    res = runner.invoke(cli.app, ["config", "show", "--all", "--config", str(ledger.path)])
    assert res.exit_code == 0, res.output
    keys = {ln.split("  ")[0] for ln in res.stdout.splitlines()}
    assert {"index.enabled", "index.max_chars", "index.show", "index.show_guarded"} <= keys
