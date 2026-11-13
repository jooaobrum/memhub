"""The SQLite backend: the Postgres tables in one local file, behind the same ports.

    Memory(db="sqlite:///memhub.db", ...)        # a file, opened per transaction
    Memory(db=sqlite3.connect("app.db"), ...)    # the app's connection: borrowed, never closed
    Memory(db="sqlite:///:memory:", ...)         # one in-memory database for the life of the backend

Each method mirrors the SQL of the `MemoryStore` method of the same name in `storage/postgres.py`. JSON
columns are text (looked up with `json_each`), uuids and timestamps are text and come back as `uuid.UUID`
and aware `datetime`, embeddings are float32 blobs. A unit of work is `BEGIN IMMEDIATE ... COMMIT`, or a
savepoint when the app's connection is already inside a transaction (AD-004).

Vector search uses sqlite-vec's `vec_distance_cosine` when the extension loads (`pip install memhub[sqlite]`)
and ranks by cosine in Python otherwise; `MEMHUB_SQLITE_VEC=0` forces the fallback, `=1` requires the
extension. What SQLite lacks - `parallel_ingest`, `schemas` - raises the capability error.
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import struct
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from memhub.connection import SqliteSource
from memhub.storage.base import FIXES, METHODS, BoundUnitOfWork, Capability, not_available
from memhub.storage.migrations import SCHEMA_KEY, SCHEMA_VERSION, check, pending
from memhub.storage.memory import _contains, _cosine
from memhub.storage.rules import (
    EntityFilter,
    NO_TABLES,
    UNSET,
    Conflict,
    EmbeddingMismatch,
    NotFound,
    Row,
    StoreError,
    apply_boosts,
    area_link,
    as_vector_list,
    edited_note,
    edited_version,
    fresh_evidence,
    newest_observed_at,
)

VEC_ENV = "MEMHUB_SQLITE_VEC"
VEC_FIX = "install memhub[sqlite] (sqlite-vec) on a Python whose sqlite3 can load extensions, or unset MEMHUB_SQLITE_VEC"
LOCK_FIX = FIXES[Capability.PARALLEL_INGEST]

_SCHEMA = Path(__file__).with_name("sqlite_schema.sql")
_MEM_JSON = ("payload", "entities", "evidence", "links")
_MEM_TIME = ("created_at", "reviewed_at", "observed_at", "valid_from", "valid_until")
_MEM_UUID = ("id", "memory_id", "conflicts_with")
_RUN_JSON = ("signals", "dropped")
_RUN_TIME = ("last_message_at", "processed_at")
_RUN_UUID = ("id", "run_id")
_STALE_SQL = "status = 'active' AND valid_until IS NOT NULL AND valid_until < ?"
_savepoints = itertools.count()


# --- values in and out ------------------------------------------------------------------------------------------


def _ts(value: Any) -> str | None:
    """A timestamp as stored: ISO-8601 in UTC with microseconds, so text order is time order."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _id(value: Any) -> str | None:
    return None if value is None else str(value)


def _blob(vector: list[float] | None) -> bytes | None:
    return None if vector is None else struct.pack(f"<{len(vector)}f", *vector)


def _floats(blob: bytes | None) -> list[float] | None:
    return None if blob is None else list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _memory_row(raw: sqlite3.Row) -> Row:
    row = dict(raw)
    for col in _MEM_JSON:
        row[col] = json.loads(row[col])
    for col in _MEM_TIME:
        row[col] = _dt(row[col])
    for col in _MEM_UUID:
        row[col] = None if row[col] is None else uuid.UUID(row[col])
    row["verified"] = bool(row["verified"])
    row["embedding"] = _floats(row["embedding"])
    return row


def _run_row(raw: sqlite3.Row) -> Row:
    row = dict(raw)
    for col in _RUN_JSON:
        row[col] = json.loads(row[col])
    for col in _RUN_TIME:
        row[col] = _dt(row[col])
    for col in _RUN_UUID:
        row[col] = uuid.UUID(row[col])
    row["final_pass"] = bool(row["final_pass"])
    return row


def _link_sql(link: dict) -> tuple[str, list[Any]]:
    """jsonb `links @> [link]`: some element of `links` holds every key/value of `link`."""
    tests = " AND ".join("json_extract(value, ?) = ?" for _ in link)
    params = [x for k, v in link.items() for x in (f"$.{k}", v)]
    return f"EXISTS (SELECT 1 FROM json_each(links) WHERE {tests})", params


# --- the vector path --------------------------------------------------------------------------------------------


def _load_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec on `conn` unless it is there already. Raises when it cannot."""
    try:
        conn.execute("SELECT vec_version()")
        return
    except sqlite3.OperationalError:
        pass
    import sqlite_vec

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def choose_vector_path(environ: Any = None) -> str:
    """`sqlite-vec` when the extension loads on a scratch connection, else `python`. `MEMHUB_SQLITE_VEC=0`
    forces `python`; `=1` raises the capability error instead of falling back."""
    wanted = (os.environ if environ is None else environ).get(VEC_ENV, "").strip()
    if wanted == "0":
        return "python"
    scratch = sqlite3.connect(":memory:")
    try:
        _load_vec(scratch)
        return "sqlite-vec"
    except (ImportError, AttributeError, sqlite3.Error) as exc:
        if wanted == "1":
            raise not_available(Capability.VECTOR_SEARCH, "sqlite", VEC_FIX) from exc
        return "python"
    finally:
        scratch.close()


# --- one transaction --------------------------------------------------------------------------------------------



def _entity_sql(table: str, flt: EntityFilter | None) -> tuple[list[str], list[Any]]:
    """The `EntityFilter` as SQL over the JSON `entities` column."""
    if flt is None:
        return [], []
    each = f"SELECT 1 FROM json_each({table}.entities) e WHERE "
    clauses: list[str] = []
    params: list[Any] = []
    if flt.any_of:
        pairs = " OR ".join("(json_extract(e.value, '$.type') = ? AND json_extract(e.value, '$.id') = ?)" for _ in flt.any_of)
        clauses.append(f"EXISTS ({each}{pairs})")
        params += [x for pair in flt.any_of for x in pair]
    if flt.of_type is not None:
        clauses.append(f"EXISTS ({each}json_extract(e.value, '$.type') = ?)")
        params.append(flt.of_type)
    if flt.none_of_types:
        marks = ", ".join("?" for _ in flt.none_of_types)
        clauses.append(f"NOT EXISTS ({each}json_extract(e.value, '$.type') IN ({marks}))")
        params += list(flt.none_of_types)
    return clauses, params

class _Tx:
    """One transaction on `conn`: the `MemoryStore` methods, without the cursor."""

    def __init__(self, conn: sqlite3.Connection, prefix: str, vector_path: str) -> None:
        self.conn, self.prefix, self.vector_path = conn, prefix, vector_path
        self.now = datetime.now(timezone.utc)  # Postgres now(): the transaction's start
        self.mem, self.runs, self.meta = f"{prefix}_memory", f"{prefix}_memory_runs", f"{prefix}_memhub_meta"
        self._dims: int | None = None

    # --- plumbing -----------------------------------------------------------------------------------------------

    def _q(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        cur = self.conn.cursor()
        cur.row_factory = sqlite3.Row  # the cursor's own: the app's connection keeps its row factory
        return cur.execute(sql, params)

    def _memories(self, sql: str, params: Any = ()) -> list[Row]:
        return [_memory_row(r) for r in self._q(sql, params).fetchall()]

    def _vector(self, embedding: Any) -> bytes | None:
        vec = as_vector_list(embedding)
        if vec is None:
            return None
        if self._dims is None:
            self._dims = int(self.get_meta("vector_dims"))
        if len(vec) != self._dims:
            raise StoreError(f"expected {self._dims} dimensions, not {len(vec)}")
        return _blob([float(x) for x in vec])

    def _set(self, id: Any, **cols: Any) -> Row:
        self._q(f"UPDATE {self.mem} SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?", [*cols.values(), _id(id)])
        return self.get_by_id(id)

    @staticmethod
    def _stale(row: Row, at: datetime) -> bool:
        return row["status"] == "active" and row["valid_until"] is not None and row["valid_until"] < at

    # --- meta ---------------------------------------------------------------------------------------------------

    def get_meta(self, key: str) -> Any | None:
        row = self._q(f"SELECT value FROM {self.meta} WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_meta(self, key: str, value: Any) -> None:
        self._q(
            f"INSERT INTO {self.meta} (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def check_embedding_config(self, *, model: str, dims: int) -> None:
        saved = self.get_meta("embedding")
        if saved is None:
            raise EmbeddingMismatch(NO_TABLES)
        if saved.get("model") != model or saved.get("dims") != dims:
            raise EmbeddingMismatch(
                f"config embedding is {model!r}/{dims}d but the ledger was built with "
                f"{saved.get('model')!r}/{saved.get('dims')}d — run `memhub reembed` after "
                f"changing the embedding model"
            )

    # --- memory writes ------------------------------------------------------------------------------------------

    def add_memory(
        self,
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
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> Row:
        row_id = uuid.uuid4()
        values = dict(
            id=str(row_id), memory_id=str(memory_id or uuid.uuid4()), version=version, type=type,
            schema_version=schema_version, scope=scope, workspace_id=workspace_id, user_id=user_id, status=status,
            verified=int(bool(verified)), content=content, payload=json.dumps(payload), entities=json.dumps(entities),
            embedding=self._vector(embedding), evidence=json.dumps(evidence), seen_count=seen_count, score=score,
            conflicts_with=_id(conflicts_with), created_by=created_by, created_at=_ts(self.now),
            observed_at=_ts(observed_at or newest_observed_at(evidence) or self.now), valid_from=_ts(valid_from),
            valid_until=_ts(valid_until), durability=durability, assertion=assertion, links=json.dumps(links or []),
            run_id=run_id, agent_id=agent_id,
        )
        self._q(
            f"INSERT INTO {self.mem} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", list(values.values())
        )
        return self.get_by_id(row_id)

    def edit_memory(
        self,
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
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> Row:
        active = self.get_active(memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        inherited = {k: active[k] for k in ("observed_at", "valid_from", "valid_until", "durability", "assertion")}
        common = dict(
            type=active["type"], schema_version=active["schema_version"], scope=active["scope"],
            workspace_id=active["workspace_id"], user_id=active["user_id"], content=content, payload=payload,
            entities=entities, embedding=embedding, evidence=active["evidence"] if evidence is None else evidence,
            created_by=created_by, memory_id=memory_id, version=active["version"] + 1,
            links=active["links"] if links is None else links, run_id=run_id, agent_id=agent_id,
            **{**inherited, **(times or {})},
        )
        if as_candidate:
            return self.add_memory(status="candidate", verified=False, conflicts_with=None, **common)
        if self._supersede(active["id"]) == 0:
            raise Conflict("the active version changed concurrently; retry the edit")
        return self.add_memory(status="active", verified=verified, **common)

    def amend_row(self, id: uuid.UUID, *, content: str, payload: dict, embedding: list[float] | None) -> None:
        self._q(
            f"UPDATE {self.mem} SET content = ?, payload = ?, embedding = ? WHERE id = ? AND status = 'candidate'",
            (content, json.dumps(payload), self._vector(embedding), _id(id)),
        )

    def _supersede(self, row_id: uuid.UUID) -> int:
        return self._q(
            f"UPDATE {self.mem} SET status = 'superseded' WHERE id = ? AND status = 'active'", (_id(row_id),)
        ).rowcount

    def archive(self, memory_id: uuid.UUID) -> Row:
        active = self.get_active(memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        return self._set(active["id"], status="archived")

    def _reviewed(self, id: Any, reviewed_by: str, note: str | None, **cols: Any) -> Row:
        return self._set(id, **cols, reviewed_by=reviewed_by, reviewed_at=_ts(self.now), review_note=note)

    def reject(self, id: uuid.UUID, *, reviewed_by: str, note: str | None = None) -> Row:
        row = self.get_by_id(id)
        if row is None:
            raise NotFound(f"no row {id}")
        if row["status"] != "candidate":
            raise StoreError("only a candidate can be rejected")
        return self._reviewed(id, reviewed_by, note, status="rejected")

    def approve(
        self,
        id: uuid.UUID,
        *,
        reviewed_by: str,
        resolve: str | None = None,
        note: str | None = None,
        merged: dict | None = None,
        edit: dict | None = None,
    ) -> Row:
        row = self.get_by_id(id)
        if row is None:
            raise NotFound(f"no row {id}")
        if row["status"] != "candidate":
            raise StoreError("only a candidate can be approved")
        if row["conflicts_with"] is not None:
            if resolve not in ("keep_old", "replace", "keep_both"):
                raise StoreError("a conflicting candidate needs --resolve keep_old|replace|keep_both")
            if resolve == "keep_old":
                return self._reviewed(id, reviewed_by, note, status="rejected")
            old_active = self.get_active(row["conflicts_with"])
            if resolve == "keep_both" and old_active is not None and row["payload"].get("key") is not None:
                if merged is None:
                    raise StoreError("keep_both on a keyed slot needs the merged statement")
                new = self.edit_memory(
                    old_active["memory_id"], content=merged["content"], payload=merged["payload"],
                    entities=old_active["entities"], embedding=merged["embedding"], verified=True, created_by=reviewed_by,
                    evidence=[*old_active["evidence"], *fresh_evidence(old_active["evidence"], row["evidence"])],
                    times={"observed_at": max(old_active["observed_at"], row["observed_at"])},
                )
                self._reviewed(id, reviewed_by, note or f"merged into {new['memory_id']} v{new['version']} (keep_both)",
                               status="archived")
                return self._reviewed(new["id"], reviewed_by, note)
            if resolve == "replace" and old_active is not None:
                self._set(old_active["id"], status="archived")
        else:
            sibling_active = self.get_active(row["memory_id"])
            if sibling_active is not None and sibling_active["id"] != row["id"]:
                if self._supersede(sibling_active["id"]) == 0:
                    raise Conflict("the active version changed concurrently; retry the approval")
        if edit is not None:  # the proposal stays in history as it was; the approved text is the next version
            self._reviewed(id, reviewed_by, edited_note(note), status="archived")
            version = max(r["version"] for r in self.history(row["memory_id"])) + 1
            new = self.add_memory(**edited_version(row, edit, version))
            return self._reviewed(new["id"], reviewed_by, note)
        return self._reviewed(id, reviewed_by, note, status="active", verified=1)

    def _merge(self, row: Row, new_evidence: list[dict], increment: int, valid_until: Any) -> Row:
        newest = newest_observed_at(new_evidence)
        cols: dict[str, Any] = {
            "evidence": json.dumps([*row["evidence"], *new_evidence]),
            "seen_count": row["seen_count"] + increment,
            "observed_at": _ts(max(row["observed_at"], newest) if newest is not None else row["observed_at"]),
        }
        if valid_until is not UNSET:  # the later of the two, with null (never expires) the latest
            later = None if row["valid_until"] is None or valid_until is None else max(row["valid_until"], valid_until)
            cols["valid_until"] = _ts(later)
        return self._set(row["id"], **cols)

    def merge_evidence(
        self, memory_id: uuid.UUID, *, new_evidence: list[dict], thread_id: str, valid_until: Any = UNSET,
    ) -> Row:
        active = self.get_active(memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        new_evidence = fresh_evidence(active["evidence"], new_evidence)
        if not new_evidence:  # the same segment again: nothing changes
            return active
        increment = 1 if thread_id not in {e.get("thread_id") for e in active["evidence"]} else 0
        return self._merge(active, new_evidence, increment, valid_until)

    def merge_evidence_row(
        self, id: uuid.UUID, *, new_evidence: list[dict], thread_id: str, valid_until: Any = UNSET,
    ) -> Row:
        row = self.get_by_id(id)
        if row is None:
            raise NotFound(f"no row {id}")
        new_evidence = fresh_evidence(row["evidence"], new_evidence)
        if not new_evidence:
            return row
        increment = 0 if thread_id in {e.get("thread_id") for e in row["evidence"]} else 1
        return self._merge(row, new_evidence, increment, valid_until)

    def delete_row(self, id: uuid.UUID) -> None:
        if self._q(f"DELETE FROM {self.mem} WHERE id = ?", (_id(id),)).rowcount == 0:
            raise NotFound(f"no row {id}")

    def delete_user(self, user_id: str) -> dict[str, int]:
        memory = self._q(f"DELETE FROM {self.mem} WHERE user_id = ?", (user_id,)).rowcount
        runs = self._q(f"DELETE FROM {self.runs} WHERE user_id = ?", (user_id,)).rowcount
        return {"memory": memory, "runs": runs}

    # --- memory reads -------------------------------------------------------------------------------------------

    def get_by_id(self, id: uuid.UUID) -> Row | None:
        rows = self._memories(f"SELECT * FROM {self.mem} WHERE id = ?", (_id(id),))
        return rows[0] if rows else None

    def get_active(self, memory_id: uuid.UUID) -> Row | None:
        rows = self._memories(f"SELECT * FROM {self.mem} WHERE memory_id = ? AND status = 'active'", (_id(memory_id),))
        return rows[0] if rows else None

    def history(self, memory_id: uuid.UUID) -> list[Row]:
        return self._memories(f"SELECT * FROM {self.mem} WHERE memory_id = ? ORDER BY version, rowid", (_id(memory_id),))

    def list_memories(
        self,
        *,
        status: str | None = None,
        type: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        stale: bool = False,
        now: datetime | None = None,
    ) -> list[Row]:
        now = now or datetime.now(timezone.utc)
        clauses, params = [], []
        for col, val in (("status", status), ("type", type), ("user_id", user_id), ("workspace_id", workspace_id)):
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        if stale:
            clauses.append(_STALE_SQL)
            params.append(_ts(now))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._memories(f"SELECT * FROM {self.mem} {where} ORDER BY created_at DESC, rowid DESC", params)
        for row in rows:
            row["stale"] = self._stale(row, now)
        return rows

    def _owner(self, type: str, scope: str, workspace_id: str, user_id: str | None) -> tuple[list[str], list[Any]]:
        clauses, params = ["type = ?", "scope = ?", "workspace_id = ?"], [type, scope, workspace_id]
        if scope == "user":
            clauses.append("user_id = ?")
            params.append(user_id)
        return clauses, params

    def count_active(
        self, *, type: str, scope: str, workspace_id: str, user_id: str | None,
        statuses: tuple[str, ...] = ("active",),
    ) -> int:
        clauses, params = self._owner(type, scope, workspace_id, user_id)
        clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
        return self._q(
            f"SELECT count(*) AS n FROM {self.mem} WHERE {' AND '.join(clauses)}", [*params, *statuses]
        ).fetchone()["n"]

    def queue(self, *, workspace_id: str | None = None) -> list[Row]:
        clauses, params = ["status = 'candidate'"], []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        return self._memories(
            f"SELECT * FROM {self.mem} WHERE {' AND '.join(clauses)} "
            f"ORDER BY (conflicts_with IS NOT NULL) DESC, created_at ASC, rowid ASC",
            params,
        )

    def owner_areas(self, *, scope: str, workspace_id: str, user_id: str | None) -> list[Row]:
        clauses, params = self._owner("area", scope, workspace_id, user_id)
        return self._memories(
            f"SELECT * FROM {self.mem} WHERE status = 'active' AND {' AND '.join(clauses)} ORDER BY created_at, rowid", params
        )

    def nearest_area(
        self, *, scope: str, workspace_id: str, user_id: str | None, query_embedding: list[float]
    ) -> Row | None:
        rows = self.top_similar(
            type="area", scope=scope, workspace_id=workspace_id, user_id=user_id, query_embedding=query_embedding, limit=1,
        )
        return rows[0] if rows else None

    def linked_rows(
        self, area_memory_id: Any, *, stale_at: datetime | None = None, limit: int | None = None,
    ) -> list[Row]:
        linked, params = _link_sql(area_link(area_memory_id))
        clauses = ["status = 'active'", "type <> 'area'", linked]
        if stale_at is not None:
            clauses.append(f"NOT ({_STALE_SQL})")
            params.append(_ts(stale_at))
        sql = f"SELECT * FROM {self.mem} WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC, created_at DESC, rowid DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return self._memories(sql, params)

    def move_area_links(self, source: Any, target: Any, *, created_by: str) -> int:
        old, new = area_link(source), area_link(target)
        linked, params = _link_sql(old)
        rows = self._memories(f"SELECT * FROM {self.mem} WHERE status IN ('active', 'candidate') AND {linked} ORDER BY rowid", params)
        for row in rows:
            links = [l for l in row["links"] if l != old]
            if new not in links:
                links.append(new)
            if row["status"] == "active":
                self.edit_memory(
                    row["memory_id"], content=row["content"], payload=row["payload"], entities=row["entities"],
                    embedding=row["embedding"], verified=row["verified"], created_by=created_by, links=links,
                )
            else:
                self._set(row["id"], links=json.dumps(links))
        return len(rows)

    def areas_by_memory_id(self, memory_ids: list[Any]) -> dict[str, Row]:
        if not memory_ids:
            return {}
        wanted = [str(uuid.UUID(str(m))) for m in memory_ids]
        rows = self._memories(
            f"SELECT * FROM {self.mem} WHERE type = 'area' AND status = 'active' "
            f"AND memory_id IN ({', '.join('?' for _ in wanted)})",
            wanted,
        )
        return {str(r["memory_id"]): r for r in rows}

    def add_link(self, id: uuid.UUID, link: dict) -> None:
        row = self.get_by_id(id)
        if row is not None and not _contains(row["links"], link):
            self._set(id, links=json.dumps([*row["links"], link]))

    # --- vectors ------------------------------------------------------------------------------------------------

    def _nearest(self, where: str, params: list[Any], query_embedding: list[float], limit: int) -> list[Row]:
        """ORDER BY embedding <=> query LIMIT n, with `similarity`; rows with no embedding sort last, ties go to
        the row inserted first. In SQL with sqlite-vec, in Python without."""
        query = [float(x) for x in query_embedding]
        if self.vector_path == "sqlite-vec":
            raws = self._q(
                f"SELECT *, CASE WHEN embedding IS NULL THEN NULL ELSE vec_distance_cosine(embedding, ?) END AS _distance "
                f"FROM {self.mem} WHERE {where} ORDER BY _distance IS NULL, _distance, rowid LIMIT ?",
                [_blob(query), *params, limit],
            ).fetchall()
            out = []
            for raw in raws:
                row = _memory_row(raw)
                distance = row.pop("_distance")
                row["similarity"] = None if distance is None else 1 - distance
                out.append(row)
            return out
        scored = []
        for row in self._memories(f"SELECT * FROM {self.mem} WHERE {where} ORDER BY rowid", params):
            row["similarity"] = None if row["embedding"] is None else _cosine(row["embedding"], query)
            scored.append(row)
        scored.sort(key=lambda r: (r["similarity"] is None, -(r["similarity"] if r["similarity"] is not None else 0.0)))
        return scored[:limit]

    def search(
        self,
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
        entities: EntityFilter | None = None,
    ) -> list[Row]:
        clauses = ["status = 'active'", "workspace_id = ?", "(scope = 'workspace' OR (scope = 'user' AND user_id = ?))"]
        params: list[Any] = [workspace_id, user_id]
        if not include_stale:  # stale = active + expired; it stays in the ledger, just out of search
            clauses.append(f"NOT ({_STALE_SQL})")
            params.append(_ts(now or datetime.now(timezone.utc)))
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        else:  # an area row is a page header (derived text), not a claim
            clauses.append("type <> 'area'")
        more, more_params = _entity_sql(self.mem, entities)
        clauses += more
        params += more_params
        return apply_boosts(
            self._nearest(" AND ".join(clauses), params, query_embedding, max(k * 4, 20)), k=k, boost=boost,
            area_boost=area_boost, text_boost=text_boost, entity_boost=entity_boost,
        )

    def top_similar(
        self,
        *,
        type: str,
        scope: str,
        workspace_id: str,
        user_id: str | None,
        query_embedding: list[float],
        limit: int = 5,
        entities: EntityFilter | None = None,
    ) -> list[Row]:
        clauses, params = self._owner(type, scope, workspace_id, user_id)
        clauses.append("status IN ('active', 'candidate')")
        more, more_params = _entity_sql(self.mem, entities)
        clauses += more
        params += more_params
        return self._nearest(" AND ".join(clauses), params, query_embedding, limit)

    def iter_for_reembed(self, *, batch_size: int = 100) -> Iterator[list[Row]]:
        rows = [{"id": uuid.UUID(r["id"]), "content": r["content"]}
                for r in self._q(f"SELECT id, content FROM {self.mem} ORDER BY created_at, rowid").fetchall()]
        for i in range(0, len(rows), batch_size):
            yield rows[i:i + batch_size]

    def update_embedding(self, id: uuid.UUID, embedding: list[float]) -> None:
        self._q(f"UPDATE {self.mem} SET embedding = ? WHERE id = ?", (self._vector(embedding), _id(id)))

    def set_embedding_dims(self, dims: int) -> None:
        """Old vectors cannot be compared with another size: they are cleared here and re-embedded right after."""
        self._q(f"UPDATE {self.mem} SET embedding = NULL")
        self.set_meta("vector_dims", dims)
        self._dims = dims

    # --- runs ---------------------------------------------------------------------------------------------------

    def get_watermark(self, *, source: str, thread_id: str) -> Row | None:
        raw = self._q(
            f"SELECT * FROM {self.runs} WHERE source = ? AND thread_id = ? ORDER BY processed_at DESC, rowid DESC LIMIT 1",
            (source, thread_id),
        ).fetchone()
        return _run_row(raw) if raw else None

    def upsert_run(
        self,
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
        values = dict(
            id=str(uuid.uuid4()), run_id=str(run_id), source=source, thread_id=thread_id, workspace_id=workspace_id,
            user_id=user_id, first_message_id=first_message_id, last_message_id=last_message_id,
            last_message_at=_ts(last_message_at), final_pass=int(bool(final_pass)), signals=json.dumps(signals or []),
            dropped=json.dumps(dropped or []), status=status, candidates_proposed=candidates_proposed, created=created,
            merged=merged, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd, processed_at=_ts(self.now),
        )
        self._q(
            f"INSERT INTO {self.runs} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", list(values.values())
        )
        return _run_row(self._q(f"SELECT * FROM {self.runs} WHERE id = ?", (values["id"],)).fetchone())

    def list_run_summaries(self, *, last: int = 10) -> list[Row]:
        raws = self._q(
            f"""
            SELECT run_id, source,
                   min(processed_at) AS started_at, max(processed_at) AS ended_at,
                   count(DISTINCT thread_id) AS threads,
                   count(*) AS segments,
                   sum(candidates_proposed) AS candidates_proposed,
                   sum(created) AS created, sum(merged) AS merged,
                   sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out, sum(cost_usd) AS cost_usd,
                   sum(status = 'extract_error') AS extract_errors
            FROM {self.runs}
            GROUP BY run_id, source
            ORDER BY max(processed_at) DESC, max(rowid) DESC
            LIMIT ?
            """,
            (last,),
        ).fetchall()
        out = []
        for raw in raws:
            row = dict(raw)
            row["run_id"] = uuid.UUID(row["run_id"])
            row["started_at"], row["ended_at"] = _dt(row["started_at"]), _dt(row["ended_at"])
            out.append(row)
        return out

    def run_detail(self, run_id: uuid.UUID) -> list[Row]:
        return [_run_row(r) for r in self._q(f"SELECT * FROM {self.runs} WHERE run_id = ? ORDER BY rowid", (_id(run_id),)).fetchall()]

    def dropped_by_reason(self, run_id: uuid.UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.run_detail(run_id):
            for item in row["dropped"]:
                counts[item["reason"]] = counts.get(item["reason"], 0) + 1
        return counts

    def purge_dropped(self, *, retention_days: int) -> int:
        cutoff = _ts(self.now - timedelta(days=retention_days))
        return self._q(
            f"UPDATE {self.runs} SET dropped = '[]' WHERE processed_at < ? AND dropped <> '[]'", (cutoff,)
        ).rowcount

    def has_final_pass(self, *, source: str, thread_id: str) -> bool:
        return self._q(
            f"SELECT 1 FROM {self.runs} WHERE source = ? AND thread_id = ? AND final_pass LIMIT 1", (source, thread_id)
        ).fetchone() is not None


def _naming_init(method: Callable[..., Any]) -> Callable[..., Any]:
    """A call before `init()` meets no table: say so the way every backend does."""

    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            raise EmbeddingMismatch(NO_TABLES) from exc

    return call


# --- the backend ------------------------------------------------------------------------------------------------


class SqliteBackend:
    """The ledger in a SQLite database. See the module docstring."""

    name = "sqlite"
    capabilities = frozenset({Capability.TRANSACTIONS, Capability.VECTOR_SEARCH, Capability.JSON_LOOKUP})

    def __init__(self, db: Any, prefix: str = "memhub", schema: str | None = None) -> None:
        if schema:
            raise not_available(Capability.SCHEMAS, self.name, FIXES[Capability.SCHEMAS])
        self.source = db if isinstance(db, SqliteSource) else SqliteSource(db)
        self.prefix = prefix
        self.vector_path = choose_vector_path()  # "sqlite-vec" | "python"; decided once, on a scratch connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """memhub's transaction on a connection: `BEGIN IMMEDIATE`, or a savepoint inside the app's own."""
        with self.source.connection() as conn:
            if self.vector_path == "sqlite-vec":
                _load_vec(conn)
            savepoint = f"memhub_{next(_savepoints)}" if conn.in_transaction else None
            conn.execute(f"SAVEPOINT {savepoint}" if savepoint else "BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if savepoint:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    conn.execute("ROLLBACK")
                raise
            conn.execute(f"RELEASE SAVEPOINT {savepoint}" if savepoint else "COMMIT")

    def init(self, *, embedding_model: str, dims: int) -> None:
        """Create the tables and indexes (idempotent). The first `init` stores the embedding model and dims; a
        later one with others is refused with `EmbeddingMismatch`, as on Postgres."""
        with self._transaction() as conn:
            try:
                conn.execute("SELECT '{\"a\": 1}' -> '$.a'")
            except sqlite3.OperationalError as exc:
                raise StoreError(f"this SQLite ({sqlite3.sqlite_version}) lacks the JSON functions memhub needs (3.38 or newer)") from exc
            ddl = "\n".join(line for line in _SCHEMA.read_text().splitlines() if not line.startswith("--"))
            for statement in ddl.replace("{prefix}", self.prefix).split(";"):
                if statement.strip():
                    conn.execute(statement)
            tx = _Tx(conn, self.prefix, self.vector_path)
            for key, value in (("embedding", {"model": embedding_model, "dims": dims}), ("vector_dims", dims)):
                conn.execute(f"INSERT OR IGNORE INTO {tx.meta} (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            tx.check_embedding_config(model=embedding_model, dims=dims)
        self.migrate()  # a new ledger, or one an older memhub made, ends at this memhub's schema version

    def _version(self, conn: sqlite3.Connection) -> int | None:
        meta = f"{self.prefix}_memhub_meta"
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (meta,)).fetchone() is None:
            return None
        row = conn.execute(f"SELECT value FROM {meta} WHERE key = ?", (SCHEMA_KEY,)).fetchone()
        return 1 if row is None else int(json.loads(row[0]))

    def schema_version(self) -> int | None:
        """The ledger's schema version: None when there is no ledger, 1 when it has none recorded."""
        with self.source.connection() as conn:
            return self._version(conn)

    def migrate(self, *, dry_run: bool = False) -> list[str]:
        """Apply the pending migrations in one `BEGIN IMMEDIATE` transaction (a second writer waits, then finds
        nothing pending). Returns their names (pending ones with `dry_run`, nothing applied)."""
        with self._transaction() as conn:
            version = self._version(conn)
            if version is None:
                raise EmbeddingMismatch(NO_TABLES)
            if version > SCHEMA_VERSION:
                check(version)
            todo = pending(self.name, version)
            if not dry_run:
                for migration in todo:
                    for statement in migration.statements(self.prefix):
                        conn.execute(statement)
                if version != SCHEMA_VERSION:
                    conn.execute(
                        f"INSERT INTO {self.prefix}_memhub_meta (key, value) VALUES (?, ?) "
                        f"ON CONFLICT (key) DO UPDATE SET value = excluded.value", (SCHEMA_KEY, json.dumps(SCHEMA_VERSION)))
            return [m.name for m in todo]

    @contextmanager
    def unit_of_work(self) -> Iterator[BoundUnitOfWork]:
        """One transaction: commits on a clean exit, rolls back and re-raises on any exception."""
        with self._transaction() as conn:
            tx = _Tx(conn, self.prefix, self.vector_path)
            yield BoundUnitOfWork(
                tx, None, {name: _naming_init(getattr(tx, name)) for names in METHODS.values() for name in names}
            )

    @contextmanager
    def thread_lock(self, source: str, thread_id: str) -> Iterator[bool]:
        """SQLite has one writer and no advisory lock: several ingest workers are not supported."""
        raise not_available(Capability.PARALLEL_INGEST, self.name, LOCK_FIX)
        yield False  # pragma: no cover
