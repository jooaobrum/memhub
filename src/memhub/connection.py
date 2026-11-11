"""Where memhub gets its database connections from.

The app owns its connections: pooling, SSL and token refresh stay with it. memhub borrows a connection
for one transaction (or for the life of an advisory lock), never closes a connection or pool it did not
open, and commits or rolls back only its own transaction - a savepoint when the app already has one open.
`register_vector` and `SET search_path` run once per connection, not once per transaction.

    source = connection_source(pool)              # psycopg_pool.ConnectionPool
    source = connection_source(conn)              # psycopg.Connection
    source = connection_source(lambda: psycopg.connect(url))
    source = connection_source("postgresql://...")
    source = connection_source("sqlite:///memhub.db")   # or a sqlite3.Connection: a `SqliteSource`
"""
from __future__ import annotations

import sqlite3
import threading
import weakref
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import psycopg
from psycopg import pq
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row, tuple_row

ACCEPTED = (
    "a psycopg_pool.ConnectionPool, a psycopg.Connection, a callable returning a psycopg.Connection, "
    "or a postgresql:// URL"
)

SQLITE_FORMS = (
    "a sqlite3.Connection, a callable returning one, sqlite:///path (a relative file), sqlite:////path "
    "(an absolute file) or sqlite:///:memory:"
)
SQLITE_MEMORY = ":memory:"

# connection -> what has been set up on it: "vector", and "schema:<name>" for the search_path
_prepared: "weakref.WeakKeyDictionary[psycopg.Connection, set[str]]" = weakref.WeakKeyDictionary()


def _prepare(conn: psycopg.Connection, *, vector: bool, schema: str | None, remember_schema: bool = True) -> set[str]:
    """Run what is missing on this connection; returns what it ran, so a rolled-back transaction can forget it.
    `remember_schema=False` sets the search_path without caching it: a `SET` made inside the app's open
    transaction is undone if the app rolls back, so it is run again on the next call."""
    done = _prepared.setdefault(conn, set())
    ran: set[str] = set()
    if schema and f"schema:{schema}" not in done:  # `public` stays on the path: that is where the vector extension usually lives
        conn.execute(f"SET search_path TO {schema}, public")
        if remember_schema:
            ran.add(f"schema:{schema}")
    if vector and "vector" not in done:
        register_vector(conn)  # a client-side adapter: no transaction can undo it
        ran.add("vector")
    done |= ran
    return ran


@contextmanager
def _owned_connection(conn: psycopg.Connection, *, vector: bool, schema: str | None) -> Iterator[psycopg.Connection]:
    """A connection memhub opened: today's behaviour - commit on a clean exit, roll back on an exception. The
    caller may commit or roll back itself inside, as code written against `MemoryStore.connect` always could."""
    conn.row_factory = dict_row
    try:
        _prepare(conn, vector=vector, schema=schema)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def _own_transaction(conn: psycopg.Connection, *, vector: bool, schema: str | None) -> Iterator[psycopg.Connection]:
    """memhub's transaction on `conn`: a real one when the connection is idle, a savepoint when the app has one
    open. Rows come back as dicts for the duration only; the app's row factory is put back after."""
    original = conn.row_factory
    inside_app = conn.info.transaction_status == pq.TransactionStatus.INTRANS
    conn.row_factory = dict_row
    ran: set[str] = set()
    try:
        with conn.transaction():
            ran = _prepare(conn, vector=vector, schema=schema, remember_schema=not inside_app)
            yield conn
    except BaseException:
        # a `SET` inside a rolled-back transaction is undone: run it again next time
        _prepared.get(conn, set()).difference_update(r for r in ran if r.startswith("schema:"))
        raise
    finally:
        conn.row_factory = original


def advisory_lock(conn: psycopg.Connection, key: str) -> bool:
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
        return bool(cur.fetchone()[0])


def advisory_unlock(conn: psycopg.Connection, key: str) -> None:
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


class ConnectionSource:
    """Hands out a connection for one memhub transaction, and one for an advisory lock."""

    owns_connections: bool = False

    @contextmanager
    def transaction(self, *, vector: bool = True, schema: str | None = None) -> Iterator[psycopg.Connection]:
        raise NotImplementedError  # pragma: no cover

    @contextmanager
    def lock_connection(self) -> Iterator[psycopg.Connection]:
        raise NotImplementedError  # pragma: no cover


class UrlSource(ConnectionSource):
    """Today's behaviour: memhub opens a connection per transaction from a URL and closes it after."""

    owns_connections = True

    def __init__(self, url: str) -> None:
        self.url = url

    @contextmanager
    def transaction(self, *, vector: bool = True, schema: str | None = None) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self.url, row_factory=dict_row, autocommit=False)
        try:
            with _owned_connection(conn, vector=vector, schema=schema) as c:
                yield c
        finally:
            conn.close()

    @contextmanager
    def lock_connection(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self.url, autocommit=True)
        try:
            yield conn
        finally:
            conn.close()


class FactorySource(ConnectionSource):
    """A callable the app gives: memhub calls it per transaction and closes what it returned."""

    owns_connections = True

    def __init__(self, factory: Callable[[], Any]) -> None:
        self.factory = factory

    def _open(self) -> psycopg.Connection:
        conn = self.factory()
        if not isinstance(conn, psycopg.Connection):
            raise TypeError(f"the db callable returned {type(conn).__name__}; it must return {ACCEPTED.split(', a callable')[0]}")
        return conn

    @contextmanager
    def transaction(self, *, vector: bool = True, schema: str | None = None) -> Iterator[psycopg.Connection]:
        conn = self._open()
        if conn.autocommit:  # memhub owns this one: one transaction per call, as with a URL
            conn.autocommit = False
        try:
            with _owned_connection(conn, vector=vector, schema=schema) as c:
                yield c
        finally:
            conn.close()

    @contextmanager
    def lock_connection(self) -> Iterator[psycopg.Connection]:
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()


class PoolSource(ConnectionSource):
    """A pool the app owns: one pooled connection per transaction, handed back open; the pool is never closed.
    While a thread holds an advisory lock, its transactions run on the lock's connection: a second connection
    from a pool of size 1 would wait for the first forever."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self._held = threading.local()

    @contextmanager
    def transaction(self, *, vector: bool = True, schema: str | None = None) -> Iterator[psycopg.Connection]:
        held = getattr(self._held, "conn", None)
        if held is not None:
            with _own_transaction(held, vector=vector, schema=schema) as c:
                yield c
            return
        with self.pool.connection() as conn, _own_transaction(conn, vector=vector, schema=schema) as c:
            yield c

    @contextmanager
    def lock_connection(self) -> Iterator[psycopg.Connection]:
        if getattr(self._held, "conn", None) is not None:  # a lock inside a lock: same connection
            yield self._held.conn
            return
        with self.pool.connection() as conn:
            self._held.conn = conn
            try:
                yield conn
            finally:
                self._held.conn = None


class BorrowedConnectionSource(ConnectionSource):
    """One connection the app owns: every memhub transaction runs on it (a savepoint inside the app's own
    transaction), and it is never closed. The advisory lock is taken on the same connection."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    @contextmanager
    def transaction(self, *, vector: bool = True, schema: str | None = None) -> Iterator[psycopg.Connection]:
        with _own_transaction(self.conn, vector=vector, schema=schema) as c:
            yield c

    @contextmanager
    def lock_connection(self) -> Iterator[psycopg.Connection]:
        yield self.conn


def sqlite_path(url: str) -> str:
    """The file a `sqlite:` URL names: `sqlite:///m.db` -> `m.db`, `sqlite:////tmp/m.db` -> `/tmp/m.db`,
    `sqlite:///:memory:` -> `:memory:`. Any other shape is a `TypeError`."""
    path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else ""
    if not path.strip() or path.strip() == "/":  # no file named: "sqlite:///", "sqlite:////", blanks
        raise TypeError(f"unsupported SQLite URL {url!r}; db must be {SQLITE_FORMS}")
    return path


class SqliteSource:
    """Hands out a `sqlite3.Connection` for one memhub transaction. A connection the app gave is borrowed and
    never closed; a file URL or a callable is opened per transaction and closed after; `:memory:` keeps one
    connection for the life of the source (the database would vanish with it)."""

    def __init__(self, db: Any) -> None:
        self.borrowed: sqlite3.Connection | None = None
        self.factory: Callable[[], Any] | None = None
        self.path: str | None = None
        self._kept: sqlite3.Connection | None = None
        self._lock = threading.RLock()  # one transaction at a time on a connection that is shared
        if isinstance(db, sqlite3.Connection):
            self.borrowed = db
        elif isinstance(db, str):
            self.path = sqlite_path(db)
        elif callable(db):
            self.factory = db
        else:
            raise TypeError(f"db is a {type(db).__name__}; it must be {SQLITE_FORMS}")

    @property
    def owns_connections(self) -> bool:
        return self.borrowed is None

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if self.borrowed is not None:
            with self._lock:
                yield self.borrowed
            return
        if self.path == SQLITE_MEMORY:
            with self._lock:
                if self._kept is None:
                    self._kept = sqlite3.connect(SQLITE_MEMORY, isolation_level=None, check_same_thread=False)
                yield self._kept
            return
        if self.factory is not None:
            conn = self.factory()
            if not isinstance(conn, sqlite3.Connection):
                raise TypeError(f"the db callable returned {type(conn).__name__}; it must return a sqlite3.Connection")
        else:  # a writer waits up to 5 s for another's commit instead of failing at once
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            yield conn
        finally:
            conn.close()


def is_sqlite(db: Any) -> bool:
    """Whether `db` names SQLite without calling anything: a connection, a source or a `sqlite:` URL."""
    return isinstance(db, (sqlite3.Connection, SqliteSource)) or (isinstance(db, str) and db.startswith("sqlite:"))


def _is_pool(db: Any) -> bool:
    try:
        from psycopg_pool import ConnectionPool
    except ImportError:  # pragma: no cover - psycopg-pool is a core dependency
        return False
    return isinstance(db, ConnectionPool)


def connection_source(db: Any) -> "ConnectionSource | SqliteSource":
    """Pick the source for what the app passed as `db`: SQLite by type or `sqlite:` scheme, else Postgres."""
    if isinstance(db, (ConnectionSource, SqliteSource)):
        return db
    if is_sqlite(db):
        return SqliteSource(db)
    if isinstance(db, str):
        if db.startswith(("postgresql://", "postgres://")):
            return UrlSource(db)
        raise TypeError(f"unsupported database URL {db.split('://')[0]!r}://...; db must be {ACCEPTED}, or {SQLITE_FORMS}")
    if _is_pool(db):
        return PoolSource(db)
    if isinstance(db, psycopg.Connection):
        return BorrowedConnectionSource(db)
    if callable(db):
        return FactorySource(db)
    raise TypeError(f"db is a {type(db).__name__}; it must be {ACCEPTED}")
