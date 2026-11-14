"""Search filters (issue #25): one grammar on `Memory.search` / `get_all`, the CLI, the toolkit, the middleware and MCP.

The backends' own SQL is proven by the contract cases (`memhub/storage/contract.py`, `test_filter_*`); these tests run
on the in-memory backend (SQLite where a CLI needs a file), with fake models and no Docker."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
import yaml
from typer.testing import CliRunner

from memhub import Memory, ValidationError, cli
from memhub.config import EntityConfig
from memhub.middleware import MemoryMiddleware
from memhub.storage.rules import area_link
from tests.fakes import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]
WS, ALICE = "ws", dict(workspace_id="ws", user_id="alice", run_id="r")
PRD1, PRD2, PRD3 = ({"type": "machine", "id": f"PRD_{i}"} for i in (1, 2, 3))
LINE1 = {"type": "machine", "id": "LINE_1"}
L1 = {"type": "line", "id": "L1"}
runner = CliRunner()
_count = iter(range(10_000))


@pytest.fixture
def config(settings):
    return settings.model_copy(update={"workspace_default": WS, "entity_types": [], "entities": {
        "machine": EntityConfig(), "line": EntityConfig(), "part": EntityConfig(isolate=False),
        "patient": EntityConfig(require_filter=True)}})


@pytest.fixture
def m(config) -> Memory:
    mem = Memory(db=f"memory://filters-{next(_count)}", embedder=FakeEmbeddings(16), config=config)
    mem.init()
    return mem


def seed(m: Memory, content: str, entities=(), *, user: str | None = "alice", scope: str = "user", ws: str = WS,
         type: str = "fact", links=None, **kw) -> dict:
    with m.store.unit_of_work() as uow:
        return uow.memories.add_memory(
            type=type, schema_version=1, scope=scope, workspace_id=ws, user_id=user, content=content,
            payload={"content": content, **({"key": content} if type == "area" else {})}, entities=list(entities),
            embedding=m.embedder.embed_query(content), evidence=[], status="active", verified=True, created_by="test",
            links=links or [], **kw)


def area(m: Memory, key: str, title: str, *, user: str | None = "alice", scope: str = "user") -> str:
    with m.store.unit_of_work() as uow:
        row = uow.memories.add_memory(
            type="area", schema_version=1, scope=scope, workspace_id=WS, user_id=user, content=title,
            payload={"key": key, "title": title}, entities=[], embedding=m.embedder.embed_query(title), evidence=[],
            status="active", verified=True, created_by="test")
    return row["memory_id"]


def contents(rows) -> set[str]:
    return {r["content"] for r in rows}


def search(m, filters=None, **kw):
    return m.search("machine", filters=filters, top_k=kw.pop("top_k", 20), **{**ALICE, **kw})


# --- S1 ------------------------------------------------------------------------------------------------------------

def test_prefix_through_search_and_get_all(m):
    """C2 (AC 1)."""
    for e in (PRD1, PRD2, PRD3, LINE1):
        seed(m, f"note {e['id']}", [e])
    flt = {"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}
    want = {"note PRD_1", "note PRD_2", "note PRD_3"}
    assert contents(search(m, flt)) == want
    assert contents(m.get_all(filters=flt, **ALICE)) == want


def test_areas_by_key_or_title(m):
    """C7 (AC 8): key or title, ignoring case, among the caller's user and workspace areas; no such area -> []."""
    bearings = area(m, "bearings", "Bearings")
    lube = area(m, "lubrication", "Lubrication", scope="workspace", user=None)
    seals = area(m, "seals", "Bearings & seals")
    seed(m, "in bearings", links=[area_link(bearings)])
    seed(m, "in lube", scope="workspace", user="bob", links=[area_link(lube)])
    seed(m, "in seals", links=[area_link(seals)])
    seed(m, "nowhere")
    for call in (lambda f: search(m, f), lambda f: m.get_all(filters=f, **ALICE)):
        assert contents(call({"areas": "Bearings"})) == {"in bearings"}
        assert contents(call({"areas": {"in": ["bearings", "LUBRICATION"]}})) == {"in bearings", "in lube"}
        assert contents(call({"areas": "Bearings & Seals"})) == {"in seals"}
        assert contents(call({"areas": "nowhere"})) == set()


def test_never_returns_another_owners_rows(m):
    """C14 (AC 15)."""
    bobs_area = area(m, "bobzone", "Bob zone", user="bob")
    seed(m, "alice prd", [PRD1])
    seed(m, "bob prd", [PRD1], user="bob", links=[area_link(bobs_area)])
    seed(m, "w2 prd", [PRD1], scope="workspace", user=None, ws="w2")
    seed(m, "w2 user prd", [PRD1], ws="w2")
    for f in ({"NOT": {"entities": [LINE1]}}, {"entities": {"type": "machine", "id": {"prefix": "PRD_"}}},
              {"OR": [{"areas": "bobzone"}, {"content": {"contains": "prd"}}]}):
        assert contents(search(m, f)) == {"alice prd"}, f
        assert contents(m.get_all(filters=f, **ALICE)) == {"alice prd"}, f


# --- S2 ------------------------------------------------------------------------------------------------------------

def test_entities_and_entity_type_are_anded(m):
    """C16 (AC 17, AD-051)."""
    seed(m, "prd and line", [PRD1, L1])
    seed(m, "prd alone", [PRD1])
    flt = {"entities": [PRD1], "entity_type": "line"}
    assert contents(search(m, flt)) == {"prd and line"}
    assert contents(m.get_all(filters=flt, **ALICE)) == {"prd and line"}


INVALID = [
    ({"tags": "x"}, "tags"),
    ({"areas": {"prefix": "b"}}, "prefix"),
    ({"types": {"in": ["fact"]}}, "types"),
    ({"observed_at": {"gte": "last month"}}, "last month"),
    ({"OR": []}, "OR"),
    ({"AND": {}}, "AND"),
    ({"NOT": [{"entities": [PRD1]}]}, "NOT"),
    ({"entity_type": {"in": []}}, "entity_type"),
    ({"entities": {"type": "machine", "id": {"prefix": ""}}}, "prefix"),
    ({"content": {"contains": ""}}, "contains"),
    ({"entities": {"type": "machine", "id": {"in": ["a"], "prefix": "b"}}}, "id"),
    ({"entities": {"type": "robot", "id": "x"}}, "robot"),
    ({"observed_at": {"after": "2026-01-01"}}, "after"),
    (["entities"], "dict"),
]


@pytest.mark.parametrize("flt, named", INVALID, ids=[f"bad{i}" for i in range(len(INVALID))])
def test_invalid_filter_raises_before_any_call(m, monkeypatch, flt, named):
    """C18 (AC 19): `ValidationError` naming the key or value, before the backend or the embedder is used."""
    def boom(*a, **kw):
        raise AssertionError("the backend or the embedder was called")

    monkeypatch.setattr(m.store, "unit_of_work", boom)
    monkeypatch.setattr(m.embedder, "embed_query", boom)
    with pytest.raises(ValidationError, match=named):
        search(m, flt)
    with pytest.raises(ValidationError, match=named):
        m.get_all(filters=flt, **ALICE)


def test_types_given_twice(m):
    """C19 (AC 20)."""
    with pytest.raises(ValidationError, match="types is given both"):
        m.search("x", types=["fact"], filters={"types": ["fact"]}, **ALICE)


# --- S3 ------------------------------------------------------------------------------------------------------------

def test_tiers_follow_the_entities_order(m):
    """C20 (AC 21): one tier per top-level item, in order; the other top-level keys apply to each tier."""
    bearings = area(m, "bearings", "Bearings")
    seed(m, "line in bearings", [LINE1], links=[area_link(bearings)])
    seed(m, "prd1 in bearings", [PRD1], links=[area_link(bearings)])
    seed(m, "prd2 in bearings", [PRD2], links=[area_link(bearings)])
    seed(m, "prd3 elsewhere", [PRD3])
    seed(m, "line elsewhere", [LINE1])
    hits = search(m, {"entities": [{"type": "machine", "id": {"prefix": "PRD_"}}, LINE1], "areas": "bearings"})
    assert [h["content"] for h in hits][-1] == "line in bearings"
    assert contents(hits[:2]) == {"prd1 in bearings", "prd2 in bearings"} and len(hits) == 3
    assert hits[-1]["matched_on"] == "machine:LINE_1"


def test_matched_on_names_the_entity_that_matched(m):
    """C21 (AC 22)."""
    seed(m, "line then prd", [L1, PRD2])
    [hit] = search(m, {"entities": {"type": "machine", "id": {"prefix": "PRD_"}}})
    assert hit["matched_on"] == "machine:PRD_2"
    [hit] = search(m, {"AND": [{"entities": [L1]}, {"entity_type": "machine"}]})
    assert hit["matched_on"] == "line:L1"


def test_no_entity_predicate_no_matched_on(m):
    """C22 (AC 23)."""
    seed(m, "pump note", [PRD1])
    [hit] = search(m, {"content": {"contains": "pump"}})
    assert "matched_on" not in hit


def test_widen_keeps_the_other_top_level_keys(m):
    """C23 (AC 24, AD-047)."""
    seed(m, "prd1 pump", [PRD1])
    seed(m, "general pump")
    seed(m, "general fan")
    hits = search(m, {"entities": [PRD1], "content": {"contains": "pump"}}, widen=True)
    assert [(h["content"], h["matched_on"]) for h in hits] == [("prd1 pump", "machine:PRD_1"),
                                                               ("general pump", "general")]


def test_widen_needs_top_level_entities(m, monkeypatch):
    """C24 (AC 25, AD-047)."""
    monkeypatch.setattr(m.store, "unit_of_work", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("called")))
    with pytest.raises(ValidationError, match="widen"):
        search(m, {"areas": "x"}, widen=True)
    with pytest.raises(ValidationError, match="AND / OR / NOT"):
        search(m, {"AND": [{"entities": [PRD1]}]}, widen=True)
    with pytest.raises(ValidationError, match="AND / OR / NOT"):
        search(m, {"entities": [PRD1], "NOT": {"entities": [PRD2]}}, widen=True)


def test_guard_counts_positive_branches_only(m):
    """C25 (AC 26): a `require_filter` type named only under NOT stays out."""
    p1 = {"type": "patient", "id": "p1"}
    seed(m, "patient p1 allergy", [p1])
    seed(m, "patient p2 allergy", [{"type": "patient", "id": "p2"}])
    seed(m, "a plain fact")
    assert contents(search(m, {"NOT": {"entities": [p1]}})) == {"a plain fact"}
    assert contents(search(m, {"OR": [{"entities": [p1]}, {"types": ["fact"]}]})) == {"patient p1 allergy", "a plain fact"}
    assert contents(search(m, {"entity_type": {"in": ["patient"]}})) == {"patient p1 allergy", "patient p2 allergy"}


def test_guard_scoped_to_the_named_subject(m):
    """C37 (AD-052): naming patient p1 lets p1's memories through, never p2's - under OR, and in another tier."""
    p1, p2, m1 = {"type": "patient", "id": "p1"}, {"type": "patient", "id": "p2"}, {"type": "machine", "id": "M1"}
    seed(m, "patient p1 allergy", [p1])
    seed(m, "patient p2 allergy", [p2])
    seed(m, "a plain fact")
    seed(m, "m1 used on p2", [m1, p2])
    seed(m, "m1 alone", [m1])
    assert contents(search(m, {"OR": [{"entities": [p1]}, {"types": ["fact"]}]})) == {
        "patient p1 allergy", "a plain fact", "m1 alone"}
    assert contents(search(m, {"entities": [p1, m1]})) == {"patient p1 allergy", "m1 alone"}
    assert contents(search(m, {"entities": [p2, m1]})) == {"patient p2 allergy", "m1 used on p2", "m1 alone"}
    assert contents(search(m, {"entity_type": "patient"})) == {"patient p1 allergy", "patient p2 allergy", "m1 used on p2"}


def test_naive_datetime_is_utc(m):
    """C35: a bound with no timezone is UTC."""
    seed(m, "at noon", observed_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc))
    seed(m, "a minute before", observed_at=datetime(2026, 10, 1, 11, 59, tzinfo=timezone.utc))
    assert contents(search(m, {"observed_at": {"gte": "2026-10-01T12:00:00"}})) == {"at noon"}


# --- S4: CLI ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """A SQLite ledger the CLI and `Memory` share, seeded with PRD / LINE machines and a guarded patient."""
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(16))
    path = tmp_path / "memhub.yaml"
    path.write_text(yaml.safe_dump({
        "project_prefix": "flt", "database_url": f"sqlite:///{tmp_path / 'm.db'}", "workspace_default": WS,
        "scopes": ["user", "workspace"], "types": {"fact": {"class": "memhub.types:Fact"}},
        "embeddings": {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16},
        "entities": {"machine": "isolate", "patient": {"isolate": True, "require_filter": True}},
    }))
    mem = Memory(db=f"sqlite:///{tmp_path / 'm.db'}", embedder=FakeEmbeddings(16), config=path)
    mem.init()
    ids = {}
    for e in (PRD1, PRD2, LINE1):
        ids[e["id"]] = str(seed(mem, f"vibration {e['id']}", [e])["memory_id"])
    seed(mem, "vibration patient", [{"type": "patient", "id": "p1"}])
    return SimpleNamespace(path=path, memory=mem, ids=ids)


def run(db, *args: str):
    return runner.invoke(cli.app, [*args, "--config", str(db.path), "--ws", WS, "--user", "alice"])


def test_cli_search_filter(cli_db):
    """C26 (AC 27)."""
    flt = {"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}
    out = run(cli_db, "search", "vibration", "--filter", json.dumps(flt))
    assert out.exit_code == 0, out.output
    expected = {str(r["memory_id"]) for r in cli_db.memory.search("vibration", filters=flt, top_k=10, **ALICE)}
    assert {str(r["memory_id"]) for r in json.loads(out.stdout)} == expected == {cli_db.ids["PRD_1"], cli_db.ids["PRD_2"]}


def test_cli_entity_flags(cli_db):
    """C27 (AC 28)."""
    listed = run(cli_db, "list", "--entity", "machine:PRD_1", "--entity", "machine:PRD_2")
    assert listed.exit_code == 0, listed.output
    assert {r["content"] for r in json.loads(listed.stdout)} == {"vibration PRD_1", "vibration PRD_2"}
    found = run(cli_db, "search", "vibration", "--entity", "machine:PRD_1", "--entity", "machine:PRD_2")
    assert [r["content"] for r in json.loads(found.stdout)] == ["vibration PRD_1", "vibration PRD_2"]
    found = run(cli_db, "search", "vibration", "--entity", "machine:PRD_2", "--entity", "machine:PRD_1")
    assert [r["content"] for r in json.loads(found.stdout)] == ["vibration PRD_2", "vibration PRD_1"]


@pytest.mark.parametrize("args, reason", [
    (["--filter", "{bad"], "not valid JSON"),
    (["--filter", '{"tags": 1}'], "tags"),
    (["--entity", "machinePRD_1"], "TYPE:ID"),
    (["--entity", "machine:PRD_1", "--filter", '{"entities": [{"type": "machine", "id": "PRD_2"}]}'], "once"),
])
@pytest.mark.parametrize("command", ["search", "list"])
def test_cli_bad_filter_exits_1(cli_db, command, args, reason):
    """C28 (AC 29): exit 1, the reason on stderr, nothing on stdout."""
    out = run(cli_db, command, *(["vibration"] if command == "search" else []), *args)
    assert out.exit_code == 1
    assert reason in out.stderr and out.stdout == ""


# --- S4: tools -------------------------------------------------------------------------------------------------------

def _mw(m: Memory) -> MemoryMiddleware:
    return MemoryMiddleware(m.service, workspace_from=lambda c: c["ws"], user_from=lambda c: c["user"])


def _mw_search(m: Memory, *args, **kw) -> str:
    tool = next(t for t in _mw(m).tools if t.name == "search_memory").func
    return tool(*args, runtime=SimpleNamespace(context={"ws": WS, "user": "alice"}), **kw)


def _mcp(m: Memory, *calls):
    from mcp import Client

    from memhub.mcp_server import build_server

    server = build_server(m, workspace_id=WS, user_id="alice", run_id="mcp-x")

    async def go():
        async with Client(server) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    return anyio.run(go)


def test_tools_take_filters(m):
    """C29 (AC 30): an object or its JSON text, through the toolkit, the middleware and MCP."""
    for e in (PRD1, PRD2, LINE1):
        seed(m, f"note {e['id']}", [e])
    flt = {"entities": {"type": "machine", "id": {"prefix": "PRD_"}}}
    kit = m.tools(workspace_id=WS, user_id="alice", run_id="r")
    want = {"note PRD_1", "note PRD_2"}
    for given in (flt, json.dumps(flt)):
        assert {h["content"] for h in json.loads(kit.call("search_memory", {"query": "note", "filters": given}))} == want
        text = _mw_search(m, "note", filters=given)
        assert "note PRD_1" in text and "note PRD_2" in text and "LINE_1" not in text
    [obj, txt] = _mcp(m, ("search_memory", {"query": "note", "filters": flt}),
                      ("search_memory", {"query": "note", "filters": json.dumps(flt)}))
    for result in (obj, txt):
        assert {h["content"] for h in json.loads(result.content[0].text)} == want


def test_tools_report_bad_filters(m, monkeypatch):
    """C30 (AC 31): an error result naming the key, no search run, no exception."""
    calls = []
    monkeypatch.setattr(m.service, "search", lambda *a, **kw: calls.append(1) or [])
    kit = m.tools(workspace_id=WS, user_id="alice", run_id="r")
    bad = {"tags": "x"}
    assert "tags" in json.loads(kit.call("search_memory", {"query": "q", "filters": bad}))["error"]
    assert _mw_search(m, "q", filters=bad).startswith("invalid filters:") and "tags" in _mw_search(m, "q", filters=bad)
    [result] = _mcp(m, ("search_memory", {"query": "q", "filters": bad}))
    assert "tags" in json.loads(result.content[0].text)["error"]
    assert calls == []


def test_guard_holds_without_filter_everywhere(m, cli_db):
    """C31 (AC 32): no filter -> no `require_filter` memory, from every entry point."""
    seed(m, "note patient", [{"type": "patient", "id": "p1"}])
    seed(m, "note plain")
    kit = m.tools(workspace_id=WS, user_id="alice", run_id="r")
    assert {h["content"] for h in json.loads(kit.call("search_memory", {"query": "note"}))} == {"note plain"}
    assert "patient" not in _mw_search(m, "note")
    [result] = _mcp(m, ("search_memory", {"query": "note"}))
    assert {h["content"] for h in json.loads(result.content[0].text)} == {"note plain"}
    out = run(cli_db, "search", "vibration")
    assert out.exit_code == 0 and "patient" not in out.stdout and "PRD_1" in out.stdout


def test_tool_schemas_declare_filters(m):
    """C32 (AC 33): `filters` optional, described with its keys and an example."""
    kit = m.tools(workspace_id=WS, user_id="alice", run_id="r")
    params = kit.tool_specs()[0]["function"]["parameters"]
    mw_tool = next(t for t in _mw(m).tools if t.name == "search_memory")
    from mcp import Client

    from memhub.mcp_server import build_server

    async def schema():
        async with Client(build_server(m, workspace_id=WS, user_id="alice", run_id="x")) as client:
            return next(t.input_schema for t in (await client.list_tools()).tools if t.name == "search_memory")

    mcp_schema = anyio.run(schema)
    for required, described in ((params["required"], params["properties"]["filters"]["description"]),
                                (mcp_schema.get("required", []), json.dumps(mcp_schema["properties"]["filters"])),
                                ([], mw_tool.description)):
        assert "filters" not in required
        for word in ("entities", "areas", "AND", "Example", "PRD_"):
            assert word in described, word
    assert "filters" in mw_tool.args and "filters" not in mw_tool.tool_call_schema.model_json_schema().get("required", [])


def test_docs_reference_the_grammar():
    """C33 (AC 34)."""
    subjects = (ROOT / "docs" / "subjects.md").read_text()
    assert "take no filter yet" not in subjects and "filters.md" in subjects
    ref = (ROOT / "docs" / "filters.md").read_text()
    for word in ("`entities`", "`entity_type`", "`types`", "`areas`", "`observed_at`", "`valid_until`", "`content`",
                 "`in`", "`prefix`", "`contains`", "`gte`", "`lte`", "`AND`", "`OR`", "`NOT`"):
        assert word in ref, word


def test_sqlite_borrowed_connection_content_filter(config):
    """C34: SQLite folds case with Python's `str.lower` (non-ASCII too) on a borrowed connection, left open."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    mem = Memory(db=conn, embedder=FakeEmbeddings(16), config=config)
    mem.init()
    seed(mem, "Ölpumpe defekt")
    seed(mem, "fan ok")
    assert contents(search(mem, {"content": {"contains": "ölPUMPE"}})) == {"Ölpumpe defekt"}
    assert conn.execute("SELECT 1").fetchone() == (1,)
