"""Storage behind ports: a `Backend` opens `UnitOfWork`s; Postgres, SQLite and in-memory implement them.

    backend = open_backend(pool, prefix="memhub")      # Postgres: a pool, connection, callable or URL
    backend = open_backend("sqlite:///memhub.db")      # SQLite: a URL, a sqlite3.Connection or a callable
    backend = open_backend("memory://")                # in-memory, new and empty
    backend = open_backend("memory://tests")           # in-memory, shared by name within the process
"""
from __future__ import annotations

from typing import Any

from memhub.storage.base import Backend, Capability, UnitOfWork, require

MEMORY_SCHEME = "memory://"

__all__ = ["Backend", "Capability", "MEMORY_SCHEME", "UnitOfWork", "open_backend", "require"]


def open_backend(db: Any, prefix: str = "memhub", schema: str | None = None) -> Backend:
    """The backend for what the app passed as `db`: an in-memory URL, SQLite (a `sqlite3.Connection`, a
    `sqlite:` URL, or a callable that returns a `sqlite3.Connection`), else Postgres (whose connection source
    rejects anything it cannot use). A callable is called once here to see what it returns; that connection
    is closed and no SQL runs on it."""
    if isinstance(db, str) and db.startswith(MEMORY_SCHEME):
        from memhub.storage.memory import InMemoryBackend

        return InMemoryBackend.for_url(db, schema=schema)
    from memhub.connection import ConnectionSource, _is_pool, is_sqlite

    sqlite = is_sqlite(db)
    if not sqlite and callable(db) and not isinstance(db, ConnectionSource) and not _is_pool(db):
        import sqlite3

        probe = db()
        sqlite = isinstance(probe, sqlite3.Connection)
        if hasattr(probe, "close"):
            probe.close()
    if sqlite:
        from memhub.storage.sqlite import SqliteBackend

        return SqliteBackend(db, prefix, schema)
    from memhub.storage.postgres import MemoryStore

    return MemoryStore(db, prefix, schema)
