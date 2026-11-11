"""PR 1 demo: an app opens its own psycopg pool, builds `Memory` with its models, and runs the whole flow."""
from __future__ import annotations

import uuid

import pytest
from psycopg_pool import ConnectionPool

from memhub import Memory, ValidationError
from tests.fakes import FakeChatModel, FakeEmbeddings


@pytest.fixture
def app_pool(pg_dsn):
    pool = ConnectionPool(pg_dsn, min_size=1, max_size=4, open=True)  # the app's pool, not memhub's
    yield pool
    pool.close()


@pytest.fixture
def memory(app_pool) -> Memory:
    m = Memory(db=app_pool, llm=FakeChatModel(), embedder=FakeEmbeddings(16), config={"project_prefix": f"e{uuid.uuid4().hex[:8]}"})
    m.init()
    return m


def test_pr1_demo(memory, app_pool):
    anna = memory.scope(workspace_id="acme", user_id="anna", run_id="trace-1")
    admin = memory.scope(workspace_id="acme", user_id="lead", run_id="trace-2", roles=["workspace_admin"])

    added = anna.add({"content": "Compressor C3 trips when the inlet filter is clogged"}, type="fact")
    [event] = added.results
    assert (event.event, event.status) == ("ADD", "active")

    hits = anna.search("compressor C3 trips")
    assert hits and str(hits[0]["memory_id"]) == event.id

    candidate = anna.promote(event.id)
    assert (candidate.event, candidate.status, candidate.scope) == ("PENDING", "candidate", "workspace")
    assert [str(r["id"]) for r in admin.queue()] == [candidate.version_id]

    approved = admin.approve(candidate.id)
    assert approved.status == "active"
    bob_hits = memory.search("compressor C3 trips", workspace_id="acme", user_id="bob", run_id="trace-3")
    assert [str(r["memory_id"]) for r in bob_hits] == [candidate.id]  # shared knowledge reaches another user

    erased = anna.delete_all()
    assert erased["deleted"] == 1  # anna's own row; the approved workspace copy stays
    assert [(str(r["memory_id"]), r["scope"]) for r in anna.get_all()] == [(candidate.id, "workspace")]

    assert app_pool.closed is False  # memhub handed every connection back
    with app_pool.connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_call_without_user_id_fails(memory):
    with pytest.raises(ValidationError, match="user_id"):
        memory.search("anything", workspace_id="acme", run_id="trace-1")
