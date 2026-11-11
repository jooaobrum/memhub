"""Schema versions and `memhub migrate`: a ledger knows its version, an older one is refused until it is migrated,
a migration applies in one transaction, once. `load_v0_1_0` builds the ledger memhub 0.1.0 left behind."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml
from typer.testing import CliRunner

from memhub import EmbeddingMismatch, Memory, SchemaOutdated, cli
from memhub.storage import migrations as migrations_mod
from memhub.storage.memory import InMemoryBackend
from memhub.storage.migrations import SCHEMA_VERSION, migrations, pending
from memhub.storage.postgres import MemoryStore
from memhub.storage.sqlite import SqliteBackend
from tests.fakes import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "data" / "v0_1_0_ledger.sql"
IDS = dict(workspace_id="acme", user_id="anna", run_id="run-1")
OLD = "the ledger is at schema version 1 and this memhub needs 2: run `memhub migrate`"
NEWER = "the ledger is at schema version 3, newer than this memhub (2): upgrade memhub"
runner = CliRunner()


def _prefix() -> str:
    return f"m{uuid.uuid4().hex[:8]}"


def load_v0_1_0(dsn: str, prefix: str, model: str = "FakeEmbeddings") -> None:
    embedding = "[" + ",".join(str(x) for x in FakeEmbeddings(16).embed_query("compressor C3 inlet filter")) + "]"
    lines = [l for l in FIXTURE.read_text().splitlines() if not l.startswith("--")]
    sql = "\n".join(lines).replace("{prefix}", prefix).replace("{model}", model).replace("{embedding}", embedding)
    with psycopg.connect(dsn, autocommit=True) as conn:
        for statement in (s.strip() for s in sql.split(";")):
            if statement:
                conn.execute(statement)


def _columns(dsn: str, prefix: str) -> dict[str, str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = %s",
                            (f"{prefix}_memory",)).fetchall()
    return dict(rows)


def _backends(pg_dsn, tmp_path) -> dict[str, object]:
    return {"postgres": MemoryStore(pg_dsn, _prefix()), "sqlite": SqliteBackend(f"sqlite:///{tmp_path / 'm.db'}", "t"),
            "memory": InMemoryBackend()}


def _set_version(backend, version: int | None) -> None:
    """Record `version` (None: remove the row, as a ledger made before versions has none)."""
    if isinstance(backend, InMemoryBackend):
        if version is None:
            backend._tables.meta.pop("schema_version", None)
        else:
            backend._tables.meta["schema_version"] = version
        return
    if version is None:
        _delete_version(backend)
        return
    with backend.unit_of_work() as uow:
        uow.meta.set_meta("schema_version", version)


def _delete_version(backend) -> None:
    if isinstance(backend, SqliteBackend):
        with backend.source.connection() as conn:
            conn.execute(f"DELETE FROM {backend.prefix}_memhub_meta WHERE key = 'schema_version'")
            conn.commit() if conn.in_transaction else None
    else:
        with backend.connect() as conn:
            conn.execute(f"DELETE FROM {backend.prefix}_memhub_meta WHERE key = 'schema_version'")


# --- S1: a ledger knows its version -----------------------------------------------------------------------------


def test_init_records_the_version(pg_dsn, tmp_path):
    for name, backend in _backends(pg_dsn, tmp_path).items():
        assert backend.schema_version() is None, name
        backend.init(embedding_model="m", dims=16)
        assert backend.schema_version() == 2, name
        with backend.unit_of_work() as uow:
            assert uow.meta.get_meta("schema_version") == 2, name


def test_no_version_row_is_version_one(pg_dsn, tmp_path):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix)
    assert MemoryStore(pg_dsn, prefix).schema_version() == 1
    lite = SqliteBackend(f"sqlite:///{tmp_path / 'm.db'}", "t")
    lite.init(embedding_model="m", dims=16)
    _delete_version(lite)
    assert lite.schema_version() == 1


def test_pending_and_the_files():
    for backend in ("postgres", "sqlite", "memory"):
        assert [m.name for m in pending(backend, 1)] == ["0002_run_and_agent_ids"], backend
        assert pending(backend, 2) == [], backend
        with pytest.raises(ValueError, match="init"):
            pending(backend, None)
    for backend in ("postgres", "sqlite"):
        files = sorted(p.name for p in (migrations_mod.HERE / backend).iterdir() if p.suffix == ".sql")
        assert [int(f[:4]) for f in files] == list(range(2, SCHEMA_VERSION + 1)), backend
        assert all(migrations_mod._FILE.match(f) for f in files), backend
    assert [m.name for m in migrations("sqlite")] == [m.name for m in migrations("postgres")]


# --- S2: refused, then upgraded ---------------------------------------------------------------------------------


def test_memory_refuses_an_old_ledger(pg_dsn, tmp_path):
    for name, backend in _backends(pg_dsn, tmp_path).items():
        backend.init(embedding_model="FakeEmbeddings", dims=16)
        _set_version(backend, None)
        db = "memory" + "://" if name == "memory" else (pg_dsn if name == "postgres" else f"sqlite:///{tmp_path / 'm.db'}")
        prefix = getattr(backend, "prefix", "memhub")
        for method, args in (("add", ("x",)), ("search", ("x",)), ("get_all", ()), ("queue", ())):
            m = Memory(db=db, embedder=FakeEmbeddings(16), config={"project_prefix": prefix})
            if name == "memory":
                m.store = m.service.store = backend
            with pytest.raises(SchemaOutdated) as exc:
                getattr(m, method)(*args, **IDS)
            assert str(exc.value) == OLD, (name, method)
        assert backend.schema_version() == 1, name
        with backend.unit_of_work() as uow:
            assert uow.memories.list_memories() == [], name


def test_a_newer_ledger_is_refused(pg_dsn, tmp_path):
    for name, backend in _backends(pg_dsn, tmp_path).items():
        backend.init(embedding_model="FakeEmbeddings", dims=16)
        _set_version(backend, 3)
        m = Memory(db="memory" + "://", embedder=FakeEmbeddings(16))
        m.store = m.service.store = backend
        with pytest.raises(SchemaOutdated) as exc:
            m.search("x", **IDS)
        assert str(exc.value) == NEWER, name
        with pytest.raises(SchemaOutdated) as exc:
            backend.migrate()
        assert str(exc.value) == NEWER and backend.schema_version() == 3, name


def test_migrate_a_v0_1_0_postgres_ledger(pg_dsn):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix)
    assert "run_id" not in _columns(pg_dsn, prefix)
    store = MemoryStore(pg_dsn, prefix)
    assert store.migrate() == ["0002_run_and_agent_ids"]
    cols = _columns(pg_dsn, prefix)
    assert (cols["run_id"], cols["agent_id"]) == ("YES", "YES")
    with store.unit_of_work() as uow:
        [row] = uow.memories.list_memories()
    assert row["content"] == "Compressor C3 trips when the inlet filter is clogged"
    assert (row["run_id"], row["agent_id"], row["verified"], str(row["memory_id"])) == (
        None, None, True, "6f1d0c9e-0000-4000-8000-000000000002")
    assert store.schema_version() == 2


def test_migrate_is_idempotent(pg_dsn, tmp_path):
    for name, backend in _backends(pg_dsn, tmp_path).items():
        backend.init(embedding_model="m", dims=16)
        assert backend.migrate() == [] and backend.migrate() == [], name
        _set_version(backend, None)
        assert backend.migrate() == ["0002_run_and_agent_ids"] and backend.migrate() == [], name


def test_a_failed_migration_applies_nothing(pg_dsn, tmp_path, monkeypatch):
    bad = tmp_path / "migrations"
    for backend in ("postgres", "sqlite"):
        (bad / backend).mkdir(parents=True)
        (bad / backend / "0002_breaks.sql").write_text(
            "CREATE TABLE {prefix}_probe (x int);\nALTER TABLE {prefix}_no_such_table ADD COLUMN y int")
    monkeypatch.setattr(migrations_mod, "HERE", bad)
    pg, lite = MemoryStore(pg_dsn, _prefix()), SqliteBackend(f"sqlite:///{tmp_path / 'm.db'}", "t")
    for backend in (pg, lite):
        monkeypatch.setattr(migrations_mod, "HERE", migrations_mod.Path(migrations_mod.__file__).parent)
        backend.init(embedding_model="m", dims=16)
        _set_version(backend, None)
        monkeypatch.setattr(migrations_mod, "HERE", bad)
        with pytest.raises((psycopg.Error, sqlite3.Error)):
            backend.migrate()
        assert backend.schema_version() == 1
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (f"{pg.prefix}_probe",)).fetchone()[0] is None
    with lite.source.connection() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 't_probe'").fetchone() is None


def test_two_migrations_at_once_apply_once(pg_dsn):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix)
    results, errors, start = [], [], threading.Barrier(2)

    def run():
        try:
            start.wait()
            results.append(MemoryStore(pg_dsn, prefix).migrate())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert sorted(results) == [[], ["0002_run_and_agent_ids"]]


def test_init_upgrades_an_old_ledger(pg_dsn):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix)
    MemoryStore(pg_dsn, prefix).init(embedding_model="FakeEmbeddings", dims=16)
    assert MemoryStore(pg_dsn, prefix).schema_version() == 2
    assert {"run_id", "agent_id"} <= set(_columns(pg_dsn, prefix))


def test_migrate_without_a_ledger(pg_dsn, tmp_path):
    for name, backend in _backends(pg_dsn, tmp_path).items():
        with pytest.raises(EmbeddingMismatch, match=r"init\(\)"):
            backend.migrate()
        assert backend.schema_version() is None, name


# --- S3: the CLI ------------------------------------------------------------------------------------------------


def _yaml(pg_dsn, prefix, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "build_embeddings", lambda cfg: FakeEmbeddings(cfg.dims))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memhub.yaml").write_text(yaml.safe_dump({
        "project_prefix": prefix, "database_url": pg_dsn, "workspace_default": "acme",
        "embeddings": {"provider": "openai", "model": "fake-embed", "api_key_env": "UNSET", "dims": 16},
        "scopes": ["user", "workspace"], "types": {"fact": {"class": "memhub.types:Fact", "type_prior": 0.6}},
    }))


def test_cli_migrate(pg_dsn, tmp_path, monkeypatch):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix, model="fake-embed")
    _yaml(pg_dsn, prefix, tmp_path, monkeypatch)
    first = runner.invoke(cli.app, ["migrate"])
    assert first.exit_code == 0, first.output
    assert first.output.splitlines() == ["applied 0002_run_and_agent_ids", "schema version 1 -> 2"]
    again = runner.invoke(cli.app, ["migrate"])
    assert (again.exit_code, again.output.strip()) == (0, "already at schema version 2")


def test_cli_migrate_dry_run(pg_dsn, tmp_path, monkeypatch):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix, model="fake-embed")
    _yaml(pg_dsn, prefix, tmp_path, monkeypatch)
    dry = runner.invoke(cli.app, ["migrate", "--dry-run"])
    assert (dry.exit_code, dry.output.strip()) == (0, "pending 0002_run_and_agent_ids")
    assert MemoryStore(pg_dsn, prefix).schema_version() == 1 and "run_id" not in _columns(pg_dsn, prefix)


def test_cli_migrate_failures(pg_dsn, tmp_path, monkeypatch):
    prefix = _prefix()
    _yaml(pg_dsn, prefix, tmp_path, monkeypatch)
    none = runner.invoke(cli.app, ["migrate"])
    assert none.exit_code == 1 and "init" in none.output
    store = MemoryStore(pg_dsn, prefix)
    store.init(embedding_model="fake-embed", dims=16)
    _set_version(store, 3)
    newer = runner.invoke(cli.app, ["migrate"])
    assert newer.exit_code == 1 and NEWER in newer.output


def test_cli_commands_refuse_an_old_ledger(pg_dsn, tmp_path, monkeypatch):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix, model="fake-embed")
    _yaml(pg_dsn, prefix, tmp_path, monkeypatch)
    (tmp_path / "f.json").write_text(json.dumps({"content": "x"}))
    for cmd in (["search", "inlet filter"], ["add", "--type", "fact", "--scope", "user", "--user", "anna", "-f", "f.json"],
                ["queue"], ["list"]):
        result = runner.invoke(cli.app, cmd)
        assert result.exit_code == 1 and OLD in result.output, (cmd, result.output)
    assert "run_id" not in _columns(pg_dsn, prefix)


def test_doctor_reports_the_version(pg_dsn, tmp_path, monkeypatch):
    prefix = _prefix()
    load_v0_1_0(pg_dsn, prefix, model="fake-embed")
    _yaml(pg_dsn, prefix, tmp_path, monkeypatch)
    old = runner.invoke(cli.app, ["doctor"])
    [line] = [l for l in old.output.splitlines() if l.split("  ")[1:2] == ["version"]]
    assert old.exit_code == 1 and line.startswith("fail  version") and "memhub migrate" in line
    assert runner.invoke(cli.app, ["migrate"]).exit_code == 0
    now = runner.invoke(cli.app, ["doctor"])
    assert "ok  version  schema version 2" in now.output.splitlines()


def test_migrate_docs():
    for doc in ("README.md", "docs/integration.md"):
        text = (ROOT / doc).read_text()
        assert "memhub migrate" in text and "SchemaOutdated" in text, doc
