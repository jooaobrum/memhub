"""The in-memory backend: the Postgres tables as dicts, for app tests and the contract suite. Nothing persists.

    Memory(db="memory://", ...)          # a new, empty ledger
    Memory(db="memory://tests", ...)     # one ledger per name in this process (AD-012)

A unit of work holds the process lock of its ledger for its whole life, works on a deep copy of the tables
and swaps the copy in on commit; an exception throws the copy away. Rows go in and out as copies, so a caller
mutating a row changes nothing stored. Each method mirrors the SQL of the `MemoryStore` method of the same
name in `storage/postgres.py`, `now()` included: one timestamp per transaction.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from memhub.storage.base import FIXES, METHODS, BoundUnitOfWork, Capability, not_available, require
from memhub.storage.migrations import SCHEMA_KEY, SCHEMA_VERSION, check, pending
from memhub.storage.rules import (
    EntityFilter,
    Filter,
    MEMORY_COLUMNS,
    NO_TABLES,
    RUN_COLUMNS,
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

SCHEMAS_FIX = FIXES[Capability.SCHEMAS]

_named: dict[tuple[str, str], "InMemoryBackend"] = {}  # (name, table prefix) -> ledger
_named_lock = threading.Lock()


def _contains(links: list[dict], link: dict) -> bool:
    """jsonb `links @> [link]`: some element holds every key/value of `link`."""
    return any(all(el.get(k) == v for k, v in link.items()) for el in links)


def _cosine(a: list[float], b: list[float]) -> float:
    """1 - pgvector's cosine distance (NaN when a vector is all zeros, as there)."""
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else math.nan


def _utc(value: Any) -> Any:
    """A `timestamptz` as Postgres returns it: aware; a naive datetime is read as UTC."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _key_text(payload: dict) -> str | None:
    """`payload->>'key'`: a string as it is, any other JSON value as its JSON text (1 -> '1', true -> 'true')."""
    key = payload.get("key")
    if key is None:
        return None
    return key if isinstance(key, str) else json.dumps(key)


def _newest_first(rows: list[Row], *keys: str) -> list[Row]:
    """ORDER BY key1 DESC, key2 DESC ...; ties go to the row inserted later."""
    return sorted(reversed(rows), key=lambda r: tuple(r[k] for k in keys), reverse=True)


class _Tables:
    def __init__(self) -> None:
        self.memory: dict[uuid.UUID, Row] = {}
        self.runs: dict[uuid.UUID, Row] = {}
        self.meta: dict[str, Any] = {}
        self.dims: int | None = None  # None: `init` never ran, there are no tables


class _Tx:
    """One transaction: the `MemoryStore` methods over a private copy of the tables."""

    def __init__(self, tables: _Tables) -> None:
        self.t = tables
        self.now = datetime.now(timezone.utc)  # Postgres now(): the transaction's start

    # --- plumbing -------------------------------------------------------------------------------------------

    def _mem(self) -> dict[uuid.UUID, Row]:
        if self.t.dims is None:
            raise EmbeddingMismatch(NO_TABLES)
        return self.t.memory

    def _runs(self) -> dict[uuid.UUID, Row]:
        if self.t.dims is None:
            raise EmbeddingMismatch(NO_TABLES)
        return self.t.runs

    @staticmethod
    def _out(row: Row | None) -> Row | None:
        return None if row is None else copy.deepcopy(row)

    def _outs(self, rows: list[Row]) -> list[Row]:
        return [copy.deepcopy(r) for r in rows]

    def _vector(self, embedding: Any) -> list[float] | None:
        vec = as_vector_list(embedding)
        if vec is not None and len(vec) != self.t.dims:
            raise StoreError(f"expected {self.t.dims} dimensions, not {len(vec)}")
        return None if vec is None else [float(x) for x in vec]

    def _check_unique(self, row: Row) -> None:
        """The Postgres constraints: UNIQUE (memory_id, version); one active row per memory_id; one active row
        per keyed slot (workspace_id, scope, user_id or '', type, payload.key)."""
        for other in self._mem().values():
            if other["id"] == row["id"]:
                continue
            if other["memory_id"] == row["memory_id"] and other["version"] == row["version"]:
                raise Conflict(f"memory {row['memory_id']} already has a version {row['version']}")
            if row["status"] != "active" or other["status"] != "active":
                continue
            if other["memory_id"] == row["memory_id"]:
                raise Conflict(f"memory {row['memory_id']} already has an active version")
            key = _key_text(row["payload"])
            if key is not None and _key_text(other["payload"]) == key and all(
                other[c] == row[c] for c in ("workspace_id", "scope", "type")
            ) and (other["user_id"] or "") == (row["user_id"] or ""):
                raise Conflict(f"an active {row['type']} with key {key!r} exists for this owner")

    def _set(self, id: Any, **cols: Any) -> Row:
        row = self._mem()[id]
        cols = {k: _utc(v) for k, v in cols.items()}
        candidate = {**row, **cols}
        if cols.get("status") == "active" or "payload" in cols:
            self._check_unique(candidate)
        row.update(cols)
        return row

    def _stale(self, row: Row, at: datetime) -> bool:
        return row["status"] == "active" and row["valid_until"] is not None and row["valid_until"] < _utc(at)

    # --- meta -----------------------------------------------------------------------------------------------

    def get_meta(self, key: str) -> Any | None:
        if self.t.dims is None:
            raise EmbeddingMismatch(NO_TABLES)
        return copy.deepcopy(self.t.meta.get(key))

    def set_meta(self, key: str, value: Any) -> None:
        if self.t.dims is None:
            raise EmbeddingMismatch(NO_TABLES)
        self.t.meta[key] = copy.deepcopy(value)

    def check_embedding_config(self, *, model: str, dims: int) -> None:
        saved = self.t.meta.get("embedding") if self.t.dims is not None else None
        if saved is None:
            raise EmbeddingMismatch(NO_TABLES)
        if saved.get("model") != model or saved.get("dims") != dims:
            raise EmbeddingMismatch(
                f"config embedding is {model!r}/{dims}d but the ledger was built with "
                f"{saved.get('model')!r}/{saved.get('dims')}d — run `memhub reembed` after "
                f"changing the embedding model"
            )

    # --- memory writes --------------------------------------------------------------------------------------

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
        row: Row = dict.fromkeys(MEMORY_COLUMNS)
        row.update(
            id=uuid.uuid4(), memory_id=memory_id or uuid.uuid4(), version=version, type=type,
            schema_version=schema_version, scope=scope, workspace_id=workspace_id, user_id=user_id, status=status,
            verified=verified, content=content, payload=copy.deepcopy(payload), entities=copy.deepcopy(entities),
            embedding=self._vector(embedding), evidence=copy.deepcopy(evidence), seen_count=seen_count, score=score,
            conflicts_with=conflicts_with, created_by=created_by, created_at=self.now,
            observed_at=_utc(observed_at or newest_observed_at(evidence) or self.now), valid_from=_utc(valid_from),
            valid_until=_utc(valid_until), durability=durability, assertion=assertion, links=copy.deepcopy(links or []),
            run_id=run_id, agent_id=agent_id,
        )
        self._check_unique(row)
        self._mem()[row["id"]] = row
        return self._out(row)

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
        new_version = active["version"] + 1
        inherited = {k: active[k] for k in ("observed_at", "valid_from", "valid_until", "durability", "assertion")}
        times = {**inherited, **(times or {})}
        links = active["links"] if links is None else links
        evidence = active["evidence"] if evidence is None else evidence
        common = dict(
            type=active["type"], schema_version=active["schema_version"], scope=active["scope"],
            workspace_id=active["workspace_id"], user_id=active["user_id"], content=content, payload=payload,
            entities=entities, embedding=embedding, evidence=evidence, created_by=created_by, memory_id=memory_id,
            version=new_version, links=links, run_id=run_id, agent_id=agent_id, **times,
        )
        if as_candidate:
            return self.add_memory(status="candidate", verified=False, conflicts_with=None, **common)
        if self._supersede(active["id"]) == 0:
            raise Conflict("the active version changed concurrently; retry the edit")
        return self.add_memory(status="active", verified=verified, **common)

    def amend_row(self, id: uuid.UUID, *, content: str, payload: dict, embedding: list[float] | None,
                  entities: list[dict] | None = None) -> None:
        row = self._mem().get(id)
        if row is not None and row["status"] == "candidate":
            cols = {"entities": copy.deepcopy(entities)} if entities is not None else {}
            self._set(id, content=content, payload=copy.deepcopy(payload), embedding=self._vector(embedding), **cols)

    def _supersede(self, row_id: uuid.UUID) -> int:
        row = self._mem().get(row_id)
        if row is None or row["status"] != "active":
            return 0
        row["status"] = "superseded"
        return 1

    def archive(self, memory_id: uuid.UUID) -> Row:
        active = self.get_active(memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        return self._out(self._set(active["id"], status="archived"))

    def reject(self, id: uuid.UUID, *, reviewed_by: str, note: str | None = None) -> Row:
        row = self.get_by_id(id)
        if row is None:
            raise NotFound(f"no row {id}")
        if row["status"] != "candidate":
            raise StoreError("only a candidate can be rejected")
        return self._out(self._set(id, status="rejected", reviewed_by=reviewed_by, reviewed_at=self.now, review_note=note))

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
                return self._out(self._set(id, status="rejected", reviewed_by=reviewed_by, reviewed_at=self.now, review_note=note))
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
                self._set(id, status="archived", reviewed_by=reviewed_by, reviewed_at=self.now,
                          review_note=note or f"merged into {new['memory_id']} v{new['version']} (keep_both)")
                return self._out(self._set(new["id"], reviewed_by=reviewed_by, reviewed_at=self.now, review_note=note))
            if resolve == "replace" and old_active is not None:
                self._set(old_active["id"], status="archived")
        else:
            sibling_active = self.get_active(row["memory_id"])
            if sibling_active is not None and sibling_active["id"] != row["id"]:
                if self._supersede(sibling_active["id"]) == 0:
                    raise Conflict("the active version changed concurrently; retry the approval")
        if edit is not None:  # the proposal stays in history as it was; the approved text is the next version
            self._set(id, status="archived", reviewed_by=reviewed_by, reviewed_at=self.now, review_note=edited_note(note))
            version = max(r["version"] for r in self._mem().values() if r["memory_id"] == row["memory_id"]) + 1
            new = self.add_memory(**edited_version(row, edit, version))
            return self._out(self._set(new["id"], reviewed_by=reviewed_by, reviewed_at=self.now, review_note=note))
        return self._out(self._set(
            id, status="active", verified=True, reviewed_by=reviewed_by, reviewed_at=self.now, review_note=note,
        ))

    def _merge(self, row: Row, new_evidence: list[dict], increment: int, valid_until: Any) -> Row:
        newest = newest_observed_at(new_evidence)
        cols: dict[str, Any] = {
            "evidence": [*row["evidence"], *copy.deepcopy(new_evidence)],
            "seen_count": row["seen_count"] + increment,
            "observed_at": max(row["observed_at"], _utc(newest)) if newest is not None else row["observed_at"],
        }
        if valid_until is not UNSET:  # the later of the two, with null (never expires) the latest
            cols["valid_until"] = None if row["valid_until"] is None or valid_until is None else max(row["valid_until"], _utc(valid_until))
        return self._out(self._set(row["id"], **cols))

    def merge_evidence(
        self, memory_id: uuid.UUID, *, new_evidence: list[dict], thread_id: str, valid_until: Any = UNSET,
    ) -> Row:
        active = self.get_active(memory_id)
        if active is None:
            raise NotFound(f"no active version for memory {memory_id}")
        new_evidence = fresh_evidence(active["evidence"], new_evidence)
        if not new_evidence:
            return active
        increment = 1 if thread_id not in {e.get("thread_id") for e in active["evidence"]} else 0
        return self._merge(self._mem()[active["id"]], new_evidence, increment, valid_until)

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
        return self._merge(self._mem()[id], new_evidence, increment, valid_until)

    def delete_row(self, id: uuid.UUID) -> None:
        if self._mem().pop(id, None) is None:
            raise NotFound(f"no row {id}")

    def delete_user(self, user_id: str) -> dict[str, int]:
        mem, runs = self._mem(), self._runs()
        gone = [k for k, r in mem.items() if r["user_id"] == user_id]
        for k in gone:
            del mem[k]
        gone_runs = [k for k, r in runs.items() if r["user_id"] == user_id]
        for k in gone_runs:
            del runs[k]
        return {"memory": len(gone), "runs": len(gone_runs)}

    # --- memory reads ---------------------------------------------------------------------------------------

    def get_by_id(self, id: uuid.UUID) -> Row | None:
        return self._out(self._mem().get(id))

    def get_active(self, memory_id: uuid.UUID) -> Row | None:
        return self._out(next(
            (r for r in self._mem().values() if r["memory_id"] == memory_id and r["status"] == "active"), None
        ))

    def history(self, memory_id: uuid.UUID) -> list[Row]:
        return self._outs(sorted((r for r in self._mem().values() if r["memory_id"] == memory_id), key=lambda r: r["version"]))

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
        wanted = {c: v for c, v in (("status", status), ("type", type), ("user_id", user_id), ("workspace_id", workspace_id)) if v is not None}
        rows = [r for r in self._mem().values() if all(r[c] == v for c, v in wanted.items())]
        if stale:
            rows = [r for r in rows if self._stale(r, now)]
        out = self._outs(_newest_first(rows, "created_at"))
        for r in out:
            r["stale"] = self._stale(r, now)
        return out

    def count_active(
        self, *, type: str, scope: str, workspace_id: str, user_id: str | None,
        statuses: tuple[str, ...] = ("active",),
    ) -> int:
        return sum(
            1 for r in self._mem().values()
            if r["status"] in statuses and r["type"] == type and r["scope"] == scope and r["workspace_id"] == workspace_id
            and (scope != "user" or r["user_id"] == user_id)
        )

    def queue(self, *, workspace_id: str | None = None) -> list[Row]:
        rows = [r for r in self._mem().values()
                if r["status"] == "candidate" and (workspace_id is None or r["workspace_id"] == workspace_id)]
        rows.sort(key=lambda r: r["created_at"])
        rows.sort(key=lambda r: r["conflicts_with"] is not None, reverse=True)
        return self._outs(rows)

    def owner_areas(self, *, scope: str, workspace_id: str, user_id: str | None) -> list[Row]:
        rows = [r for r in self._mem().values()
                if r["type"] == "area" and r["status"] == "active" and r["scope"] == scope
                and r["workspace_id"] == workspace_id and (scope != "user" or r["user_id"] == user_id)]
        return self._outs(sorted(rows, key=lambda r: r["created_at"]))

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
        link = area_link(area_memory_id)
        rows = [r for r in self._mem().values()
                if r["status"] == "active" and r["type"] != "area" and _contains(r["links"], link)
                and (stale_at is None or not self._stale(r, stale_at))]
        rows = _newest_first(rows, "observed_at", "created_at")
        return self._outs(rows if limit is None else rows[:limit])

    def move_area_links(self, source: Any, target: Any, *, created_by: str) -> int:
        old, new = area_link(source), area_link(target)
        rows = self._outs([r for r in self._mem().values() if r["status"] in ("active", "candidate") and _contains(r["links"], old)])
        for row in rows:
            links = [l for l in row["links"] if l != old]
            if new not in links:
                links.append(new)
            if row["status"] == "active":
                self.edit_memory(
                    row["memory_id"], content=row["content"], payload=row["payload"], entities=row["entities"],
                    embedding=as_vector_list(row["embedding"]), verified=row["verified"], created_by=created_by,
                    links=links,
                )
            else:
                self._set(row["id"], links=links)
        return len(rows)

    def areas_by_memory_id(self, memory_ids: list[Any]) -> dict[str, Row]:
        if not memory_ids:
            return {}
        wanted = {uuid.UUID(str(m)) for m in memory_ids}
        return {str(r["memory_id"]): self._out(r) for r in self._mem().values()
                if r["type"] == "area" and r["status"] == "active" and r["memory_id"] in wanted}

    def add_link(self, id: uuid.UUID, link: dict) -> None:
        row = self._mem().get(id)
        if row is not None and not _contains(row["links"], link):
            row["links"] = [*row["links"], copy.deepcopy(link)]

    # --- vectors --------------------------------------------------------------------------------------------

    def _nearest(self, rows: list[Row], query_embedding: list[float], limit: int) -> list[Row]:
        """ORDER BY embedding <=> query LIMIT n, with `similarity`; rows with no embedding sort last (NULLS LAST)."""
        q = [float(x) for x in query_embedding]
        scored = [(r, None if r["embedding"] is None else _cosine(r["embedding"], q)) for r in rows]
        scored.sort(key=lambda rs: (rs[1] is None, -(rs[1] if rs[1] is not None else 0.0)))
        out = []
        for r, sim in scored[:limit]:
            row = copy.deepcopy(r)
            row["similarity"] = sim
            out.append(row)
        return out

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
        filter: Filter | None = None,
    ) -> list[Row]:
        now = now or datetime.now(timezone.utc)
        rows = [
            r for r in self._mem().values()
            if r["status"] == "active" and r["workspace_id"] == workspace_id
            and (r["scope"] == "workspace" or (r["scope"] == "user" and user_id is not None and r["user_id"] == user_id))
            and (include_stale or not self._stale(r, now))
            and (r["type"] == type if type is not None else r["type"] != "area")
            and (entities is None or entities.matches(r["entities"]))
            and (filter is None or filter.matches(r))
        ]
        return apply_boosts(
            self._nearest(rows, query_embedding, max(k * 4, 20)), k=k, boost=boost, area_boost=area_boost,
            text_boost=text_boost, entity_boost=entity_boost,
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
        rows = [r for r in self._mem().values()
                if r["status"] in ("active", "candidate") and r["type"] == type and r["scope"] == scope
                and r["workspace_id"] == workspace_id and (scope != "user" or r["user_id"] == user_id)
                and (entities is None or entities.matches(r["entities"]))]
        return self._nearest(rows, query_embedding, limit)

    def iter_for_reembed(self, *, batch_size: int = 100) -> Iterator[list[Row]]:
        rows = [{"id": r["id"], "content": r["content"]} for r in sorted(self._mem().values(), key=lambda r: r["created_at"])]
        for i in range(0, len(rows), batch_size):
            yield rows[i:i + batch_size]

    def update_embedding(self, id: uuid.UUID, embedding: list[float]) -> None:
        row = self._mem().get(id)
        if row is not None:
            row["embedding"] = self._vector(embedding)

    def set_embedding_dims(self, dims: int) -> None:
        for row in self._mem().values():  # old vectors cannot be cast to another size: cleared, then re-embedded
            row["embedding"] = None
        self.t.dims = dims

    # --- runs -----------------------------------------------------------------------------------------------

    def get_watermark(self, *, source: str, thread_id: str) -> Row | None:
        rows = [r for r in self._runs().values() if r["source"] == source and r["thread_id"] == thread_id]
        rows = _newest_first(rows, "processed_at")
        return self._out(rows[0]) if rows else None

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
        row: Row = dict.fromkeys(RUN_COLUMNS)
        row.update(
            id=uuid.uuid4(), run_id=run_id, source=source, thread_id=thread_id, workspace_id=workspace_id,
            user_id=user_id, first_message_id=first_message_id, last_message_id=last_message_id,
            last_message_at=_utc(last_message_at), final_pass=final_pass, signals=copy.deepcopy(signals or []),
            dropped=copy.deepcopy(dropped or []), status=status, candidates_proposed=candidates_proposed,
            created=created, merged=merged, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd,
            processed_at=self.now,
        )
        self._runs()[row["id"]] = row
        return self._out(row)

    def list_run_summaries(self, *, last: int = 10) -> list[Row]:
        groups: dict[tuple, list[Row]] = {}
        for r in self._runs().values():
            groups.setdefault((r["run_id"], r["source"]), []).append(r)

        def total(rows: list[Row], col: str) -> Any:  # SQL sum(): null when every value is null
            values = [r[col] for r in rows if r[col] is not None]
            return sum(values) if values else None

        out = [
            {
                "run_id": run_id, "source": source,
                "started_at": min(r["processed_at"] for r in rows), "ended_at": max(r["processed_at"] for r in rows),
                "threads": len({r["thread_id"] for r in rows}), "segments": len(rows),
                **{c: total(rows, c) for c in ("candidates_proposed", "created", "merged", "tokens_in", "tokens_out", "cost_usd")},
                "extract_errors": sum(1 for r in rows if r["status"] == "extract_error"),
            }
            for (run_id, source), rows in groups.items()
        ]
        return copy.deepcopy(_newest_first(out, "ended_at")[:last])

    def run_detail(self, run_id: uuid.UUID) -> list[Row]:
        return self._outs([r for r in self._runs().values() if r["run_id"] == run_id])

    def dropped_by_reason(self, run_id: uuid.UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.run_detail(run_id):
            for item in row["dropped"]:
                counts[item["reason"]] = counts.get(item["reason"], 0) + 1
        return counts

    def purge_dropped(self, *, retention_days: int) -> int:
        cutoff = self.now - timedelta(days=retention_days)
        purged = 0
        for row in self._runs().values():
            if row["processed_at"] < cutoff and row["dropped"]:
                row["dropped"] = []
                purged += 1
        return purged

    def has_final_pass(self, *, source: str, thread_id: str) -> bool:
        return any(r["source"] == source and r["thread_id"] == thread_id and r["final_pass"] for r in self._runs().values())


class InMemoryBackend:
    """The ledger in this process's memory. See the module docstring."""

    name = "memory"
    capabilities = frozenset(Capability) - {Capability.SCHEMAS}

    def __init__(self, schema: str | None = None) -> None:
        if schema:
            raise not_available(Capability.SCHEMAS, self.name, SCHEMAS_FIX)
        self._tables = _Tables()
        self._tx_lock = threading.Lock()  # one unit of work at a time: the process lock
        self._open = threading.local()  # whether this thread has a unit of work open
        self._thread_locks: dict[tuple[str, str], threading.Lock] = {}
        self._thread_locks_guard = threading.Lock()

    @classmethod
    def for_url(cls, url: str, *, prefix: str = "memhub", schema: str | None = None) -> "InMemoryBackend":
        """`memory://` -> a new ledger; `memory://<name>` -> the one ledger of that name and table prefix in this
        process (two prefixes are two sets of tables, as on Postgres)."""
        name = url.split("://", 1)[1]
        if not name:
            return cls(schema)
        if schema:
            raise not_available(Capability.SCHEMAS, cls.name, SCHEMAS_FIX)
        with _named_lock:
            if (name, prefix) not in _named:
                _named[(name, prefix)] = cls()
            return _named[(name, prefix)]

    def init(self, *, embedding_model: str, dims: int) -> None:
        """Create the tables (idempotent): the first `init` stores the embedding model and dims, a later one with
        others is refused with `EmbeddingMismatch`, as on Postgres."""
        with self._own_unit():
            if self._tables.dims is None:
                self._tables.dims = dims
            self._tables.meta.setdefault("embedding", {"model": embedding_model, "dims": dims})
            _Tx(self._tables).check_embedding_config(model=embedding_model, dims=dims)
        self.migrate()

    def schema_version(self) -> int | None:
        """None when `init` never ran; 1 when no version is recorded; else the recorded one."""
        with self._own_unit():
            return None if self._tables.dims is None else int(self._tables.meta.get(SCHEMA_KEY, 1))

    def migrate(self, *, dry_run: bool = False) -> list[str]:
        """Nothing to alter in memory: record the version the migrations reach. Returns their names."""
        with self._own_unit():
            if self._tables.dims is None:
                raise EmbeddingMismatch(NO_TABLES)
            version = int(self._tables.meta.get(SCHEMA_KEY, 1))
            if version > SCHEMA_VERSION:
                check(version)
            todo = pending(self.name, version)
            if not dry_run and version != SCHEMA_VERSION:
                self._tables.meta[SCHEMA_KEY] = SCHEMA_VERSION
            return [m.name for m in todo]

    @contextmanager
    def _own_unit(self) -> Iterator[None]:
        """The process lock, refused to a thread that already holds it: a unit of work opened inside another would
        copy the committed tables and lose its writes when the outer one commits."""
        if getattr(self._open, "active", False):
            raise StoreError("a unit of work is already open in this thread: pass its `uow` instead of opening another")
        with self._tx_lock:
            self._open.active = True
            try:
                yield
            finally:
                self._open.active = False

    @contextmanager
    def unit_of_work(self) -> Iterator[BoundUnitOfWork]:
        with self._own_unit():
            working = copy.deepcopy(self._tables)
            tx = _Tx(working)
            yield BoundUnitOfWork(
                tx, None, {name: getattr(tx, name) for names in METHODS.values() for name in names}, self,
            )
            self._tables = working  # reached only on a clean exit: an exception leaves the old tables

    @contextmanager
    def thread_lock(self, source: str, thread_id: str) -> Iterator[bool]:
        require(self, Capability.PARALLEL_INGEST, FIXES[Capability.PARALLEL_INGEST])
        with self._thread_locks_guard:
            lock = self._thread_locks.setdefault((source, thread_id), threading.Lock())
        got = lock.acquire(blocking=False)
        try:
            yield got
        finally:
            if got:
                lock.release()

