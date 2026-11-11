"""What only the SQLite backend promises: the accepted `db` values, a borrowed connection left alone, one writer
at a time, the storage types, both vector paths, and loud errors for what SQLite cannot do. No Docker."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memhub import Memory
from memhub.connection import SqliteSource, connection_source, sqlite_path
from memhub.service import ValidationError
from memhub.storage import Capability, DeferredBackend, open_backend
from memhub.storage import contract
from memhub.storage.contract import DIMS, MODEL, NOW, _cand, _ingest, _ledger, _line, add, contents, vec
from memhub.storage.memory import InMemoryBackend
from memhub.storage.rules import MEMORY_COLUMNS, RUN_COLUMNS, EmbeddingMismatch, StoreError, area_link
from memhub.storage.sqlite import VEC_ENV, SqliteBackend
from tests.fakes import FakeChatModel, FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]
IDS = dict(workspace_id="acme", user_id="anna", run_id="run-1")


def _memory(db, dims: int = DIMS, **kw) -> Memory:
    return Memory(db=db, llm=FakeChatModel(), embedder=FakeEmbeddings(dims=dims), **kw)


def _backend(tmp_path, name: str = "m.db") -> SqliteBackend:
    b = SqliteBackend(f"sqlite:///{tmp_path / name}", "t")
    b.init(embedding_model=MODEL, dims=DIMS)
    return b


def _tables(path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    finally:
        conn.close()


# --- S1: the same ports -----------------------------------------------------------------------------------------


def test_db_kinds_build_a_sqlite_backend_and_run_no_sql(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    borrowed = sqlite3.connect(tmp_path / "borrowed.db")
    opened = []

    def factory():
        opened.append(sqlite3.connect(tmp_path / "factory.db"))
        return opened[-1]

    kinds = {
        "connection": (borrowed, tmp_path / "borrowed.db"),
        "relative": ("sqlite:///relative.db", tmp_path / "relative.db"),
        "absolute": (f"sqlite:///{tmp_path / 'absolute.db'}", tmp_path / "absolute.db"),
        "callable": (factory, tmp_path / "factory.db"),
        "memory": ("sqlite:///:memory:", None),
    }
    for kind, (db, path) in kinds.items():
        m = _memory(db)
        if kind == "callable":  # what it returns decides the backend, and building Memory calls nothing (AD-002)
            assert isinstance(m.store, DeferredBackend) and opened == []
            assert isinstance(m.store.resolve(), SqliteBackend) and len(opened) == 1
        else:
            assert isinstance(m.store, SqliteBackend), kind
            assert isinstance(connection_source(db), SqliteSource), kind
        if path is not None:
            assert not path.exists() or _tables(path) == [], kind  # nothing created before init()
        m.init()
        event = m.add("Prefers tea", **IDS).results[0]
        assert m.get(event.id, **IDS)["content"] == "Prefers tea", kind
        if path is not None:
            assert "memhub_memory" in _tables(path), kind
    assert all(_closed(c) for c in opened)  # what the callable opened, memhub closed
    assert borrowed.execute("SELECT 1").fetchone() == (1,)  # what the app opened, it did not


def _closed(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
        return False
    except sqlite3.ProgrammingError:
        return True


def test_sqlite_url_forms(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert sqlite_path("sqlite:///m.db") == "m.db"
    assert sqlite_path("sqlite:////tmp/x/m.db") == "/tmp/x/m.db"
    assert sqlite_path("sqlite:///:memory:") == ":memory:"
    relative = _memory("sqlite:///m.db")
    relative.init()
    assert (tmp_path / "m.db").exists()
    for bad in ("sqlite://", "sqlite:m.db", "sqlite:///", "sqlite://m.db", "sqlite:////", "sqlite:///  "):
        with pytest.raises(TypeError, match="sqlite:///path"):
            open_backend(bad)
        with pytest.raises(TypeError, match="sqlite:///path"):
            _memory(bad)


def test_borrowed_connection_stays_open_and_usable(tmp_path):
    conn = sqlite3.connect(tmp_path / "app.db")
    as_dicts = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))  # noqa: E731
    conn.row_factory = as_dicts
    conn.execute("CREATE TABLE app (x)")
    conn.commit()
    m = _memory(conn)
    m.init()
    m.add("Prefers tea", **IDS)
    assert conn.execute("SELECT 1 AS one").fetchone() == {"one": 1}
    assert conn.row_factory is as_dicts and not conn.in_transaction

    with pytest.raises(ValidationError):
        m.add("x", type="no-such-type", **IDS)
    with pytest.raises(sqlite3.IntegrityError):  # a failure inside memhub's own transaction
        with m.store.unit_of_work() as uow:
            first = add(uow, "first")
            add(uow, "second", memory_id=first["memory_id"], version=2)
    assert conn.execute("SELECT 1 AS one").fetchone() == {"one": 1} and not conn.in_transaction
    assert [r["content"] for r in m.get_all(**IDS)] == ["Prefers tea"]  # the failed unit of work left nothing

    # inside the app's own transaction memhub neither commits nor rolls it back
    conn.execute("INSERT INTO app VALUES (1)")
    assert conn.in_transaction
    m.add("Inside the app's transaction", **IDS)
    with pytest.raises(RuntimeError):
        with m.store.unit_of_work() as uow:
            add(uow, "rolled back to memhub's savepoint")
            raise RuntimeError("boom")
    assert conn.in_transaction
    assert conn.execute("SELECT count(*) AS n FROM app").fetchone() == {"n": 1}
    assert len(m.get_all(**IDS)) == 2
    conn.rollback()  # the app's decision takes memhub's write with it
    assert conn.execute("SELECT count(*) AS n FROM app").fetchone() == {"n": 0}
    assert [r["content"] for r in m.get_all(**IDS)] == ["Prefers tea"]
    conn.close()


def test_second_writer_waits_for_the_first(tmp_path):
    first, second = _backend(tmp_path), SqliteBackend(f"sqlite:///{tmp_path / 'm.db'}", "t")
    statements: list[str] = []
    started, seen, errors, waited = threading.Event(), [], [], []

    def a():
        with first.unit_of_work() as uow:
            add(uow, "from A")
            started.set()
            time.sleep(0.3)

    def b():
        started.wait()
        t0 = time.monotonic()
        try:
            with second.unit_of_work() as uow:
                waited.append(time.monotonic() - t0)
                seen.extend(contents(uow.memories.list_memories()))
                add(uow, "from B")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ta, tb = threading.Thread(target=a), threading.Thread(target=b)
    ta.start(), tb.start()
    ta.join(), tb.join()
    assert errors == []
    assert seen == ["from A"] and waited[0] >= 0.2

    conn = sqlite3.connect(tmp_path / "traced.db")
    conn.set_trace_callback(statements.append)
    traced = SqliteBackend(conn, "t")
    traced.init(embedding_model=MODEL, dims=DIMS)
    statements.clear()
    with traced.unit_of_work() as uow:
        add(uow, "traced")
    in_tx = [s for s in statements if s != "SELECT vec_version()"]  # the extension probe runs before the transaction
    assert in_tx[0] == "BEGIN IMMEDIATE" and in_tx[-1] == "COMMIT" and in_tx.count("BEGIN IMMEDIATE") == 1


def test_schema_columns_match_the_reference_and_init_is_idempotent(tmp_path):
    backend = _backend(tmp_path)
    conn = sqlite3.connect(tmp_path / "m.db")

    def columns(table: str) -> tuple[str, ...]:
        return tuple(r[1] for r in conn.execute(f"PRAGMA table_info({table})"))

    def snapshot() -> list[tuple]:
        return sorted(conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()) + sorted(
            conn.execute("SELECT key, value FROM t_memhub_meta").fetchall())

    assert set(_tables(tmp_path / "m.db")) == {"t_memory", "t_memory_runs", "t_memhub_meta"}
    assert sorted(columns("t_memory")) == sorted(MEMORY_COLUMNS) and len(columns("t_memory")) == len(MEMORY_COLUMNS)
    assert sorted(columns("t_memory_runs")) == sorted(RUN_COLUMNS)
    assert columns("t_memhub_meta") == ("key", "value")
    before = snapshot()
    with backend.unit_of_work() as uow:
        add(uow, "kept across init")
    backend.init(embedding_model=MODEL, dims=DIMS)
    assert snapshot() == before
    with backend.unit_of_work() as uow:
        assert contents(uow.memories.list_memories()) == ["kept across init"]
    conn.close()


def test_row_values_round_trip(tmp_path):
    backend = _backend(tmp_path)
    paris = timezone(timedelta(hours=2))
    until = datetime(2026, 3, 1, 9, 30, 15, 123456, tzinfo=paris)
    other = uuid.uuid4()
    embedding = [0.1 * i for i in range(DIMS)]
    wrote = dict(
        payload={"content": "c", "key": "drink", "nested": {"n": [1, 2.5, None, True]}},
        entities=[{"type": "machine", "id": "P1"}], evidence=[{"message_id": "m1", "quote": "é ü"}],
        links=[area_link(other)], conflicts_with=other, valid_until=until, observed_at=NOW, verified=True,
        score=0.75, run_id="run-9", agent_id="agent-1", durability="stable",
    )
    with backend.unit_of_work() as uow:
        row = add(uow, "c", embedding, type="preference", **wrote)
        run = uow.runs.upsert_run(
            run_id=other, source="jsonl", thread_id="t", workspace_id="ws", user_id="alice", first_message_id="a",
            last_message_id="b", last_message_at=until, final_pass=True, signals=[{"kind": "thumb"}], dropped=[{"reason": "x"}],
        )
    with backend.unit_of_work() as uow:
        again = uow.memories.get_by_id(row["id"])
        watermark = uow.runs.get_watermark(source="jsonl", thread_id="t")
    with sqlite3.connect(tmp_path / "m.db") as raw:  # door 3: stored as UTC text, so text order is time order
        stored = raw.execute("SELECT valid_until, observed_at FROM t_memory WHERE id = ?", (str(row["id"]),)).fetchone()
    assert stored == (until.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                      NOW.isoformat(timespec="microseconds"))
    assert stored[0].endswith("+00:00")
    for got in (row, again):
        assert set(got) == set(MEMORY_COLUMNS)
        for col in ("payload", "entities", "evidence", "links", "score", "run_id", "agent_id", "durability"):
            assert got[col] == wrote[col], col
        assert got["conflicts_with"] == other and isinstance(got["conflicts_with"], uuid.UUID)
        assert got["valid_until"] == until and got["valid_until"].tzinfo is not None
        assert got["observed_at"] == NOW and got["verified"] is True and got["valid_from"] is None
        assert isinstance(got["embedding"], list) and len(got["embedding"]) == DIMS
        assert all(abs(a - b) < 1e-6 for a, b in zip(got["embedding"], embedding))
    for got in (run, watermark):
        assert (got["run_id"], got["last_message_at"], got["final_pass"]) == (other, until, True)
        assert (got["signals"], got["dropped"]) == ([{"kind": "thumb"}], [{"reason": "x"}])


def test_link_lookups_use_json_each(tmp_path):
    conn = sqlite3.connect(tmp_path / "m.db")
    backend = SqliteBackend(conn, "t")
    backend.init(embedding_model=MODEL, dims=DIMS)
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    with backend.unit_of_work() as uow:
        both = add(uow, "in A and B", links=[area_link(a), area_link(b)])
        add(uow, "only in B", links=[area_link(b)])
        add(uow, "in none")
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    with backend.unit_of_work() as uow:
        assert contents(uow.memories.linked_rows(a)) == ["in A and B"]
        assert sorted(contents(uow.memories.linked_rows(b))) == ["in A and B", "only in B"]
        assert uow.memories.linked_rows(c) == []
        assert any("json_each(links)" in s for s in statements if s.startswith("SELECT"))
        statements.clear()
        assert uow.memories.move_area_links(a, d, created_by="admin") == 1
        assert any("json_each(links)" in s for s in statements if s.startswith("SELECT"))
        assert contents(uow.memories.linked_rows(d)) == ["in A and B"] and uow.memories.linked_rows(a) == []
        moved = uow.memories.get_active(both["memory_id"])
        uow.memories.add_link(moved["id"], area_link(d))
        uow.memories.add_link(moved["id"], area_link(c))
        uow.memories.add_link(moved["id"], area_link(c))
        assert uow.memories.get_by_id(moved["id"])["links"] == [area_link(b), area_link(d), area_link(c)]
    conn.close()


def test_capabilities(tmp_path):
    backend = _backend(tmp_path)
    assert backend.name == "sqlite"
    assert backend.capabilities == frozenset({Capability.TRANSACTIONS, Capability.VECTOR_SEARCH, Capability.JSON_LOOKUP})


# --- S2: both vector paths --------------------------------------------------------------------------------------


def _by_path(tmp_path, monkeypatch) -> dict[str, SqliteBackend]:
    out = {}
    for path, flag in (("sqlite-vec", "1"), ("python", "0")):
        monkeypatch.setenv(VEC_ENV, flag)
        out[path] = _backend(tmp_path, f"{path}.db")
        assert out[path].vector_path == path
    return out


def test_both_vector_paths_agree(tmp_path, monkeypatch):
    results = {}
    for path, backend in _by_path(tmp_path, monkeypatch).items():
        with backend.unit_of_work() as uow:
            for sim in (0.50, 0.95, 0.86):
                add(uow, f"at {sim}", vec(sim))
            add(uow, "tie one", vec(0.70))
            add(uow, "tie two", vec(0.70))
            add(uow, "stale", vec(0.99), valid_until=NOW - timedelta(days=1))
            add(uow, "bob's", vec(0.99), user_id="bob")
            add(uow, "no vector", status="candidate")
            add(uow, "far area", vec(0.30), type="area", payload={"content": "far", "title": "Far"})
            add(uow, "near area", vec(0.80), type="area", payload={"content": "near", "title": "Near"})
        with backend.unit_of_work() as uow:
            uow.vectors.update_embedding  # noqa: B018 - the same repository on both paths
            common = dict(workspace_id="ws", user_id="alice", query_embedding=vec(1.0))
            results[path] = {
                "search": uow.vectors.search(k=3, now=NOW, **common),
                "wide": uow.vectors.search(k=10, now=NOW, **common),
                "with stale": uow.vectors.search(k=10, now=NOW, include_stale=True, **common),
                "top": uow.vectors.top_similar(type="fact", scope="user", limit=10, **common),
                "area": [uow.vectors.nearest_area(scope="user", **common)],
            }
    vec_path, python = results["sqlite-vec"], results["python"]
    assert contents(python["search"]) == ["at 0.95", "at 0.86", "tie one"]
    assert contents(python["wide"]) == ["at 0.95", "at 0.86", "tie one", "tie two", "at 0.5"]  # never bob's, never stale
    assert contents(python["with stale"])[0] == "stale" and "bob's" not in contents(python["with stale"])
    assert contents(python["area"]) == ["near area"]
    for name in python:
        assert contents(vec_path[name]) == contents(python[name]), name
        for a, b in zip(vec_path[name], python[name]):
            assert abs(a["similarity"] - b["similarity"]) < 1e-6, (name, a["content"])
    for row, sim in zip(vec_path["search"], (0.95, 0.86, 0.70)):
        assert abs(row["similarity"] - sim) < 1e-6
    for row, sim in zip(python["search"], (0.95, 0.86, 0.70)):
        assert abs(row["similarity"] - sim) < 1e-6


def _first_hit(backend: SqliteBackend) -> str:
    backend.init(embedding_model=MODEL, dims=DIMS)
    with backend.unit_of_work() as uow:
        add(uow, "at 0.5", vec(0.50))
        add(uow, "at 0.95", vec(0.95))
    with backend.unit_of_work() as uow:
        return uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice")[0]["content"]


def test_vector_path_selection(tmp_path, monkeypatch):
    import sqlite_vec

    monkeypatch.delenv(VEC_ENV, raising=False)
    assert SqliteBackend(f"sqlite:///{tmp_path / 'a.db'}", "t").vector_path == "sqlite-vec"

    monkeypatch.setenv(VEC_ENV, "0")
    forced = SqliteBackend(f"sqlite:///{tmp_path / 'b.db'}", "t")
    assert forced.vector_path == "python" and _first_hit(forced) == "at 0.95"

    monkeypatch.delenv(VEC_ENV)
    with monkeypatch.context() as patched:  # the package is not installed
        patched.setitem(sys.modules, "sqlite_vec", None)
        missing = SqliteBackend(f"sqlite:///{tmp_path / 'c.db'}", "t")
        assert missing.vector_path == "python" and _first_hit(missing) == "at 0.95"

    def refuse(_conn):
        raise sqlite3.OperationalError("not authorized")

    with monkeypatch.context() as patched:  # this Python's sqlite3 cannot load extensions
        patched.setattr(sqlite_vec, "load", refuse)
        refused = SqliteBackend(f"sqlite:///{tmp_path / 'd.db'}", "t")
        assert refused.vector_path == "python" and _first_hit(refused) == "at 0.95"


def test_forced_vec_without_the_extension_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(VEC_ENV, "1")
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    with pytest.raises(NotImplementedError, match=r"^vector_search is not available on sqlite: install memhub\[sqlite\]"):
        SqliteBackend(f"sqlite:///{tmp_path / 'm.db'}", "t")
    with pytest.raises(NotImplementedError, match=r"^vector_search is not available on sqlite: install memhub\[sqlite\]"):
        _memory(f"sqlite:///{tmp_path / 'm.db'}")


def test_a_vector_of_the_wrong_size_is_refused(tmp_path):
    backend = _backend(tmp_path)
    with pytest.raises(StoreError, match=r"16 dimensions, not 15"):
        with backend.unit_of_work() as uow:
            add(uow, "short", [0.1] * 15)
    with backend.unit_of_work() as uow:
        row = add(uow, "right size")
        with pytest.raises(StoreError, match=r"16 dimensions, not 17"):
            uow.vectors.update_embedding(row["id"], [0.1] * 17)
        with pytest.raises(StoreError, match=r"16 dimensions, not 15"):
            uow.memories.amend_row(row["id"], content="x", payload={"content": "x"}, embedding=[0.1] * 15)
        uow.vectors.set_embedding_dims(15)
        uow.vectors.update_embedding(row["id"], [0.1] * 15)
        assert len(uow.memories.get_by_id(row["id"])["embedding"]) == 15


def test_contract_suite_runs_on_both_vector_paths_with_no_skip():
    tests = [n for n in contract.__all__ if n.startswith("test_")]
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_contract_sqlite.py", "-q", "-rs", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stdout[-2000:]
    summary = out.stdout.strip().splitlines()[-1]
    assert f"{2 * len(tests)} passed" in summary and "skipped" not in out.stdout, summary
    listed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_contract_sqlite.py", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout
    for name in tests:
        assert f"::{name}[sqlite-vec]" in listed and f"::{name}[python]" in listed, name


# --- S3: what SQLite cannot do ----------------------------------------------------------------------------------


def test_thread_lock_names_the_capability_and_the_fix(tmp_path):
    backend = _backend(tmp_path)
    with pytest.raises(NotImplementedError) as exc:
        with backend.thread_lock("jsonl", "t1"):
            pass
    assert str(exc.value) == "parallel_ingest is not available on sqlite: run one ingest worker or use Postgres"


def test_database_schema_is_refused_at_startup(tmp_path):
    for db in (f"sqlite:///{tmp_path / 'm.db'}", sqlite3.connect(":memory:")):
        with pytest.raises(NotImplementedError) as exc:
            _memory(db, config={"database_schema": "tenant"})
        assert str(exc.value) == "schemas is not available on sqlite: remove database_schema from the config, or use Postgres"
    assert not (tmp_path / "m.db").exists()


class _SpyLocks:
    """A backend whose `thread_lock` is counted; `taken` names the threads another worker holds."""

    def __init__(self, inner, taken=()) -> None:
        self.inner, self.taken, self.asked = inner, set(taken), []
        self.name, self.capabilities = inner.name, inner.capabilities

    def unit_of_work(self):
        return self.inner.unit_of_work()

    @contextmanager
    def thread_lock(self, source, thread_id):
        self.asked.append(thread_id)
        if self.taken:
            yield thread_id not in self.taken
            return
        with self.inner.thread_lock(source, thread_id) as got:
            yield got


def test_ingest_locks_only_with_parallel_ingest(tmp_path, settings, registry):
    lines = [_line("c1", "m1", "I live in Lyon"), _line("c2", "m2", "My dog is called Rex")]
    answers = [[_cand("User lives in Lyon", "I live in Lyon", "m1")], [_cand("User has a dog called Rex", "My dog is called Rex", "m2")]]

    without = _SpyLocks(_backend(tmp_path))
    summary = _ingest(without, settings, registry, tmp_path, lines, answers)
    assert without.asked == []
    assert (summary.threads_processed, summary.created, summary.threads_locked, summary.failed_segments) == (2, 2, 0, 0)

    memory = InMemoryBackend()
    memory.init(embedding_model=MODEL, dims=DIMS)
    with_it = _SpyLocks(memory)
    summary = _ingest(with_it, settings, registry, tmp_path, lines, answers)
    assert with_it.asked == ["c1", "c2"]
    assert (summary.threads_processed, summary.created, summary.threads_locked) == (2, 2, 0)

    other = InMemoryBackend()
    other.init(embedding_model=MODEL, dims=DIMS)
    busy = _SpyLocks(other, taken={"c1"})
    summary = _ingest(busy, settings, registry, tmp_path, lines, answers[1:])
    assert busy.asked == ["c1", "c2"]
    assert (summary.threads_processed, summary.created, summary.threads_locked) == (1, 1, 1)


def test_memory_on_sqlite_needs_init_and_the_same_dims(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    fresh = _memory(url)
    with pytest.raises(EmbeddingMismatch, match=r"init\(\)"):
        fresh.add("too early", **IDS)
    with pytest.raises(EmbeddingMismatch, match=r"init\(\)"):
        fresh.search("too early", **IDS)
    fresh.init()
    fresh.add("now it works", **IDS)
    other = _memory(url, dims=8)
    with pytest.raises(EmbeddingMismatch, match="16"):
        other.get_all(**IDS)
    with pytest.raises(EmbeddingMismatch):
        other.init()


# --- S4: the same ingest as in memory ---------------------------------------------------------------------------


def test_ingest_matches_the_in_memory_backend(tmp_path, settings, registry, monkeypatch):
    lines = [
        _line("c1", "m1", "I live in Lyon"), _line("c1", "m2", "I work as a welder"),
        _line("c2", "m3", "My dog is called Rex"), _line("c3", "m4", "Nothing to keep here"),
    ]
    answers = [
        [_cand("User lives in Lyon", "I live in Lyon", "m1"), _cand("User is a welder", "I work as a welder", "m2")],
        [_cand("User has a cat", "I have a cat", "m3")],  # the quote is not in the message: dropped
        [],
    ]
    memory = InMemoryBackend()
    memory.init(embedding_model=MODEL, dims=DIMS)
    ledgers = {}
    for name, backend in {"memory": memory, **_by_path(tmp_path, monkeypatch)}.items():
        summary = _ingest(backend, settings, registry, tmp_path, lines, answers)
        ledgers[name] = (_ledger(backend), summary.created, summary.candidates_proposed, summary.dropped_by_reason)
    assert [m[:4] for m in ledgers["memory"][0][0]] == [("fact", "user", "active", "User is a welder"), ("fact", "user", "active", "User lives in Lyon")]
    assert ledgers["memory"][0][1] == [("c1", 2, 2, []), ("c2", 1, 0, ["ungrounded"]), ("c3", 0, 0, [])]
    assert ledgers["sqlite-vec"] == ledgers["memory"]
    assert ledgers["python"] == ledgers["memory"]


def test_doctor_reports_the_vector_path(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from memhub import cli

    monkeypatch.chdir(tmp_path)
    import langchain.embeddings

    monkeypatch.setattr(langchain.embeddings, "init_embeddings", lambda spec, **kw: FakeEmbeddings(16))
    runner = CliRunner()
    init = runner.invoke(cli.app, ["init", "--db", "sqlite:///m.db", "--embeddings", "openai:text-embedding-3-small",
                                   "--preset", "assistant", "--project", "doc"])
    assert init.exit_code == 0, init.output
    for flag, path in (("1", "sqlite-vec"), ("0", "python")):
        monkeypatch.setenv(VEC_ENV, flag)
        out = runner.invoke(cli.app, ["doctor"])
        lines = [line.split("  ") for line in out.output.splitlines()]
        assert ["ok", "db", "sqlite answers SELECT 1"] in lines, out.output
        vector = [line for line in lines if line[1:2] == ["vector"]]
        assert len(vector) == 1 and vector[0][0] == "ok" and vector[0][2].startswith(path), out.output
        assert any(line[:2] == ["ok", "schema"] for line in lines), out.output


def test_stale_filter_compares_times_not_offsets(tmp_path, monkeypatch):
    """`valid_until` written in +02:00 and `now` in -05:00, an hour apart: text order must be time order."""
    plus2, minus5 = timezone(timedelta(hours=2)), timezone(timedelta(hours=-5))
    now = datetime(2026, 3, 1, 12, 0, tzinfo=minus5)  # 17:00 UTC
    for path, backend in _by_path(tmp_path, monkeypatch).items():
        with backend.unit_of_work() as uow:
            add(uow, "expired an hour ago", vec(0.9), valid_until=(now - timedelta(hours=1)).astimezone(plus2))
            add(uow, "expires in an hour", vec(0.8), valid_until=(now + timedelta(hours=1)).astimezone(plus2))
        with backend.unit_of_work() as uow:
            found = contents(uow.vectors.search(query_embedding=vec(1.0), workspace_id="ws", user_id="alice", now=now))
            stale = {r["content"] for r in uow.memories.list_memories(stale=True, now=now)}
        assert found == ["expires in an hour"], path
        assert stale == {"expired an hour ago"}, path
