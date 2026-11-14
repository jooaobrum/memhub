"""Review primitives (issue #19, S2): what an app needs to build review from the chat on public `Memory` calls.

`check_fields`, `propose` (candidate, reviewed write, correction, rejected repeat) and the `propose_memory` tool of
the toolkit, the middleware and `memhub mcp`. The backend is in the test id."""
from __future__ import annotations

import inspect
import json
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client

from memhub import Memory, NotFound, PermissionDenied, ValidationError
from memhub.config import TypeConfig
from memhub.mcp_server import build_server
from memhub.middleware import MemoryMiddleware
from memhub.storage.rules import as_vector_list
from tests.fakes import FakeEmbeddings, LookupEmbeddings, vec_at

ROOT = Path(__file__).resolve().parents[1]
WS = "acme"
ANNA = dict(workspace_id=WS, user_id="anna", run_id="r")
BOB = dict(workspace_id=WS, user_id="bob", run_id="r")
REVIEWER = dict(workspace_id=WS, user_id="rev", run_id="r", roles=["workspace_admin"])
EVIDENCE = [{"message_id": "m1", "quote": "we use Auth0"}]
REJECTED = "a reviewer already rejected this memory"


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def build(request, settings, tmp_path):
    """`build(embedder)` -> an initialized `Memory` on this test's backend."""
    def make(embedder=None) -> Memory:
        cfg = settings.model_copy(deep=True, update={"project_prefix": f"p{uuid.uuid4().hex[:8]}"})
        cfg.types = {**cfg.types, "reading": TypeConfig(fields={"count": "int"})}
        if request.param == "memory":
            db = "memory" + "://"
        elif request.param == "sqlite":
            db = f"sqlite:///{tmp_path}/{uuid.uuid4().hex[:6]}.db"
        else:
            db = request.getfixturevalue("pg_dsn")
        m = Memory(db=db, embedder=embedder or FakeEmbeddings(dims=16), config=cfg)
        m.init()
        return m
    return make


@pytest.fixture
def m(build) -> Memory:
    return build()


def _rows(m: Memory, event) -> list[dict]:
    return m.history(event.id, **REVIEWER)


# --- C18 -----------------------------------------------------------------------------------------------------------

def test_check_fields(m):
    out = m.check_fields("fact", {"content": "x"}, **ANNA)
    assert out["content"] == "x" and out["payload"]["content"] == "x"
    with pytest.raises(ValidationError, match="count"):
        m.check_fields("reading", {"content": "x", "count": "abc"}, **ANNA)
    assert m.queue(**REVIEWER) == [] and m.get_all(**ANNA) == []


# --- C19 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scope", ["user", "workspace"])
def test_propose_writes_a_candidate(m, scope):
    e = m.propose("fact", {"content": "Login uses Auth0"}, evidence=EVIDENCE, scope=scope, **ANNA)
    assert (e.event, e.status, e.scope, e.content) == ("PENDING", "candidate", scope, "Login uses Auth0")
    assert [str(r["id"]) for r in m.queue(**REVIEWER)] == [e.version_id]


# --- C20 -----------------------------------------------------------------------------------------------------------

def test_propose_approved_is_active(m):
    own = m.propose("fact", {"content": "Login uses Okta"}, evidence=EVIDENCE, approve=True, **ANNA)
    shared = m.propose("fact", {"content": "Billing uses Adyen"}, evidence=EVIDENCE, scope="workspace", approve=True,
                       **REVIEWER)
    for event, reviewer in ((own, "anna"), (shared, "rev")):
        assert (event.event, event.status) == ("ADD", "active")
        [row] = _rows(m, event)
        assert (row["status"], row["verified"], row["reviewed_by"]) == ("active", True, reviewer)
    assert m.queue(**REVIEWER) == []


# --- C21 -----------------------------------------------------------------------------------------------------------

def test_propose_approve_not_allowed_falls_back(m):
    e = m.propose("fact", {"content": "Billing uses Adyen"}, evidence=EVIDENCE, scope="workspace", approve=True, **ANNA)
    assert (e.event, e.status) == ("PENDING", "candidate")
    assert len(m.queue(**REVIEWER)) == 1


# --- C22 -----------------------------------------------------------------------------------------------------------

def test_correction_updates_the_candidate(m):
    first = m.propose("fact", {"content": "Login uses Auth0"}, evidence=EVIDENCE, **ANNA)
    fixed = m.propose("fact", {"content": "Login uses Okta", "entities": [{"type": "machine", "id": "sso"}]},
                      evidence=EVIDENCE, memory_id=first.id, **ANNA)
    assert (fixed.id, fixed.version_id, fixed.version, fixed.status) == (first.id, first.version_id, 1, "candidate")
    [row] = _rows(m, first)
    assert (row["content"], row["payload"]["content"]) == ("Login uses Okta", "Login uses Okta")
    assert row["entities"] == [{"type": "machine", "id": "sso"}]
    with m.store.unit_of_work() as uow:
        stored = uow.memories.get_by_id(row["id"])
    assert as_vector_list(stored["embedding"]) == pytest.approx(m.embedder.embed_query("Login uses Okta"))
    assert len(m.queue(**REVIEWER)) == 1


# --- C23 -----------------------------------------------------------------------------------------------------------

def test_bad_memory_id(m):
    fields = {"content": "Login uses Okta"}
    with pytest.raises(NotFound):
        m.propose("fact", fields, memory_id=str(uuid.uuid4()), **ANNA)

    annas = m.propose("fact", {"content": "Login uses Auth0"}, **ANNA)
    with pytest.raises(PermissionDenied):
        m.propose("fact", fields, memory_id=annas.id, **BOB)
    with pytest.raises(PermissionDenied):
        m.propose("fact", fields, memory_id=annas.id, workspace_id="other", user_id="anna", run_id="r")

    active = m.add({"content": "Deploys on Fridays"}, **ANNA).results[0]
    rejected = m.propose("fact", {"content": "Mail uses SES"}, **ANNA)
    m.reject(rejected.id, **REVIEWER)
    archived = m.add({"content": "Search uses Elastic"}, **ANNA).results[0]
    m.delete(archived.id, **ANNA)
    for target in (active, rejected, archived):
        with pytest.raises(ValidationError, match="not a pending candidate"):
            m.propose("fact", fields, memory_id=target.id, **ANNA)

    written = [r["content"] for e in (annas, active, rejected, archived) for r in m.history(e.id, **REVIEWER)]
    assert "Login uses Okta" not in written and len(written) == 4


# --- C24 -----------------------------------------------------------------------------------------------------------

def test_rejected_repeat(build):
    texts = {"rejected": vec_at(1.0), "at": vec_at(0.92), "below": vec_at(0.91), "same": vec_at(1.0)}
    m = build(LookupEmbeddings(texts, vec_at(0.0)))
    assert m.settings.reconcile.duplicate == 0.92
    no = m.propose("fact", {"content": "rejected"}, **ANNA)
    m.reject(no.id, **REVIEWER)

    with pytest.raises(ValidationError, match=f"^{REJECTED}$"):
        m.propose("fact", {"content": "at"}, **ANNA)
    assert m.propose("fact", {"content": "below"}, **ANNA).status == "candidate"
    assert m.propose("fact", {"content": "same"}, **BOB).status == "candidate"
    assert m.propose("reading", {"content": "same", "count": 1}, **ANNA).status == "candidate"
    assert m.propose("fact", {"content": "at"}, user_requested=True, **ANNA).status == "candidate"
    assert sorted(r["content"] for r in m.queue(**REVIEWER)) == ["at", "below", "same", "same"]

    # a correction may not rewrite a draft into the rejected text either, unless the user asked
    draft = m.propose("fact", {"content": "below"}, **ANNA)
    with pytest.raises(ValidationError, match=f"^{REJECTED}$"):
        m.propose("fact", {"content": "same"}, memory_id=draft.id, **ANNA)
    assert m.propose("fact", {"content": "same"}, memory_id=draft.id, user_requested=True, **ANNA).content == "same"


def test_rejected_repeat_workspace_scope(build):
    """The workspace is the owner of a workspace memory: another user of that workspace is refused, another
    workspace is not."""
    m = build(LookupEmbeddings({"shared": vec_at(1.0), "close": vec_at(0.95)}, vec_at(0.0)))
    no = m.propose("fact", {"content": "shared"}, scope="workspace", **ANNA)
    m.reject(no.id, **REVIEWER)
    with pytest.raises(ValidationError, match=f"^{REJECTED}$"):
        m.propose("fact", {"content": "close"}, scope="workspace", **BOB)
    other = m.propose("fact", {"content": "close"}, scope="workspace", workspace_id="other", user_id="bob", run_id="r")
    assert other.status == "candidate"


# --- C25 -----------------------------------------------------------------------------------------------------------

def _mw_call(m: Memory, **args) -> dict:
    mw = m.middleware(workspace_id=WS, user_id="anna", run_id="r")
    func = next(t for t in mw.tools if t.name == "propose_memory").func
    writer_events: list = []
    runtime = SimpleNamespace(context=None, stream_writer=writer_events.append)
    out = func(args["type"], args["fields"], args.get("evidence", []), runtime, scope=args.get("scope", "user"),
               memory_id=args.get("memory_id"), user_requested=args.get("user_requested", False))
    return json.loads(out)


def _kit_call(m: Memory, **args) -> dict:
    return json.loads(m.tools(**ANNA).call("propose_memory", args))


def _mcp_call(m: Memory, **args) -> dict:
    server = build_server(m, workspace_id=WS, user_id="anna", run_id="mcp-x")

    async def run():
        async with Client(server) as client:
            return await client.call_tool("propose_memory", args)

    result = anyio.run(run)
    assert not result.is_error, result
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("call", [_kit_call, _mw_call, _mcp_call], ids=["toolkit", "middleware", "mcp"])
def test_tool_contract(build, call):
    m = build(LookupEmbeddings({"no": vec_at(1.0), "no again": vec_at(1.0)}, vec_at(0.0)))
    out = call(m, type="fact", fields={"content": "Login uses Auth0"}, evidence=EVIDENCE)
    assert set(out) == {"id", "event", "status", "content"}
    assert (out["event"], out["status"], out["content"]) == ("PENDING", "candidate", "Login uses Auth0")

    fixed = call(m, type="fact", fields={"content": "Login uses Okta"}, memory_id=out["id"])
    assert (fixed["id"], fixed["status"], fixed["content"]) == (out["id"], "candidate", "Login uses Okta")

    assert "rejected" in call(m, type="reading", fields={"content": "x", "count": "abc"})
    assert "rejected" in call(m, type="fact", fields={"content": "x"}, memory_id=str(uuid.uuid4()))
    no = m.propose("fact", {"content": "no"}, **ANNA)
    m.reject(no.id, **REVIEWER)
    assert call(m, type="fact", fields={"content": "no again"}) == {"rejected": REJECTED}
    assert call(m, type="fact", fields={"content": "no again"}, user_requested=True)["status"] == "candidate"
    assert m.get_all(**ANNA) == []  # a tool never writes active


# --- C26 -----------------------------------------------------------------------------------------------------------

def test_middleware_signature_unchanged():
    assert list(inspect.signature(MemoryMiddleware.__init__).parameters) == [
        "self", "service", "workspace_from", "user_from", "k", "actor"]
    assert list(inspect.signature(Memory.middleware).parameters) == [
        "self", "workspace_id", "user_id", "run_id", "agent_id", "roles", "workspace_from", "user_from", "k"]


# --- C33 -----------------------------------------------------------------------------------------------------------

def test_langchain_pins():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "langchain>=1.0" in project["dependencies"]
    assert "langchain>=1.4.6" in project["optional-dependencies"]["dev"]
