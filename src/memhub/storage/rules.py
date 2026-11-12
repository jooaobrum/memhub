"""What every backend shares and that touches no database: row helpers, errors, the actor, the search boosts.

Nothing here imports psycopg, so the in-memory backend and the service layer load without it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

Row = dict[str, Any]

UNSET: Any = object()  # "leave the column alone", as opposed to None (= null)

ACTIVE_STATUSES = ("candidate", "active", "rejected", "archived", "superseded")

# The columns of each table, as the Postgres DDL in `storage/postgres.py` creates them: the reference schema.
MEMORY_COLUMNS = (
    "id", "memory_id", "version", "type", "schema_version", "scope", "workspace_id", "user_id", "status", "verified",
    "content", "payload", "entities", "embedding", "evidence", "seen_count", "score", "conflicts_with", "created_by",
    "created_at", "reviewed_by", "reviewed_at", "review_note",
    "observed_at", "valid_from", "valid_until", "durability", "assertion", "links", "run_id", "agent_id",
)
RUN_COLUMNS = (
    "id", "run_id", "source", "thread_id", "workspace_id", "user_id", "first_message_id", "last_message_id",
    "last_message_at", "final_pass", "signals", "dropped", "status", "candidates_proposed", "created", "merged",
    "tokens_in", "tokens_out", "cost_usd", "processed_at",
)


class StoreError(Exception):
    """Base class for store-level errors."""


class NotFound(StoreError):
    """No row matches the given id."""


class Conflict(StoreError):
    """A concurrent change invalidated this operation (e.g. optimistic-lock loss)."""


class EmbeddingMismatch(StoreError):
    """The configured embedding model/dims no longer match what `init` saved."""


class SchemaOutdated(StoreError):
    """The ledger's schema version is not this memhub's: older (run `memhub migrate`) or newer (upgrade memhub)."""


NO_TABLES = "the ledger has no tables yet: run `memhub init` (or `Memory.init()`) first"


@dataclass
class Actor:
    """Who is performing an operation: an id plus a set of roles."""

    id: str
    roles: list[str] = field(default_factory=list)

    def has_role(self, role: str) -> bool:
        return role in self.roles


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


def area_link(area_memory_id: Any) -> dict:
    return {"kind": "in_area", "memory_id": str(area_memory_id)}


def area_ids(links: list[dict]) -> list[str]:
    """The area memory_ids a row's links point at."""
    return [l["memory_id"] for l in links if l.get("kind") == "in_area"]


def as_vector_list(value: Any) -> list[float] | None:
    """Normalize an `embedding` column read back from a row: pgvector.psycopg
    returns a `Vector` object, not a plain list (the in-memory backend returns a list)."""
    if value is None:
        return None
    if hasattr(value, "to_list"):
        return value.to_list()
    return list(value)


def apply_boosts(
    rows: list[Row], *, k: int, boost: float, area_boost: Any = None, text_boost: list[str] | None = None,
    entity_boost: list[dict] | None = None,
) -> list[Row]:
    """Lift rows linked to `area_boost`, rows whose content holds a `text_boost` word, and rows naming an
    `entity_boost` entity, each by `boost` capped at 1.0; then keep the first `k`."""
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
    return rows[:k]


@dataclass(frozen=True)
class EntityFilter:
    """Which rows a search keeps by their entities; every part given must hold.

    `any_of`: the row names one of these `(type, id)`; `of_type`: the row names an entity of this type;
    `none_of_types`: the row names no entity of these types (an untagged row, for these types)."""

    any_of: tuple[tuple[str, str], ...] = ()
    of_type: str | None = None
    none_of_types: tuple[str, ...] = ()

    def matches(self, entities: list[dict]) -> bool:
        keys = {(e["type"], e["id"]) for e in entities}
        types = {t for t, _ in keys}
        return ((not self.any_of or bool(keys & set(self.any_of)))
                and (self.of_type is None or self.of_type in types)
                and not types & set(self.none_of_types))
