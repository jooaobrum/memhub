"""PR 3 demo: an app's tests run memhub on `memory://` - no Docker, no Postgres - with the review flow intact."""
from __future__ import annotations

import psycopg
import pytest

from memhub import Memory
from memhub.storage.rules import EmbeddingMismatch
from tests.fakes import FakeChatModel, FakeEmbeddings

MEM = "memory" + "://"
ADMIN = ["workspace_admin"]


@pytest.fixture
def no_postgres(monkeypatch):
    def refuse(*_a, **_kw):
        raise AssertionError("memory:// must not open a Postgres connection")

    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(lambda cls, *a, **kw: refuse()))


def _memory(url: str = MEM, dims: int = 16) -> Memory:
    return Memory(db=url, llm=FakeChatModel(), embedder=FakeEmbeddings(dims=dims))


def test_app_flow_on_memory_without_postgres(no_postgres):
    m = _memory()
    m.init()
    anna = m.scope(workspace_id="acme", user_id="anna", run_id="run-1")
    added = anna.add({"content": "Prefers answers in Portuguese"}, type="fact")
    [event] = added.results
    assert (event.event, event.status, event.scope) == ("ADD", "active", "user")

    hits = anna.search("answers in Portuguese")
    assert str(hits[0]["memory_id"]) == event.id

    pending = anna.promote(event.id)
    assert (pending.event, pending.status, pending.scope) == ("PENDING", "candidate", "workspace")
    approved = m.approve(pending.id, workspace_id="acme", user_id="lead", run_id="run-2", roles=ADMIN)
    assert approved.status == "active"

    shared = [r for r in anna.get_all() if r["scope"] == "workspace"]
    assert [(r["content"], r["status"]) for r in shared] == [("Prefers answers in Portuguese", "active")]

    assert anna.delete_all() == {"deleted": 1, "runs": 0}
    assert [r["scope"] for r in anna.get_all()] == ["workspace"]  # the approved workspace copy stays


def test_memory_urls_isolate_or_share_by_name(no_postgres):
    first, second = _memory(), _memory()
    for m in (first, second):
        m.init()
    first.add("only in the first", workspace_id="w", user_id="u", run_id="r")
    assert second.get_all(workspace_id="w", user_id="u", run_id="r") == []

    one, other = _memory(MEM + "shared"), _memory(MEM + "shared")
    one.init()
    event = one.add("in the shared ledger", workspace_id="w", user_id="u", run_id="r").results[0]
    assert other.get(event.id, workspace_id="w", user_id="u", run_id="r")["content"] == "in the shared ledger"

    a, b = _memory(MEM + "a"), _memory(MEM + "b")
    a.init(), b.init()
    a.add("only in a", workspace_id="w", user_id="u", run_id="r")
    assert b.get_all(workspace_id="w", user_id="u", run_id="r") == []


def test_a_named_url_keeps_one_ledger_per_table_prefix(no_postgres):
    def named(prefix: str) -> Memory:
        return Memory(db=MEM + "x", llm=FakeChatModel(), embedder=FakeEmbeddings(), config={"project_prefix": prefix})

    app_a, app_b, app_a_again = named("appa"), named("appb"), named("appa")
    for m in (app_a, app_b):
        m.init()
    app_a.add("only in appa", workspace_id="w", user_id="u", run_id="r")
    assert app_b.get_all(workspace_id="w", user_id="u", run_id="r") == []
    assert [r["content"] for r in app_a_again.get_all(workspace_id="w", user_id="u", run_id="r")] == ["only in appa"]


def test_init_required_and_dims_checked(no_postgres):
    fresh = _memory()
    with pytest.raises(EmbeddingMismatch, match=r"init\(\)"):
        fresh.add("too early", workspace_id="w", user_id="u", run_id="r")

    built = _memory(MEM + "dims", dims=16)
    built.init()
    other = _memory(MEM + "dims", dims=8)
    with pytest.raises(EmbeddingMismatch):
        other.get_all(workspace_id="w", user_id="u", run_id="r")
