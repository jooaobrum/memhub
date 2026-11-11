"""The storage ports themselves: the protocol, the repository signatures, the capability errors, and the
promises about what moved where (PR 3 of #1)."""
from __future__ import annotations

import ast
import importlib.util
import inspect
import re
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

import memhub.store
from memhub import Memory
from memhub.storage import Backend, Capability, open_backend
from memhub.storage import postgres, rules
from memhub.storage.base import METHODS
from memhub.storage.memory import InMemoryBackend
from memhub.storage.postgres import MemoryStore
from tests.fakes import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "memhub"
BEFORE_PORTS = "2a58e6c"
PORTS_DONE = "9fdbf05"  # the last commit of PR 3: later PRs change old tests with their own claims (AD-021)
SCHEMAS_MESSAGE = "schemas is not available on memory: remove database_schema from the config, or use Postgres"


def test_capability_values_and_backends_satisfy_protocol():
    assert sorted(c.value for c in Capability) == sorted(
        ["transactions", "vector_search", "json_lookup", "parallel_ingest", "schemas"])
    assert isinstance(MemoryStore("postgresql://nobody@nowhere/x", "p"), Backend)
    assert isinstance(InMemoryBackend(), Backend)


def _params(fn) -> list[tuple]:
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]


def test_repository_methods_match_memorystore_signatures():
    assert sum(len(v) for v in METHODS.values()) == 37
    memory = InMemoryBackend()
    memory.init(embedding_model="m", dims=4)
    pg_uow = MemoryStore("postgresql://nobody@nowhere/x", "p").bind(object())
    with memory.unit_of_work() as mem_uow:
        for kind, names in METHODS.items():
            for name in names:
                expected = _params(getattr(MemoryStore, name))[2:]  # without self, cur
                assert expected, name
                for uow in (pg_uow, mem_uow):
                    assert _params(getattr(getattr(uow, kind), name)) == expected, f"{kind}.{name}"
        with pytest.raises(AttributeError):
            mem_uow.memories.search  # a vector method is not on the ledger repository


def test_postgres_capabilities_for_every_db_kind(pg_dsn):
    from psycopg_pool import ConnectionPool

    with ConnectionPool(pg_dsn, min_size=1, max_size=1, open=True) as pool, psycopg.connect(pg_dsn) as conn:
        for db in (pool, conn, lambda: psycopg.connect(pg_dsn), pg_dsn):
            backend = open_backend(db, "p")
            assert isinstance(backend, MemoryStore)
            assert backend.capabilities == frozenset(Capability)


def test_bind_shares_the_cursor_transaction(store):
    with store.connect() as conn, conn.cursor() as cur:
        uow = store.bind(cur)
        row = uow.memories.add_memory(
            type="fact", schema_version=1, scope="user", workspace_id="ws", user_id="alice", content="bound",
            payload={"content": "bound"}, entities=[], embedding=[1.0] + [0.0] * 15, evidence=[], status="active",
            verified=False, created_by="t",
        )
        cur.execute(f"SELECT content FROM {store._t('memory')} WHERE id = %s", (row["id"],))
        assert cur.fetchone()["content"] == "bound"  # same transaction, before any commit
        conn.rollback()  # bind committed nothing: the row goes with the caller's rollback
    with store.unit_of_work() as uow:
        assert uow.memories.get_by_id(row["id"]) is None


def _callers() -> list[Path]:
    return [SRC / n for n in ("service.py", "memory.py", "cli.py", "middleware.py", "toolkit.py")] + sorted(
        (SRC / "pipeline").glob("*.py"))


def test_no_psycopg_outside_the_postgres_backend():
    found = []
    for path in _callers():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "psycopg" for a in node.names):
                found.append(f"{path.name}:{node.lineno} import psycopg")
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "psycopg":
                found.append(f"{path.name}:{node.lineno} from psycopg")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                    if a.arg == "cur":
                        found.append(f"{path.name}:{node.lineno} parameter cur")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("cursor", "connect"):
                found.append(f"{path.name}:{node.lineno} .{node.func.attr}(")
    assert len(_callers()) >= 15 and found == []


def _public_names_before_ports() -> list[str]:
    source = subprocess.run(["git", "show", f"{BEFORE_PORTS}:src/memhub/store.py"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout
    tree = ast.parse(source)
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    names += [t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
    names += [n.target.id for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    return [n for n in names if not n.startswith("_")]


def test_store_shim_exports_every_name_from_before_the_ports():
    names = _public_names_before_ports()
    assert {"MemoryStore", "Actor", "NotFound", "newest_observed_at", "fresh_evidence", "ADDED_COLUMNS"} <= set(names)
    for name in names:
        home = rules if hasattr(rules, name) else postgres
        assert getattr(memhub.store, name) is getattr(home, name), name


HELPERS = ("newest_observed_at", "fresh_evidence", "area_link", "area_ids", "as_vector_list", "Actor", "StoreError",
           "NotFound", "Conflict", "EmbeddingMismatch")


def test_pure_helpers_import_without_psycopg():
    script = f"""
import importlib.util, sys
sys.modules["psycopg"] = None  # any `import psycopg` now raises ImportError
spec = importlib.util.spec_from_file_location("rules_alone", {str(SRC / 'storage' / 'rules.py')!r})
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
missing = [n for n in {HELPERS!r} if not hasattr(mod, n)]
assert not missing, missing
assert "psycopg" not in {{k for k, v in sys.modules.items() if v is not None}}
"""
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    for name in HELPERS:
        assert getattr(memhub.store, name) is getattr(rules, name)


_CALL_SITE = re.compile(r"cur=cur, store=((?:\w+\.)?store)\b")


def test_old_test_files_change_only_at_pipeline_call_sites():
    old = set(subprocess.run(["git", "ls-tree", "--name-only", BEFORE_PORTS, "tests/"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split())
    diff = subprocess.run(["git", "diff", "-U0", BEFORE_PORTS, PORTS_DONE, "--", *sorted(p for p in old if p.endswith(".py"))],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout
    removed = [l[1:] for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    assert len(removed) == len(added) == 5
    for r, a in zip(removed, added):
        assert a == _CALL_SITE.sub(r"uow=\1.bind(cur)", r) != r, (r, a)


def test_missing_capability_message_is_exact():
    with pytest.raises(NotImplementedError) as exc:
        InMemoryBackend(schema="tenant")
    assert str(exc.value) == SCHEMAS_MESSAGE


def test_memory_init_checks_needed_capabilities():
    mem = "memory" + "://"
    with pytest.raises(NotImplementedError) as exc:
        Memory(db=mem, embedder=FakeEmbeddings(), config={"database_schema": "tenant"})
    assert str(exc.value) == SCHEMAS_MESSAGE
    with pytest.raises(NotImplementedError) as exc:
        Memory(db=mem + "named", embedder=FakeEmbeddings(), config={"database_schema": "tenant"})
    assert str(exc.value) == SCHEMAS_MESSAGE
    m = Memory(db=mem, embedder=FakeEmbeddings())
    assert isinstance(m.store, InMemoryBackend) and m.source is None


def test_memory_contract_skips_nothing():
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                          "tests/test_contract_memory.py"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-2000:]
    assert "skipped" not in out.stdout and "failed" not in out.stdout and re.search(r"\b\d+ passed\b", out.stdout), out.stdout[-500:]


class _Lacking(InMemoryBackend):
    """A backend with transactions only: every other capability must fail loudly, never skip."""

    name = "lacking"
    capabilities = frozenset({Capability.TRANSACTIONS})


def test_contract_capability_tests_assert_the_error_on_a_backend_that_lacks_them(monkeypatch):
    from memhub.storage import contract

    backend = _Lacking()
    backend.init(embedding_model=contract.MODEL, dims=contract.DIMS)
    contract.test_vector_search_capability(backend)
    contract.test_json_lookup_capability(backend)
    contract.test_parallel_ingest_capability(backend)
    contract.test_schemas_capability(lambda schema: _Lacking(schema=schema))
    monkeypatch.setattr("memhub.memory.open_backend", lambda *a, **kw: _Lacking())
    with pytest.raises(NotImplementedError, match="^vector_search is not available on lacking: "):
        Memory(db="memory" + "://", embedder=FakeEmbeddings())
