"""The Postgres + pgvector ledger: two tables, lifecycle transitions, search.

Every public method here takes an already-open ``cur`` (a psycopg cursor) as
its first argument, except :meth:`MemoryStore.connect` itself and
:meth:`MemoryStore.init`. This lets callers (the service layer, the ingest
pipeline) batch several writes — several memory rows plus one run row — into
a single transaction, which is what the "each segment is one transaction"
requirement needs.

    with store.connect() as conn, conn.cursor() as cur:
        store.add_memory(cur, ...)
        store.upsert_run(cur, ...)
    # committed together on a clean exit from `connect()`, rolled back on
    # any exception.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg
from psycopg.cursor import Cursor
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pgvector.psycopg import register_vector

Row = dict[str, Any]


def newest_observed_at(evidence: list[dict]) -> datetime | None:
    """The latest `observed_at` among evidence entries, or None if none carries one."""
    stamps = [datetime.fromisoformat(e["observed_at"]) for e in evidence if e.get("observed_at")]
    return max(stamps) if stamps else None


def fresh_evidence(existing: list[dict], new: list[dict]) -> list[dict]:
    """The entries of `new` that `existing` does not hold yet. Evidence is deduplicated by message id
    (an entry with no message id, like a manual one, is always new), so a segment seen twice adds nothing."""
    seen = {e["message_id"] for e in existing if e.get("message_id")}
    out = []
    for e in new:
        if e.get("message_id"):
            if e["message_id"] in seen:
                continue
            seen.add(e["message_id"])
        out.append(e)
    return out


# Stale is computed at query time, never stored: `%s` is the current time.
_STALE_SQL = "status = 'active' AND valid_until IS NOT NULL AND valid_until < %s"


def _valid_until_sql(valid_until: Any) -> str:
    return "" if valid_until is _UNSET else f", {_MERGE_VALID_UNTIL}"


def _valid_until_args(valid_until: Any) -> tuple:
    return () if valid_until is _UNSET else (valid_until, valid_until)


def area_link(area_memory_id: Any) -> dict:
    return {"kind": "in_area", "memory_id": str(area_memory_id)}


def area_ids(links: list[dict]) -> list[str]:
    """The area memory_ids a row's links point at."""
    return [l["memory_id"] for l in links if l.get("kind") == "in_area"]


def as_vector_list(value: Any) -> list[float] | None:
    """Normalize an `embedding` column read back from a row: pgvector.psycopg
    returns a `Vector` object, not a plain list."""
    if value is None:
        return None
    if hasattr(value, "to_list"):
        return value.to_list()
    return list(value)

# Columns added after v1. `init` adds each with ADD COLUMN IF NOT EXISTS, backfills existing rows, and
# only then applies NOT NULL, so it upgrades an old ledger and is a no-op on a current one.
# (name, type, backfill SQL expression for existing rows or None, NOT NULL)
ADDED_COLUMNS: tuple[tuple[str, str, str | None, bool], ...] = (
    ("observed_at", "timestamptz", "created_at", True),
    ("valid_from", "timestamptz", None, False),
    ("valid_until", "timestamptz", None, False),  # null = never expires
    ("durability", "text", None, False),  # stable | ongoing | temporary
    ("assertion", "text DEFAULT 'stated'", "'stated'", True),  # stated | inferred
    ("links", "jsonb DEFAULT '[]'::jsonb", "'[]'::jsonb", True),  # [{kind, memory_id}]; ticket 29 writes area links
)

_UNSET: Any = object()  # "leave the column alone", as opposed to None (= null)

# `valid_until` on a merge: the later of the two, with null (never expires) the latest.
_MERGE_VALID_UNTIL = (
    "valid_until = CASE WHEN valid_until IS NULL OR %s::timestamptz IS NULL THEN NULL "
    "ELSE GREATEST(valid_until, %s::timestamptz) END"
)

ACTIVE_STATUSES = ("candidate", "active", "rejected", "archived", "superseded")


class StoreError(Exception):
    """Base class for store-level errors."""


class NotFound(StoreError):
    """No row matches the given id."""


class Conflict(StoreError):
    """A concurrent change invalidated this operation (e.g. optimistic-lock loss)."""


class EmbeddingMismatch(StoreError):
    """The configured embedding model/dims no longer match what `init` saved."""


@dataclass
class Actor:
    """Who is performing an operation: an id plus a set of roles."""

    id: str
    roles: list[str] = field(default_factory=list)

    def has_role(self, role: str) -> bool:
        return role in self.roles


class MemoryStore:
    def __init__(self, database_url: str, prefix: str, schema: str | None = None) -> None:
        self.database_url = database_url
        self.prefix = prefix
        self.schema = schema  # None: whatever the connection's search_path says

    def _t(self, name: str) -> str:
        return f"{self.prefix}_{name}"

    @contextmanager
    def connect(self, *, vector: bool = True, schema: bool = True) -> Iterator[psycopg.Connection]:
        """Open a connection for one transaction: commits on a clean exit,
        rolls back on any exception. `vector=False` skips registering the
        pgvector adapter, which is needed exactly once, before the `vector`
        extension exists (see `init`)."""
        conn = psycopg.connect(self.database_url, row_factory=dict_row, autocommit=False)
        try:
            if self.schema and schema:  # `public` stays on the path: that is where the vector extension usually lives
                conn.execute(f"SET search_path TO {self.schema}, public")
            if vector:
                register_vector(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def thread_lock(self, source: str, thread_id: str) -> Iterator[bool]:
        """Held while one worker ingests a thread; False when another worker (or cron overlap) already holds it.
        A session advisory lock on its own connection: it disappears with the connection, so a crash frees it."""
        conn = psycopg.connect(self.database_url, autocommit=True)
        try:
            got = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"{self.schema}.{self.prefix}:{source}:{thread_id}",)).fetchone()[0]
            yield bool(got)
        finally:
            conn.close()

    # --- init / meta / embedding-config guard -------------------------------

    def init(self, *, embedding_model: str, dims: int) -> None:
        """Create the extension, both tables and their indexes (idempotent)."""
        mem = self._t("memory")
        runs = self._t("memory_runs")
        meta = self._t("memhub_meta")
        with self.connect(vector=False, schema=False) as conn, conn.cursor() as cur:
            if self.schema:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {mem} (
                  id uuid PRIMARY KEY,
                  memory_id uuid NOT NULL,
                  version int NOT NULL,
                  type text NOT NULL,
                  schema_version int NOT NULL,
                  scope text NOT NULL,
                  workspace_id text NOT NULL,
                  user_id text,
                  status text NOT NULL,
                  verified boolean NOT NULL,
                  content text NOT NULL,
                  payload jsonb NOT NULL,
                  entities jsonb NOT NULL DEFAULT '[]',
                  embedding vector({dims}),
                  evidence jsonb NOT NULL DEFAULT '[]',
                  seen_count int NOT NULL DEFAULT 1,
                  score real,
                  conflicts_with uuid,
                  created_by text NOT NULL,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  reviewed_by text,
                  reviewed_at timestamptz,
                  review_note text,
                  UNIQUE (memory_id, version)
                )
                """
            )
            for name, sql_type, backfill, not_null in ADDED_COLUMNS:
                cur.execute(f"ALTER TABLE {mem} ADD COLUMN IF NOT EXISTS {name} {sql_type}")
                if backfill is not None:
                    cur.execute(f"UPDATE {mem} SET {name} = {backfill} WHERE {name} IS NULL")
                if not_null:
                    cur.execute(f"ALTER TABLE {mem} ALTER COLUMN {name} SET NOT NULL")
            cur.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {self.prefix}_memory_active_uidx "
                f"ON {mem} (memory_id) WHERE status = 'active'"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.prefix}_memory_scope_idx "
                f"ON {mem} (workspace_id, scope, status, type)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.prefix}_memory_user_idx "
                f"ON {mem} (user_id) WHERE user_id IS NOT NULL"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.prefix}_memory_entities_gin ON {mem} USING gin (entities)"
            )
            cur.execute(  # a keyed type (payload has a `key`) has at most one active row per owner and key
                f"CREATE UNIQUE INDEX IF NOT EXISTS {self.prefix}_memory_key_uidx "
                f"ON {mem} (workspace_id, scope, COALESCE(user_id, ''), type, (payload->>'key')) "
                f"WHERE status = 'active' AND payload->>'key' IS NOT NULL"
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS {self.prefix}_memory_links_gin ON {mem} USING gin (links)")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {runs} (
                  id uuid PRIMARY KEY,
                  run_id uuid NOT NULL,
                  source text NOT NULL,
                  thread_id text NOT NULL,
                  workspace_id text,
                  user_id text,
                  first_message_id text NOT NULL,
                  last_message_id text NOT NULL,
                  last_message_at timestamptz NOT NULL,
                  final_pass boolean NOT NULL DEFAULT false,
                  signals jsonb NOT NULL DEFAULT '[]',
                  dropped jsonb NOT NULL DEFAULT '[]',
                  status text NOT NULL,
                  candidates_proposed int NOT NULL DEFAULT 0,
                  created int NOT NULL DEFAULT 0,
                  merged int NOT NULL DEFAULT 0,
                  tokens_in int,
                  tokens_out int,
                  cost_usd real,
                  processed_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.prefix}_runs_thread_idx "
                f"ON {runs} (source, thread_id, processed_at DESC)"
            )
            cur.execute(f"CREATE TABLE IF NOT EXISTS {meta} (key text PRIMARY KEY, value jsonb NOT NULL)")
            cur.execute(
                f"INSERT INTO {meta} (key, value) VALUES ('embedding', %s) ON CONFLICT (key) DO NOTHING",
                (Jsonb({"model": embedding_model, "dims": dims}),),
            )
            # A re-run of `init` must be a no-op: it may never overwrite what an earlier run saved.
            self.check_embedding_config(cur, model=embedding_model, dims=dims)
        # HNSW needs pgvector >= 0.5; attempt it in its own transaction so an
        # older server leaves the tables above intact and just searches unindexed.
        try:
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.prefix}_memory_embedding_hnsw "
                    f"ON {mem} USING hnsw (embedding vector_cosine_ops)"
                )
        except psycopg.Error:
            pass

    def get_meta(self, cur: Cursor, key: str) -> Any | None:
        cur.execute(f"SELECT value FROM {self._t('memhub_meta')} WHERE key = %s", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_meta(self, cur: Cursor, key: str, value: Any) -> None:
        cur.execute(
            f"INSERT INTO {self._t('memhub_meta')} (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, Jsonb(value)),
        )

    def check_embedding_config(self, cur: Cursor, *, model: str, dims: int) -> None:
        saved = self.get_meta(cur, "embedding")
        if saved is None:
            raise EmbeddingMismatch("run `memhub init` first")
        if saved.get("model") != model or saved.get("dims") != dims:
            raise EmbeddingMismatch(
                f"config embedding is {model!r}/{dims}d but the ledger was built with "
                f"{saved.get('model')!r}/{saved.get('dims')}d — run `memhub reembed` after "
                f"changing the embedding model"
            )

    # --- writes --------------------------------------------------------------

    def add_memory(
        self,
        cur: Cursor,
        *,
        type: str,
        schema_version: int,
        scope: str,
        workspace_id: str,
        user_id: str | None,
        content: str,
        payload: dict,
        entities: list[dict],
        embedding: list[float] | None,
        evidence: list[dict],
        status: str,
        verified: bool,
        created_by: str,
        memory_id: uuid.UUID | None = None,
        version: int = 1,
        seen_count: int = 1,
        score: float | None = None,
        conflicts_with: uuid.UUID | None = None,
        observed_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        durability: str | None = None,
        assertion: str = "stated",
        links: list[dict] | None = None,
    ) -> Row:
        """`observed_at` defaults to the newest evidence timestamp, else now."""
        row_id = uuid.uuid4()
        observed_at = observed_at or newest_observed_at(evidence)
        memory_id = memory_id or uuid.uuid4()
        cur.execute(
            f"""
            INSERT INTO {self._t('memory')}
              (id, memory_id, version, type, schema_version, scope, workspace_id, user_id,
               status, verified, content, payload, entities, embedding, evidence,
               seen_count, score, conflicts_with, created_by, observed_at, valid_from, valid_until, durability, assertion, links)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s, COALESCE(%s, now()), %s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                row_id, memory_id, version, type, schema_version, scope, workspace_id, user_id,
                status, verified, content, Jsonb(payload), Jsonb(entities), embedding, Jsonb(evidence),
                seen_count, score, conflicts_with, created_by, observed_at, valid_from, valid_until, durability, assertion,
                Jsonb(links or []),
            ),
        )
        return cur.fetchone()

    def edit_memory(
        self,
        cur: Cursor,
        memory_id: uuid.UUID,
        *,
        content: str,
        payload: dict,
        entities: list[dict],
        embedding: list[float] | None,
        verified: bool,
        created_by: str,
        as_candidate: bool = False,
        times: dict[str, Any] | None = None,
        links: list[dict] | None = None,
        evidence: list[dict] | None = None,
    ) -> Row:
        """Insert version N+1 (active, or candidate when `as_candidate`), and
        (for the active path) mark version N superseded, atomically. Version N+1 inherits
        observed_at / valid_from / valid_until / durability / assertion from N unless `times` gives them,
        and its links and evidence unless `links` / `evidence` give them."""
        active = self.get_active(cur, memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        new_version = active["version"] + 1
        inherited = {k: active[k] for k in ("observed_at", "valid_from", "valid_until", "durability", "assertion")}
        times = {**inherited, **(times or {})}
        links = active["links"] if links is None else links
        evidence = active["evidence"] if evidence is None else evidence
        if as_candidate:
            return self.add_memory(
                cur, type=active["type"], schema_version=active["schema_version"], scope=active["scope"],
                workspace_id=active["workspace_id"], user_id=active["user_id"], content=content,
                payload=payload, entities=entities, embedding=embedding, evidence=evidence,
                status="candidate", verified=False, created_by=created_by, memory_id=memory_id,
                version=new_version, conflicts_with=None, links=links, **times,
            )
        updated = self._supersede(cur, active["id"])
        if updated == 0:
            raise Conflict("the active version changed concurrently; retry the edit")
        return self.add_memory(
            cur, type=active["type"], schema_version=active["schema_version"], scope=active["scope"],
            workspace_id=active["workspace_id"], user_id=active["user_id"], content=content,
            payload=payload, entities=entities, embedding=embedding, evidence=evidence,
            status="active", verified=verified, created_by=created_by, memory_id=memory_id,
            version=new_version, links=links, **times,
        )

    def amend_row(self, cur: Cursor, id: uuid.UUID, *, content: str, payload: dict, embedding: list[float] | None) -> None:
        """Rewrite a pending candidate in place (its evidence is merged separately)."""
        cur.execute(
            f"UPDATE {self._t('memory')} SET content=%s, payload=%s, embedding=%s WHERE id=%s AND status='candidate'",
            (content, Jsonb(payload), embedding, id),
        )

    def _supersede(self, cur: Cursor, row_id: uuid.UUID) -> int:
        cur.execute(
            f"UPDATE {self._t('memory')} SET status = 'superseded' WHERE id = %s AND status = 'active'",
            (row_id,),
        )
        return cur.rowcount

    def archive(self, cur: Cursor, memory_id: uuid.UUID) -> Row:
        active = self.get_active(cur, memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        cur.execute(
            f"UPDATE {self._t('memory')} SET status = 'archived' WHERE id = %s RETURNING *", (active["id"],)
        )
        return cur.fetchone()

    def reject(self, cur: Cursor, id: uuid.UUID, *, reviewed_by: str, note: str | None = None) -> Row:
        row = self.get_by_id(cur, id)
        if row is None:
            raise NotFound(f"no row {id}")
        if row["status"] != "candidate":
            raise StoreError("only a candidate can be rejected")
        cur.execute(
            f"UPDATE {self._t('memory')} SET status='rejected', reviewed_by=%s, reviewed_at=now(), "
            f"review_note=%s WHERE id=%s RETURNING *",
            (reviewed_by, note, id),
        )
        return cur.fetchone()

    def approve(
        self,
        cur: Cursor,
        id: uuid.UUID,
        *,
        reviewed_by: str,
        resolve: str | None = None,
        note: str | None = None,
        merged: dict | None = None,
    ) -> Row:
        """`merged` ({content, payload, embedding}) is the single statement `keep_both` writes for a keyed slot:
        a slot holds one value, so both values become one new version of the existing row."""
        row = self.get_by_id(cur, id)
        if row is None:
            raise NotFound(f"no row {id}")
        if row["status"] != "candidate":
            raise StoreError("only a candidate can be approved")

        if row["conflicts_with"] is not None:
            if resolve not in ("keep_old", "replace", "keep_both"):
                raise StoreError("a conflicting candidate needs --resolve keep_old|replace|keep_both")
            if resolve == "keep_old":
                cur.execute(
                    f"UPDATE {self._t('memory')} SET status='rejected', reviewed_by=%s, reviewed_at=now(), "
                    f"review_note=%s WHERE id=%s RETURNING *",
                    (reviewed_by, note, id),
                )
                return cur.fetchone()
            old_active = self.get_active(cur, row["conflicts_with"])
            if resolve == "keep_both" and old_active is not None and row["payload"].get("key") is not None:
                if merged is None:
                    raise StoreError("keep_both on a keyed slot needs the merged statement")
                new = self.edit_memory(
                    cur, old_active["memory_id"], content=merged["content"], payload=merged["payload"],
                    entities=old_active["entities"], embedding=merged["embedding"], verified=True, created_by=reviewed_by,
                    evidence=[*old_active["evidence"], *fresh_evidence(old_active["evidence"], row["evidence"])],
                    times={"observed_at": max(old_active["observed_at"], row["observed_at"])},
                )
                cur.execute(
                    f"UPDATE {self._t('memory')} SET status='archived', reviewed_by=%s, reviewed_at=now(), review_note=%s "
                    f"WHERE id=%s",
                    (reviewed_by, note or f"merged into {new['memory_id']} v{new['version']} (keep_both)", id),
                )
                cur.execute(
                    f"UPDATE {self._t('memory')} SET reviewed_by=%s, reviewed_at=now(), review_note=%s WHERE id=%s RETURNING *",
                    (reviewed_by, note, new["id"]),
                )
                return cur.fetchone()
            if resolve == "replace":
                if old_active is not None:
                    cur.execute(
                        f"UPDATE {self._t('memory')} SET status='archived' WHERE id=%s", (old_active["id"],)
                    )
            # "keep_both" and "replace" both fall through to activating this candidate below.
        else:
            sibling_active = self.get_active(cur, row["memory_id"])
            if sibling_active is not None and sibling_active["id"] != row["id"]:
                if self._supersede(cur, sibling_active["id"]) == 0:
                    raise Conflict("the active version changed concurrently; retry the approval")

        cur.execute(
            f"UPDATE {self._t('memory')} SET status='active', verified=true, reviewed_by=%s, "
            f"reviewed_at=now(), review_note=%s WHERE id=%s RETURNING *",
            (reviewed_by, note, id),
        )
        return cur.fetchone()

    def merge_evidence(
        self, cur: Cursor, memory_id: uuid.UUID, *, new_evidence: list[dict], thread_id: str, valid_until: Any = _UNSET,
    ) -> Row:
        active = self.get_active(cur, memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        new_evidence = fresh_evidence(active["evidence"], new_evidence)
        if not new_evidence:  # the same segment again: nothing changes
            return active
        seen_threads = {e.get("thread_id") for e in active["evidence"]}
        increment = 1 if thread_id not in seen_threads else 0
        merged_evidence = [*active["evidence"], *new_evidence]
        cur.execute(
            f"UPDATE {self._t('memory')} SET evidence=%s, seen_count = seen_count + %s, "
            f"observed_at = GREATEST(observed_at, COALESCE(%s, observed_at)){_valid_until_sql(valid_until)} "
            f"WHERE id=%s RETURNING *",
            (Jsonb(merged_evidence), increment, newest_observed_at(new_evidence), *_valid_until_args(valid_until), active["id"]),
        )
        return cur.fetchone()

    def delete_row(self, cur: Cursor, id: uuid.UUID) -> None:
        cur.execute(f"DELETE FROM {self._t('memory')} WHERE id=%s", (id,))
        if cur.rowcount == 0:
            raise NotFound(f"no row {id}")

    def delete_user(self, cur: Cursor, user_id: str) -> dict[str, int]:
        cur.execute(f"DELETE FROM {self._t('memory')} WHERE user_id=%s", (user_id,))
        memory_deleted = cur.rowcount
        cur.execute(f"DELETE FROM {self._t('memory_runs')} WHERE user_id=%s", (user_id,))
        runs_deleted = cur.rowcount
        return {"memory": memory_deleted, "runs": runs_deleted}

    # --- reads -----------------------------------------------------------------

    def get_by_id(self, cur: Cursor, id: uuid.UUID) -> Row | None:
        cur.execute(f"SELECT * FROM {self._t('memory')} WHERE id=%s", (id,))
        return cur.fetchone()

    def get_active(self, cur: Cursor, memory_id: uuid.UUID) -> Row | None:
        cur.execute(f"SELECT * FROM {self._t('memory')} WHERE memory_id=%s AND status='active'", (memory_id,))
        return cur.fetchone()

    def history(self, cur: Cursor, memory_id: uuid.UUID) -> list[Row]:
        """Every version of a memory, oldest first, whatever its status."""
        cur.execute(f"SELECT * FROM {self._t('memory')} WHERE memory_id=%s ORDER BY version", (memory_id,))
        return cur.fetchall()

    def list_memories(
        self,
        cur: Cursor,
        *,
        status: str | None = None,
        type: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        stale: bool = False,
        now: datetime | None = None,
    ) -> list[Row]:
        """Every row, each with a computed `stale` flag; `stale=True` keeps only the stale ones."""
        now = now or datetime.now(timezone.utc)
        clauses, params = [], []
        for col, val in (("status", status), ("type", type), ("user_id", user_id), ("workspace_id", workspace_id)):
            if val is not None:
                clauses.append(f"{col} = %s")
                params.append(val)
        if stale:
            clauses.append(_STALE_SQL)
            params.append(now)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(
            f"SELECT *, ({_STALE_SQL}) AS stale FROM {self._t('memory')} {where} ORDER BY created_at DESC",
            [now, *params],
        )
        return cur.fetchall()

    def count_active(
        self, cur: Cursor, *, type: str, scope: str, workspace_id: str, user_id: str | None,
        statuses: tuple[str, ...] = ("active",),
    ) -> int:
        clauses = ["status = ANY(%s)", "type=%s", "scope=%s", "workspace_id=%s"]
        params: list[Any] = [list(statuses), type, scope, workspace_id]
        if scope == "user":
            clauses.append("user_id=%s")
            params.append(user_id)
        cur.execute(f"SELECT count(*) AS n FROM {self._t('memory')} WHERE {' AND '.join(clauses)}", params)
        return cur.fetchone()["n"]

    def queue(self, cur: Cursor, *, workspace_id: str | None = None) -> list[Row]:
        clauses = ["status='candidate'"]
        params: list[Any] = []
        if workspace_id is not None:
            clauses.append("workspace_id=%s")
            params.append(workspace_id)
        where = " AND ".join(clauses)
        cur.execute(
            f"SELECT * FROM {self._t('memory')} WHERE {where} "
            f"ORDER BY (conflicts_with IS NOT NULL) DESC, created_at ASC",
            params,
        )
        return cur.fetchall()

    def search(
        self,
        cur: Cursor,
        *,
        query_embedding: list[float],
        workspace_id: str,
        user_id: str | None = None,
        k: int = 10,
        type: str | None = None,
        entity_boost: list[dict] | None = None,
        boost: float = 0.05,
        include_stale: bool = False,
        now: datetime | None = None,
        area_boost: uuid.UUID | None = None,
        text_boost: list[str] | None = None,
    ) -> list[Row]:
        """`area_boost` (an area's memory_id) lifts the rows linked to that area; `text_boost` lifts the rows whose
        content mentions one of the words. Neither filters."""
        clauses = [
            "status='active'",
            "((scope='workspace' AND workspace_id=%(ws)s) OR "
            " (scope='user' AND workspace_id=%(ws)s AND user_id=%(uid)s))",
        ]
        params: dict[str, Any] = {"ws": workspace_id, "uid": user_id, "qe": query_embedding, "limit": max(k * 4, 20)}
        if not include_stale:  # stale = active + expired; it stays in the ledger, just out of search
            clauses.append(f"NOT ({_STALE_SQL.replace('%s', '%(now)s')})")
            params["now"] = now or datetime.now(timezone.utc)
        if type is not None:
            clauses.append("type=%(type)s")
            params["type"] = type
        else:  # an area row is a page header (derived text), not a claim
            clauses.append("type <> 'area'")
        sql = (
            f"SELECT *, 1 - (embedding <=> %(qe)s::vector) AS similarity FROM {self._t('memory')} "
            f"WHERE {' AND '.join(clauses)} ORDER BY embedding <=> %(qe)s::vector LIMIT %(limit)s"
        )
        cur.execute(sql, params)
        rows = cur.fetchall()
        if area_boost is not None:
            for row in rows:
                if area_link(area_boost) in row["links"]:
                    row["similarity"] = min(1.0, row["similarity"] + boost)
            rows.sort(key=lambda r: r["similarity"], reverse=True)
        words = [w.lower() for w in text_boost or [] if w.strip()]
        if words:
            for row in rows:
                if any(w in row["content"].lower() for w in words):
                    row["similarity"] = min(1.0, row["similarity"] + boost)
            rows.sort(key=lambda r: r["similarity"], reverse=True)
        entity_boost = entity_boost or []
        if entity_boost:
            wanted = {(e["type"], e["id"]) for e in entity_boost}
            for row in rows:
                if any((e["type"], e["id"]) in wanted for e in row["entities"]):
                    row["similarity"] = min(1.0, row["similarity"] + boost)
            rows.sort(key=lambda r: r["similarity"], reverse=True)
        rows = rows[:k]
        return rows

    def top_similar(
        self,
        cur: Cursor,
        *,
        type: str,
        scope: str,
        workspace_id: str,
        user_id: str | None,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[Row]:
        clauses = ["status IN ('active','candidate')", "type=%(type)s", "scope=%(scope)s", "workspace_id=%(ws)s"]
        params: dict[str, Any] = {
            "type": type, "scope": scope, "ws": workspace_id, "qe": query_embedding, "limit": limit,
        }
        if scope == "user":
            clauses.append("user_id=%(uid)s")
            params["uid"] = user_id
        sql = (
            f"SELECT *, 1 - (embedding <=> %(qe)s::vector) AS similarity FROM {self._t('memory')} "
            f"WHERE {' AND '.join(clauses)} ORDER BY embedding <=> %(qe)s::vector LIMIT %(limit)s"
        )
        cur.execute(sql, params)
        return cur.fetchall()

    # --- runs / watermarks -----------------------------------------------------

    def get_watermark(self, cur: Cursor, *, source: str, thread_id: str) -> Row | None:
        cur.execute(
            f"SELECT * FROM {self._t('memory_runs')} WHERE source=%s AND thread_id=%s "
            f"ORDER BY processed_at DESC LIMIT 1",
            (source, thread_id),
        )
        return cur.fetchone()

    def upsert_run(
        self,
        cur: Cursor,
        *,
        run_id: uuid.UUID,
        source: str,
        thread_id: str,
        workspace_id: str | None,
        user_id: str | None,
        first_message_id: str,
        last_message_id: str,
        last_message_at,
        final_pass: bool = False,
        signals: list[dict] | None = None,
        dropped: list[dict] | None = None,
        status: str = "ok",
        candidates_proposed: int = 0,
        created: int = 0,
        merged: int = 0,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> Row:
        cur.execute(
            f"""
            INSERT INTO {self._t('memory_runs')}
              (id, run_id, source, thread_id, workspace_id, user_id, first_message_id, last_message_id,
               last_message_at, final_pass, signals, dropped, status, candidates_proposed, created, merged,
               tokens_in, tokens_out, cost_usd)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s)
            RETURNING *
            """,
            (
                uuid.uuid4(), run_id, source, thread_id, workspace_id, user_id, first_message_id, last_message_id,
                last_message_at, final_pass, Jsonb(signals or []), Jsonb(dropped or []), status,
                candidates_proposed, created, merged, tokens_in, tokens_out, cost_usd,
            ),
        )
        return cur.fetchone()

    def list_run_summaries(self, cur: Cursor, *, last: int = 10) -> list[Row]:
        cur.execute(
            f"""
            SELECT run_id, source,
                   min(processed_at) AS started_at, max(processed_at) AS ended_at,
                   count(DISTINCT thread_id) AS threads,
                   count(*) AS segments,
                   sum(candidates_proposed) AS candidates_proposed,
                   sum(created) AS created, sum(merged) AS merged,
                   sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out, sum(cost_usd) AS cost_usd,
                   count(*) FILTER (WHERE status='extract_error') AS extract_errors
            FROM {self._t('memory_runs')}
            GROUP BY run_id, source
            ORDER BY max(processed_at) DESC
            LIMIT %s
            """,
            (last,),
        )
        return cur.fetchall()

    def run_detail(self, cur: Cursor, run_id: uuid.UUID) -> list[Row]:
        cur.execute(f"SELECT * FROM {self._t('memory_runs')} WHERE run_id=%s", (run_id,))
        return cur.fetchall()

    def dropped_by_reason(self, cur: Cursor, run_id: uuid.UUID) -> dict[str, int]:
        rows = self.run_detail(cur, run_id)
        counts: dict[str, int] = {}
        for row in rows:
            for item in row["dropped"]:
                counts[item["reason"]] = counts.get(item["reason"], 0) + 1
        return counts

    def purge_dropped(self, cur: Cursor, *, retention_days: int) -> int:
        cur.execute(
            f"UPDATE {self._t('memory_runs')} SET dropped='[]'::jsonb "
            f"WHERE processed_at < now() - (%s || ' days')::interval AND dropped <> '[]'::jsonb",
            (retention_days,),
        )
        return cur.rowcount

    # --- ingest pipeline additions (append-only) ---------------------------------

    def has_final_pass(self, cur: Cursor, *, source: str, thread_id: str) -> bool:
        cur.execute(
            f"SELECT 1 FROM {self._t('memory_runs')} WHERE source=%s AND thread_id=%s AND final_pass LIMIT 1",
            (source, thread_id),
        )
        return cur.fetchone() is not None

    def merge_evidence_row(
        self, cur: Cursor, id: uuid.UUID, *, new_evidence: list[dict], thread_id: str, valid_until: Any = _UNSET,
    ) -> Row:
        """Like `merge_evidence`, but addressed by row id so it also works on a pending candidate."""
        row = self.get_by_id(cur, id)
        if row is None:
            raise NotFound(f"no row {id}")
        new_evidence = fresh_evidence(row["evidence"], new_evidence)
        if not new_evidence:  # the same segment again: nothing changes
            return row
        increment = 0 if thread_id in {e.get("thread_id") for e in row["evidence"]} else 1
        cur.execute(
            f"UPDATE {self._t('memory')} SET evidence=%s, seen_count = seen_count + %s, "
            f"observed_at = GREATEST(observed_at, COALESCE(%s, observed_at)){_valid_until_sql(valid_until)} "
            f"WHERE id=%s RETURNING *",
            (Jsonb([*row["evidence"], *new_evidence]), increment, newest_observed_at(new_evidence),
             *_valid_until_args(valid_until), id),
        )
        return cur.fetchone()

    # --- areas -------------------------------------------------------------------

    def owner_areas(self, cur: Cursor, *, scope: str, workspace_id: str, user_id: str | None) -> list[Row]:
        """The active area rows of one owner (a user, or the workspace), oldest first."""
        clauses = ["type='area'", "status='active'", "scope=%s", "workspace_id=%s"]
        params: list[Any] = [scope, workspace_id]
        if scope == "user":
            clauses.append("user_id=%s")
            params.append(user_id)
        cur.execute(f"SELECT * FROM {self._t('memory')} WHERE {' AND '.join(clauses)} ORDER BY created_at", params)
        return cur.fetchall()

    def nearest_area(
        self, cur: Cursor, *, scope: str, workspace_id: str, user_id: str | None, query_embedding: list[float]
    ) -> Row | None:
        rows = self.top_similar(
            cur, type="area", scope=scope, workspace_id=workspace_id, user_id=user_id,
            query_embedding=query_embedding, limit=1,
        )
        return rows[0] if rows else None

    def linked_rows(
        self, cur: Cursor, area_memory_id: Any, *, stale_at: datetime | None = None, limit: int | None = None,
    ) -> list[Row]:
        """Active rows (never areas) linked to the area, newest `observed_at` first.
        `stale_at` also leaves out the rows whose `valid_until` has passed at that time."""
        clauses = ["status='active'", "type <> 'area'", "links @> %s"]
        params: list[Any] = [Jsonb([area_link(area_memory_id)])]
        if stale_at is not None:
            clauses.append(f"NOT ({_STALE_SQL})")
            params.append(stale_at)
        sql = f"SELECT * FROM {self._t('memory')} WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC, created_at DESC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        cur.execute(sql, params)
        return cur.fetchall()

    def move_area_links(self, cur: Cursor, source: Any, target: Any, *, created_by: str) -> int:
        """Point every row linked to area `source` at `target`: an active row gets a new version, a pending
        candidate is updated in place. Returns the number of rows moved."""
        old, new = area_link(source), area_link(target)
        cur.execute(
            f"SELECT * FROM {self._t('memory')} WHERE status IN ('active','candidate') AND links @> %s",
            (Jsonb([old]),),
        )
        moved = 0
        for row in cur.fetchall():
            links = [l for l in row["links"] if l != old]
            if new not in links:
                links.append(new)
            if row["status"] == "active":
                self.edit_memory(
                    cur, row["memory_id"], content=row["content"], payload=row["payload"], entities=row["entities"],
                    embedding=as_vector_list(row["embedding"]), verified=row["verified"], created_by=created_by,
                    links=links,
                )
            else:
                cur.execute(f"UPDATE {self._t('memory')} SET links=%s WHERE id=%s", (Jsonb(links), row["id"]))
            moved += 1
        return moved

    def areas_by_memory_id(self, cur: Cursor, memory_ids: list[Any]) -> dict[str, Row]:
        if not memory_ids:
            return {}
        cur.execute(
            f"SELECT * FROM {self._t('memory')} WHERE type='area' AND status='active' AND memory_id = ANY(%s)",
            ([uuid.UUID(str(m)) for m in memory_ids],),
        )
        return {str(r["memory_id"]): r for r in cur.fetchall()}

    # --- reembed -----------------------------------------------------------------

    def add_link(self, cur: Cursor, id: uuid.UUID, link: dict) -> None:
        """Append `link` to the row's links unless an identical one is already there."""
        cur.execute(
            f"UPDATE {self._t('memory')} SET links = CASE WHEN links @> %(l)s THEN links ELSE links || %(l)s END "
            f"WHERE id = %(id)s",
            {"l": Jsonb([link]), "id": id},
        )

    def iter_for_reembed(self, cur: Cursor, *, batch_size: int = 100) -> Iterator[list[Row]]:
        """Every row with content, whatever its status: a candidate approved later, or a superseded
        version consulted by reconcile, must not keep a vector from the old embedding model."""
        cur.execute(f"SELECT id, content FROM {self._t('memory')} ORDER BY created_at")
        while True:
            batch = cur.fetchmany(batch_size)
            if not batch:
                return
            yield batch

    def update_embedding(self, cur: Cursor, id: uuid.UUID, embedding: list[float]) -> None:
        cur.execute(f"UPDATE {self._t('memory')} SET embedding=%s WHERE id=%s", (embedding, id))

    def set_embedding_dims(self, cur: Cursor, dims: int) -> None:
        """Retype the vector column. Old vectors cannot be cast to another size, so they are
        cleared here and every row is re-embedded right after."""
        mem = self._t("memory")
        cur.execute(f"DROP INDEX IF EXISTS {self.prefix}_memory_embedding_hnsw")
        cur.execute(f"ALTER TABLE {mem} ALTER COLUMN embedding TYPE vector({dims}) USING NULL")
        # pgvector's HNSW caps at 2000 dims; a failed CREATE must not abort the surrounding transaction.
        cur.execute("SAVEPOINT hnsw")
        try:
            cur.execute(
                f"CREATE INDEX {self.prefix}_memory_embedding_hnsw ON {mem} USING hnsw (embedding vector_cosine_ops)"
            )
        except psycopg.Error:
            cur.execute("ROLLBACK TO SAVEPOINT hnsw")
        else:
            cur.execute("RELEASE SAVEPOINT hnsw")
