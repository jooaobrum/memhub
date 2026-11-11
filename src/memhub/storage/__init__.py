"""Storage behind ports: a `Backend` opens `UnitOfWork`s; Postgres, SQLite and in-memory implement them.

    backend = open_backend(pool, prefix="memhub")      # Postgres: a pool, connection, callable or URL
    backend = open_backend("sqlite:///memhub.db")      # SQLite: a URL, a sqlite3.Connection or a callable
    backend = open_backend("memory://")                # in-memory, new and empty
    backend = open_backend("memory://tests")           # in-memory, shared by name within the process
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Callable, ContextManager

from memhub.storage.base import Backend, Capability, UnitOfWork, require

MEMORY_SCHEME = "memory://"

__all__ = ["Backend", "Capability", "DeferredBackend", "MEMORY_SCHEME", "UnitOfWork", "open_backend", "require"]


def open_backend(db: Any, prefix: str = "memhub", schema: str | None = None) -> Backend:
    """The backend for what the app passed as `db`: an in-memory URL, SQLite (a `sqlite3.Connection` or a
    `sqlite:` URL), a callable (a `DeferredBackend`: SQLite or Postgres, told by what it returns on first use),
    else Postgres (whose connection source rejects anything it cannot use)."""
    if isinstance(db, str) and db.startswith(MEMORY_SCHEME):
        from memhub.storage.memory import InMemoryBackend

        return InMemoryBackend.for_url(db, prefix=prefix, schema=schema)
    from memhub.connection import ConnectionSource, _is_pool, is_sqlite

    if is_sqlite(db):
        from memhub.storage.sqlite import SqliteBackend

        return SqliteBackend(db, prefix, schema)
    if callable(db) and not isinstance(db, ConnectionSource) and not _is_pool(db):
        return DeferredBackend(db, prefix, schema)
    from memhub.storage.postgres import MemoryStore

    return MemoryStore(db, prefix, schema)


class DeferredBackend:
    """The backend for a callable `db`. Which database it is shows only in what the callable returns, and
    building `Memory` must open nothing (AD-002): the first use calls it once, closes that connection, and
    builds a `SqliteBackend` (a `sqlite3.Connection` came back) or a Postgres `MemoryStore` (anything else) on
    the same callable. Every attribute is the built backend's."""

    deferred = True

    def __init__(self, factory: Callable[[], Any], prefix: str, schema: str | None) -> None:
        self._factory, self._prefix, self._schema = factory, prefix, schema
        self._backend: Backend | None = None
        self._lock = threading.Lock()

    def resolve(self) -> Backend:
        with self._lock:
            if self._backend is None:
                probe = self._factory()
                try:
                    sqlite = isinstance(probe, sqlite3.Connection)
                finally:
                    if hasattr(probe, "close"):
                        probe.close()
                if sqlite:
                    from memhub.storage.sqlite import SqliteBackend

                    self._backend = SqliteBackend(self._factory, self._prefix, self._schema)
                else:
                    from memhub.storage.postgres import MemoryStore

                    self._backend = MemoryStore(self._factory, self._prefix, self._schema)
            return self._backend

    @property
    def name(self) -> str:
        return self.resolve().name

    @property
    def capabilities(self) -> frozenset[Capability]:
        return self.resolve().capabilities

    def unit_of_work(self) -> ContextManager[UnitOfWork]:
        return self.resolve().unit_of_work()

    def thread_lock(self, source: str, thread_id: str) -> ContextManager[bool]:
        return self.resolve().thread_lock(source, thread_id)

    def init(self, *, embedding_model: str, dims: int) -> None:
        self.resolve().init(embedding_model=embedding_model, dims=dims)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.resolve(), name)
