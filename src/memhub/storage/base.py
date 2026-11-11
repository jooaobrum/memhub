"""The storage ports: what memhub asks of a database, whichever one it is.

    with backend.unit_of_work() as uow:          # begin
        row = uow.memories.add_memory(...)
        uow.runs.upsert_run(...)
    # committed on a clean exit, rolled back (and the exception re-raised) on any error

A `UnitOfWork` is one transaction with four repositories. Their methods are `MemoryStore`'s methods of the
same name without the `cur` argument (`METHODS` lists them), so Postgres serves them by binding one cursor and
the in-memory backend by working on one snapshot. A backend that lacks a capability says so with
`require(...)`: `NotImplementedError("<capability> is not available on <backend>: <fix>")`.
"""
from __future__ import annotations

import functools
from enum import Enum
from typing import Any, Callable, ContextManager, Protocol, runtime_checkable


class Capability(str, Enum):
    TRANSACTIONS = "transactions"
    VECTOR_SEARCH = "vector_search"
    JSON_LOOKUP = "json_lookup"
    PARALLEL_INGEST = "parallel_ingest"
    SCHEMAS = "schemas"


# Which repository serves which method. Every name is a `MemoryStore` method; the parameters are the same, minus `cur`.
METHODS: dict[str, tuple[str, ...]] = {
    "memories": (
        "add_memory", "edit_memory", "amend_row", "archive", "reject", "approve", "merge_evidence",
        "merge_evidence_row", "delete_row", "delete_user", "get_by_id", "get_active", "history", "list_memories",
        "count_active", "queue", "owner_areas", "linked_rows", "move_area_links", "areas_by_memory_id", "add_link",
    ),
    "runs": (
        "get_watermark", "upsert_run", "list_run_summaries", "run_detail", "dropped_by_reason", "purge_dropped",
        "has_final_pass",
    ),
    "meta": ("get_meta", "set_meta", "check_embedding_config"),
    "vectors": ("search", "top_similar", "nearest_area", "iter_for_reembed", "update_embedding", "set_embedding_dims"),
}


# The methods that need more than `transactions`, and what to do on a backend that lacks it.
NEEDS: dict[str, Capability] = {
    "search": Capability.VECTOR_SEARCH, "top_similar": Capability.VECTOR_SEARCH, "nearest_area": Capability.VECTOR_SEARCH,
    "linked_rows": Capability.JSON_LOOKUP, "move_area_links": Capability.JSON_LOOKUP, "add_link": Capability.JSON_LOOKUP,
}
FIXES: dict[Capability, str] = {
    Capability.TRANSACTIONS: "use Postgres",
    Capability.VECTOR_SEARCH: "install a vector extension for this database, or use Postgres",
    Capability.JSON_LOOKUP: "use a database with JSON queries, or Postgres",
    Capability.PARALLEL_INGEST: "run one ingest worker or use Postgres",  # the PRD's wording
    Capability.SCHEMAS: "remove database_schema from the config, or use Postgres",
}


class MemoryRepository(Protocol):
    """Memory rows: versions, lifecycle, review, areas. Methods: `METHODS["memories"]`."""

    def __getattr__(self, name: str) -> Callable[..., Any]: ...


class RunRepository(Protocol):
    """Run rows: watermarks, summaries, dropped candidates. Methods: `METHODS["runs"]`."""

    def __getattr__(self, name: str) -> Callable[..., Any]: ...


class MetaRepository(Protocol):
    """Key/value settings of the ledger: the embedding model and dims. Methods: `METHODS["meta"]`."""

    def __getattr__(self, name: str) -> Callable[..., Any]: ...


class VectorIndex(Protocol):
    """Nearest-neighbour reads and embedding writes. Methods: `METHODS["vectors"]`."""

    def __getattr__(self, name: str) -> Callable[..., Any]: ...


class UnitOfWork(Protocol):
    memories: MemoryRepository
    runs: RunRepository
    meta: MetaRepository
    vectors: VectorIndex


@runtime_checkable
class Backend(Protocol):
    name: str
    capabilities: frozenset[Capability]

    def unit_of_work(self) -> ContextManager[UnitOfWork]: ...

    def thread_lock(self, source: str, thread_id: str) -> ContextManager[bool]: ...

    def init(self, *, embedding_model: str, dims: int) -> None: ...


def not_available(capability: Capability, backend: str, fix: str) -> NotImplementedError:
    return NotImplementedError(f"{capability.value} is not available on {backend}: {fix}")


def require(backend: Backend, capability: Capability, fix: str) -> None:
    """Raise the capability error unless `backend` has `capability`."""
    if capability not in backend.capabilities:
        raise not_available(capability, backend.name, fix)


class Repository:
    """One repository of a unit of work: the methods `METHODS[kind]` names, each looked up with `resolve`."""

    def __init__(self, kind: str, resolve: Callable[[str], Callable[..., Any]]) -> None:
        self._kind, self._names, self._resolve = kind, METHODS[kind], resolve

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_") or name not in self._names:
            raise AttributeError(f"{self._kind} has no method {name!r}")
        return self._resolve(name)

    def __dir__(self) -> list[str]:
        return list(self._names)


class BoundUnitOfWork:
    """A unit of work whose methods are `target`'s, with `first` (a cursor, a transaction) passed first. A method
    that `NEEDS` a capability `backend` lacks raises the capability error when it is looked up."""

    def __init__(self, target: Any, first: Any, overrides: dict[str, Callable[..., Any]] | None = None,
                 backend: Backend | None = None) -> None:
        overrides = overrides or {}

        def resolve(name: str) -> Callable[..., Any]:
            if backend is not None and name in NEEDS:
                require(backend, NEEDS[name], FIXES[NEEDS[name]])
            return overrides[name] if name in overrides else functools.partial(getattr(target, name), first)

        self.memories = Repository("memories", resolve)
        self.runs = Repository("runs", resolve)
        self.meta = Repository("meta", resolve)
        self.vectors = Repository("vectors", resolve)
