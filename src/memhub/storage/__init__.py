"""Storage behind ports: a `Backend` opens `UnitOfWork`s; Postgres and in-memory implement them.

    backend = open_backend(pool, prefix="memhub")      # Postgres: a pool, connection, callable or URL
    backend = open_backend("memory://")                # in-memory, new and empty
    backend = open_backend("memory://tests")           # in-memory, shared by name within the process
"""
from __future__ import annotations

from typing import Any

from memhub.storage.base import Backend, Capability, UnitOfWork, require

MEMORY_SCHEME = "memory://"

__all__ = ["Backend", "Capability", "MEMORY_SCHEME", "UnitOfWork", "open_backend", "require"]


def open_backend(db: Any, prefix: str = "memhub", schema: str | None = None) -> Backend:
    """The backend for what the app passed as `db`: an in-memory URL, else Postgres (whose connection source
    rejects anything it cannot use)."""
    if isinstance(db, str) and db.startswith(MEMORY_SCHEME):
        from memhub.storage.memory import InMemoryBackend

        return InMemoryBackend.for_url(db, prefix=prefix, schema=schema)
    from memhub.storage.postgres import MemoryStore

    return MemoryStore(db, prefix, schema)
