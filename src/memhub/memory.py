"""`Memory`: memhub with the app's own database connection and models, and mem0-style methods.

    from memhub import Memory

    m = Memory(db=pool, llm=chat_model, embedder=embeddings)   # a pool, a connection, a callable or a URL
    m.init()                                                   # once: creates the tables (idempotent)
    me = m.scope(workspace_id="acme", user_id="anna", run_id=trace_id)
    me.add({"content": "Prefers answers in Portuguese"}, type="fact")
    me.search("language")

`Memory` is a facade over `MemoryService`: every rule (validation, injection scan, ownership, review) lives
there. It adds three things: the ids every call must carry (`CallContext`), the storage the app lends (a
Postgres connection via `memhub.connection`, or `memory://` for tests; see `memhub.storage`), and one result format for every write (`AddResult`, `MemoryEvent`). Embeddings are
never returned.
"""
from __future__ import annotations

import functools
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from memhub.config import Config, EmbeddingConfig
from memhub.filters import SearchFilter
from memhub.filters import parse as parse_filters
from memhub.settings import Parts, Resolved, embedding_model_id, resolve
from memhub.middleware import MemoryMiddleware
from memhub.service import MemoryService, NotFound, ValidationError
from memhub.storage import Capability, open_backend, require
from memhub.storage.base import FIXES
from memhub.storage.migrations import check as check_schema
from memhub.storage.rules import NO_TABLES, Actor, EmbeddingMismatch, Row
from memhub.toolkit import MemoryToolkit
from memhub.types import INTERNAL_TYPES

__all__ = ["AddResult", "CallContext", "Dropped", "Memory", "MemoryEvent", "ScopedMemory"]

Event = Literal["ADD", "UPDATE", "MERGE", "PENDING", "CONFLICT"]
ADMIN = "workspace_admin"


class MemoryEvent(BaseModel):
    """One memory a write touched. `id` is the stable memory id apps store and pass back; `version_id` is the
    row of this version (what `memhub queue` prints)."""

    id: str
    version_id: str
    version: int
    event: Event
    status: Literal["active", "candidate", "rejected", "archived"]
    scope: Literal["user", "workspace"]
    type: str
    content: str
    conflicts_with: str | None = None


class Dropped(BaseModel):
    """Something the pipeline decided not to keep, and why (`no_quote`, `low_score`, `injection`, ...)."""

    content: str | None = None
    reason: str


class AddResult(BaseModel):
    run_id: str
    results: list[MemoryEvent]
    dropped: list[Dropped] = Field(default_factory=list)


class CallContext(BaseModel):
    """Who a call is for. `workspace_id`, `user_id` and `run_id` are required; `user_id` is also the actor."""

    workspace_id: str
    user_id: str
    run_id: str
    agent_id: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["user"])

    @classmethod
    def of(cls, workspace_id: Any, user_id: Any, run_id: Any, agent_id: Any = None, roles: Any = None) -> "CallContext":
        missing = [n for n, v in (("workspace_id", workspace_id), ("user_id", user_id), ("run_id", run_id))
                   if v is None or (isinstance(v, str) and not v.strip())]
        if missing:
            raise ValidationError(f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required on every memhub call")
        return cls(workspace_id=str(workspace_id), user_id=str(user_id), run_id=str(run_id),
                   agent_id=None if agent_id is None else str(agent_id), roles=list(roles if roles is not None else ["user"]))

    @property
    def actor(self) -> Actor:
        return Actor(id=self.user_id, roles=self.roles)

    @property
    def is_admin(self) -> bool:
        return ADMIN in self.roles


def _clean(row: Row) -> dict[str, Any]:
    """A row as an app sees it: everything but the embedding."""
    return {k: v for k, v in row.items() if k != "embedding"}


def _entity_type(e: Any) -> Any:
    return e.get("type") if isinstance(e, dict) else getattr(e, "type", None)


def _event(row: Row, event: Event) -> MemoryEvent:
    return MemoryEvent(
        id=str(row["memory_id"]), version_id=str(row["id"]), version=row["version"], event=event,
        status=row["status"], scope=row["scope"], type=row["type"], content=row["content"],
        conflicts_with=str(row["conflicts_with"]) if row.get("conflicts_with") else None,
    )


def _push_event(outcome: str, row: Row) -> Event:
    """What a routed proposal is to the app: a new memory, a new version, the same fact, or a contradiction."""
    if outcome == "merged":
        return "MERGE"
    if outcome == "conflict":
        return "CONFLICT"
    if row["status"] == "candidate":
        return "PENDING"
    return "UPDATE" if outcome == "superseded" else "ADD"


def _dropped_content(dropped: dict[str, Any]) -> str | None:
    """The text of a dropped candidate: its `content` field, else its text fields joined."""
    fields = (dropped.get("candidate") or {}).get("fields") or {}
    if isinstance(fields.get("content"), str):
        return fields["content"]
    return " ".join(v for v in fields.values() if isinstance(v, str)) or None


def _uuid(value: Any) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"no memory {value}") from exc


# What every `Memory` call relies on; `schemas` is added when the config sets `database_schema`.
_NEEDED = (Capability.TRANSACTIONS, Capability.VECTOR_SEARCH, Capability.JSON_LOOKUP)


class Memory:
    """memhub for one app. See the module docstring."""

    def __init__(
        self,
        db: Any = None,
        llm: Any = None,
        embedder: Any = None,
        judge: Any = None,
        *,
        config: str | Path | dict | Config | Resolved | None = None,
        overrides: dict[str, Any] | None = None,
        roles: list[str] | None = None,
    ) -> None:
        """`roles` are the default roles of every call (`["user"]`); a call or `scope(...)` may pass its own.
        What is not passed comes from memhub.yaml, `MEMHUB_*` and `overrides` (see `memhub.settings`): the app's own
        `connect: "module:function"`, else `db:` and `models:`. `Memory()` with no arguments is all of that."""
        self.resolved = resolve(config, overrides)
        self.settings = self.resolved.config.model_copy(deep=True)
        self.parts = Parts(self.resolved, passed={"db": db, "llm": llm, "embedder": embedder, "judge": judge})
        self.store = open_backend(self.parts.db, self.settings.project_prefix, self.settings.database_schema)
        self._capable = False
        if not getattr(self.store, "deferred", False):  # a callable `db` is checked on first use: nothing opens here
            self._check_capabilities()
        self.embedder = self.parts.embedder
        self._embedding_model = embedding_model_id(self.embedder, self.settings)
        self.settings.embeddings = None  # known once one probe embed gives the dims; see `_ready`
        self.registry = self.settings.build_registry()
        self.service = MemoryService(store=self.store, settings=self.settings, registry=self.registry, embeddings=self.embedder)
        self.roles = list(roles) if roles is not None else ["user"]
        self._checked = False

    @property
    def llm(self) -> Any:
        """The app's chat model (built from the config the first time it is needed)."""
        return self.parts.llm

    @property
    def judge(self) -> Any:
        return self.parts.judge

    # --- setup ------------------------------------------------------------------------------------------------

    @property
    def source(self) -> Any:
        """Postgres and SQLite: where connections come from."""
        return getattr(self.store, "source", None)

    def _check_capabilities(self) -> None:
        """What every call relies on, and `schemas` when the config sets `database_schema`: fail at startup."""
        if self._capable:
            return
        for capability in (*_NEEDED, *((Capability.SCHEMAS,) if self.settings.database_schema else ())):
            require(self.store, capability, FIXES[capability])
        self._capable = True

    def _probe(self) -> EmbeddingConfig:
        if self.settings.embeddings is None:
            dims = len(self.embedder.embed_query("memhub"))
            self.settings.embeddings = EmbeddingConfig(model=str(self._embedding_model), dims=dims)
        return self.settings.embeddings

    def init(self) -> None:
        """Create the tables, or add what an older memhub lacks (idempotent). Stores the embedding model and the
        dims of one probe embed; a later `Memory` with another embedder is refused with `EmbeddingMismatch`."""
        self._check_capabilities()
        emb = self._probe()
        self.store.init(embedding_model=emb.model, dims=emb.dims)
        self._checked = True

    def migrate(self, *, dry_run: bool = False) -> list[str]:
        """Bring the ledger to this memhub's schema version (`memhub migrate` from code). Returns the migrations
        applied, or pending with `dry_run`. The next call of this instance checks the ledger again."""
        self._check_capabilities()
        done = self.store.migrate(dry_run=dry_run)
        self._checked = False
        return done

    def _ready(self) -> None:
        """Once per instance: the ledger exists, is at this memhub's schema version, and was built with this
        embedder. An older ledger raises `SchemaOutdated` naming `memhub migrate` before any other read or write."""
        if self._checked:
            return
        self._check_capabilities()
        version = self.store.schema_version()
        if version is None:
            raise EmbeddingMismatch(NO_TABLES)
        check_schema(version)
        emb = self._probe()
        with self.store.unit_of_work() as uow:  # no tables yet: EmbeddingMismatch naming `init()`, on every backend
            uow.meta.check_embedding_config(model=emb.model, dims=emb.dims)
        self._checked = True

    def _ctx(self, workspace_id: Any, user_id: Any, run_id: Any, agent_id: Any = None, roles: Any = None) -> CallContext:
        return CallContext.of(workspace_id, user_id, run_id, agent_id, self.roles if roles is None else roles)

    def _call(self, workspace_id: Any, user_id: Any, run_id: Any, agent_id: Any = None, roles: Any = None) -> CallContext:
        ctx = self._ctx(workspace_id, user_id, run_id, agent_id, roles)
        self._ready()
        return ctx

    def _visible(self, row: Row | None, ctx: CallContext) -> Row:
        """A row the caller may see: in their workspace, and a workspace row, their own, or any for an admin."""
        if row is None or row["workspace_id"] != ctx.workspace_id or (
            row["scope"] == "user" and row["user_id"] != ctx.user_id and not ctx.is_admin
        ):
            raise NotFound("no such memory")
        return row

    def _active(self, memory_id: Any, ctx: CallContext) -> Row:
        with self.store.unit_of_work() as uow:
            return self._visible(uow.memories.get_active(_uuid(memory_id)), ctx)

    def scope(self, workspace_id: Any = None, user_id: Any = None, run_id: Any = None, agent_id: Any = None,
              roles: list[str] | None = None) -> "ScopedMemory":
        """A client with the ids bound: the same methods, no id arguments."""
        return ScopedMemory(self, self._ctx(workspace_id, user_id, run_id, agent_id, roles))

    # --- writes -----------------------------------------------------------------------------------------------

    def add(self, data: str | dict[str, Any] | list[dict[str, Any]], *, workspace_id: Any = None, user_id: Any = None,
            run_id: Any = None, agent_id: Any = None, roles: list[str] | None = None, type: str | None = None,
            scope: str = "user", infer: bool = False, thread_id: str | None = None,
            entities: list[dict[str, Any]] | None = None) -> AddResult:
        """`infer=False` (the default, AD-019): a memory from its fields (or a text, stored as a `fact`), with no
        model call. User scope is active at once; a workspace memory waits for review (`PENDING`) unless the caller
        is a `workspace_admin`.

        `infer=True`: `data` is a conversation, `[{"role": "user"|"assistant", "content", "timestamp"?}]` (or a
        string: one user message). The messages not extracted yet in `thread_id` (default: `run_id`) go through
        extraction, grounding, admission, reconcile and routing; the result lists every memory written (`ADD`,
        `PENDING`, `UPDATE`, `MERGE`, `CONFLICT`) and every candidate dropped, with its reason."""
        ctx = self._ctx(workspace_id, user_id, run_id, agent_id, roles)
        subjects = self._subjects(entities)
        if infer:
            return self._push(data, ctx, type=type, thread_id=thread_id, subjects=subjects)
        if isinstance(data, list):
            raise ValidationError("a list of messages is a conversation: pass infer=True to extract memories from it")
        if type is None and "fact" not in self.settings.types:
            listed = ", ".join(t for t in self.settings.types if t not in INTERNAL_TYPES)
            raise ValidationError(f"type is required: this project has no 'fact' type (types: {listed})")
        self._ready()
        fields = {"content": data} if isinstance(data, str) else dict(data)
        if subjects:  # the bound subjects replace the given entities of their types (AD-032)
            bound = {e["type"] for e in subjects}
            fields["entities"] = [*(e for e in fields.get("entities") or [] if _entity_type(e) not in bound), *subjects]
        row = self.service.add(
            ctx.actor, type=type or "fact", scope=scope, fields=fields, workspace_id=ctx.workspace_id,
            user_id=ctx.user_id, run_id=ctx.run_id, agent_id=ctx.agent_id,
        )
        return AddResult(run_id=ctx.run_id, results=[_event(row, "ADD" if row["status"] == "active" else "PENDING")])

    def _subjects(self, entities: Any) -> list[dict[str, str]]:
        """`entities=` checked before any model or database work: `{"type", "id"}` items of declared types."""
        if entities is None:
            return []
        if not isinstance(entities, list):
            raise ValidationError(f'entities must be a list of {{"type", "id"}}, not {type(entities).__name__}')
        declared = self.settings.declared_entity_types()
        out = []
        for e in entities:
            if not isinstance(e, dict) or not isinstance(e.get("type"), str) or not isinstance(e.get("id"), str) \
                    or not e["type"] or not e["id"] or set(e) - {"type", "id"}:
                raise ValidationError(f'an entity is {{"type": str, "id": str}}, got {e!r}')
            if e["type"] not in declared:
                raise ValidationError(f"unknown entity type {e['type']!r} in {e!r} (declared: {declared})")
            out.append({"type": e["type"], "id": e["id"]})
        return out

    def _filters(self, filters: Any, types: list[str] | None, widen: bool) -> SearchFilter:
        """`filters` parsed and checked before any model or database work (`memhub.filters`, #25)."""
        return parse_filters(filters, declared=self.settings.declared_entity_types(), types=types, widen=widen)

    def _push(self, data: Any, ctx: CallContext, *, type: str | None, thread_id: str | None,
              subjects: list[dict] | None = None) -> AddResult:
        from memhub.pipeline.push import push_messages, to_messages

        if type is not None:
            raise ValidationError("infer=True extracts each memory's type itself: do not pass type")
        if isinstance(data, dict):
            raise ValidationError('infer=True takes a list of {"role", "content"} messages, or a string, not fields')
        to_messages(data)  # refuse a bad call before any model or database work
        if self.llm is None:
            raise ValidationError("infer=True needs an llm to extract memories: pass Memory(llm=...) or set models.chat")
        self._ready()
        pushed = push_messages(
            data, store=self.store, settings=self.settings, registry=self.registry, extractor=self.llm,
            judge=self.judge, embeddings=self.embedder, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
            subjects=subjects or None,
            run_id=ctx.run_id, agent_id=ctx.agent_id, thread_id=thread_id,
        )
        results = [_event(row, _push_event(routed.outcome, row)) for seg in pushed for routed, row in seg.written]
        dropped = [Dropped(content=_dropped_content(d), reason=d["reason"]) for seg in pushed for d in seg.dropped]
        dropped += [Dropped(content=None, reason="extract_error") for seg in pushed if seg.extract_error]
        return AddResult(run_id=ctx.run_id, results=results, dropped=dropped)

    def update(self, id: Any, fields: dict[str, Any], *, workspace_id: Any = None, user_id: Any = None,
               run_id: Any = None, agent_id: Any = None, roles: list[str] | None = None) -> MemoryEvent:
        """A new active version; a person's edit is never superseded by ingest."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        active = self._active(id, ctx)
        row = self.service.edit(ctx.actor, active["memory_id"], fields, run_id=ctx.run_id, agent_id=ctx.agent_id)
        return _event(row, "UPDATE")

    def delete(self, id: Any, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
               agent_id: Any = None, roles: list[str] | None = None, hard: bool = False) -> None:
        """Archive the memory (kept in history). `hard=True` removes one row for good, under the service's rules:
        a rejected candidate, or one of your own user rows."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        if hard:
            with self.store.unit_of_work() as uow:
                row = self._visible(uow.memories.get_by_id(_uuid(id)), ctx)
            self.service.delete(ctx.actor, row["id"])
            return
        active = self._active(id, ctx)
        self.service.archive(ctx.actor, active["memory_id"])

    def delete_all(self, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
                   agent_id: Any = None, roles: list[str] | None = None) -> dict[str, int]:
        """Erase every memory and run row of the caller's `user_id`."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        out = self.service.delete_user(ctx.actor, ctx.user_id)
        return {"deleted": out["memory"], "runs": out["runs"]}

    # --- review -----------------------------------------------------------------------------------------------

    def _candidate(self, id: Any, ctx: CallContext) -> Row:
        """The row `id` names: a version row, or a memory's newest candidate."""
        key = _uuid(id)
        with self.store.unit_of_work() as uow:
            row = uow.memories.get_by_id(key)
            if row is None:
                pending = [r for r in uow.memories.history(key) if r["status"] == "candidate"]
                row = max(pending, key=lambda r: (r["version"], r["created_at"])) if pending else None
        return self._visible(row, ctx)

    def approve(self, id: Any, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
                agent_id: Any = None, roles: list[str] | None = None, resolve: str | None = None,
                note: str | None = None) -> MemoryEvent:
        """Make a candidate active. `id` is a memory id (its newest candidate) or a version id."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        row = self.service.approve(ctx.actor, self._candidate(id, ctx)["id"], resolve=resolve, note=note)
        return _event(row, "ADD" if row["version"] == 1 else "UPDATE")

    def reject(self, id: Any, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
               agent_id: Any = None, roles: list[str] | None = None, note: str | None = None) -> MemoryEvent:
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        row = self.service.reject(ctx.actor, self._candidate(id, ctx)["id"], note=note)
        return _event(row, "ADD" if row["version"] == 1 else "UPDATE")

    def promote(self, id: Any, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
                agent_id: Any = None, roles: list[str] | None = None) -> MemoryEvent:
        """Copy your user memory into the workspace review queue; returns the new workspace candidate."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        active = self._active(id, ctx)
        row = self.service.promote(ctx.actor, active["memory_id"], run_id=ctx.run_id, agent_id=ctx.agent_id)
        return _event(row, "PENDING")

    def queue(self, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
              agent_id: Any = None, roles: list[str] | None = None) -> list[dict[str, Any]]:
        """Candidates of the workspace, conflicts first; another user's private candidates only for a reviewer."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        reviewer = any(r in ctx.roles for r in self.settings.roles.approve_workspace)
        rows = self.service.queue(ctx.actor, workspace_id=ctx.workspace_id)
        return [_clean(r) for r in rows if reviewer or r["scope"] == "workspace" or r["user_id"] == ctx.user_id]

    # --- reads ------------------------------------------------------------------------------------------------

    def search(self, query: str, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
               agent_id: Any = None, roles: list[str] | None = None, top_k: int = 5,
               types: list[str] | None = None, filters: dict[str, Any] | None = None,
               widen: bool = False) -> list[dict[str, Any]]:
        """Your active user memories and the workspace's, nearest first, stale ones left out.

        `filters={"entities": [{"type", "id"}, ...]}` keeps the memories about one of those subjects, ranked in
        that order; `widen=True` then adds the memories about no isolated subject (`general`).
        `filters={"entity_type": "machine"}` keeps the memories about any machine. `filters["types"]` is the
        `types` parameter. Ids take `in`, `prefix`, `contains`; `areas`, `observed_at`, `valid_until`, `content`,
        `AND`, `OR`, `NOT` narrow further (`docs/filters.md`). With an entity in the filter, each hit says what it
        matched in `matched_on`."""
        flt = self._filters(filters, types, widen)
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        rows = self.service.search(ctx.actor, query, workspace_id=ctx.workspace_id, user_id=ctx.user_id, k=top_k,
                                   filter=flt, widen=widen)
        return [_clean(r) for r in rows]

    def get(self, id: Any, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
            agent_id: Any = None, roles: list[str] | None = None) -> dict[str, Any]:
        """The active version of a memory; `NotFound` when it is not yours to see."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        return _clean(self._active(id, ctx))

    def get_all(self, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None, agent_id: Any = None,
                roles: list[str] | None = None, status: str = "active", type: str | None = None,
                filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Your user memories and the workspace's, with that status, newest first; `filters` as in `search`
        (`entities`, `entity_type`; `types` keeps the memories of those types)."""
        flt = self._filters(filters, None, False)
        if flt.types and type is not None:
            raise ValidationError("type is given both as a parameter and as filters['types']: keep one")
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        rows = self.service.list(ctx.actor, status=status, type=type, workspace_id=ctx.workspace_id,
                                 filter=flt, viewer=ctx.user_id)
        return [_clean(r) for r in rows if r["scope"] == "workspace" or r["user_id"] == ctx.user_id]

    def history(self, id: Any, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
                agent_id: Any = None, roles: list[str] | None = None) -> list[dict[str, Any]]:
        """Every version of a memory, oldest first."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        rows = self.service.history(ctx.actor, _uuid(id))
        self._visible(rows[-1], ctx)
        return [_clean(r) for r in rows]

    # --- agents and jobs --------------------------------------------------------------------------------------

    def middleware(self, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None, agent_id: Any = None,
                   roles: list[str] | None = None, workspace_from: Any = None, user_from: Any = None,
                   k: int = 5) -> MemoryMiddleware:
        """A LangChain `MemoryMiddleware` on this memory. The ids are the defaults; `workspace_from` / `user_from`
        (callables over `runtime.context`) override them per invocation."""
        # One middleware serves every user of the app: a `*_from` resolver stands in for its fixed id, and the run is
        # the agent's own (a middleware writes no run rows), so neither a fixed user nor a run_id is asked for here.
        ctx = self._call(workspace_id if workspace_from is None else (workspace_id or "-"),
                         user_id if user_from is None else (user_id or "-"), run_id or "middleware", agent_id, roles)
        return MemoryMiddleware(self.service, workspace_from=workspace_from or ctx.workspace_id,
                                user_from=user_from or ctx.user_id, k=k, actor=Actor(id=ctx.agent_id or "agent"))

    def tools(self, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None, agent_id: Any = None,
              roles: list[str] | None = None, k: int = 5) -> MemoryToolkit:
        """Framework-neutral `search_memory` / `propose_memory` tools for this workspace and user."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        return MemoryToolkit(self.service, workspace_id=ctx.workspace_id, user_id=ctx.user_id, k=k,
                             actor=Actor(id=ctx.agent_id or "agent"), run_id=ctx.run_id, agent_id=ctx.agent_id)

    def ingest(self, source: str | None = None, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
               agent_id: Any = None, roles: list[str] | None = None, base_dir: str | Path = ".", **kwargs: Any):
        """Run the scheduled ingest over a configured source (the only one when `source` is omitted)."""
        from memhub.pipeline.ingest import ingest_source
        from memhub.sources import build_source

        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        names = list(self.settings.sources)
        if source is None:
            if len(names) != 1:
                raise ValidationError(f"name the source to ingest; configured: {names}")
            source = names[0]
        if source not in self.settings.sources:
            raise ValidationError(f"unknown source {source!r}; configured: {names}")
        if self.llm is None:
            raise ValidationError("ingest needs an llm: pass Memory(llm=...)")
        try:
            adapter = build_source(self.settings.sources[source], workspace_id=ctx.workspace_id, base_dir=Path(base_dir))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return ingest_source(store=self.store, settings=self.settings, registry=self.registry, source=adapter,
                             source_name=source, extractor=self.llm, judge=self.judge, embeddings=self.embedder, **kwargs)


_SCOPED = ("add", "search", "get", "get_all", "update", "delete", "delete_all", "history", "queue", "approve",
           "reject", "promote", "middleware", "tools", "ingest")


class ScopedMemory:
    """`Memory` with the ids bound once: `m.scope(workspace_id=, user_id=, run_id=).add(...)`."""

    def __init__(self, memory: Memory, ctx: CallContext) -> None:
        self.memory, self.context = memory, ctx

    def __getattr__(self, name: str) -> Any:
        if name not in _SCOPED:
            raise AttributeError(name)
        c = self.context
        return functools.partial(getattr(self.memory, name), workspace_id=c.workspace_id, user_id=c.user_id,
                                 run_id=c.run_id, agent_id=c.agent_id, roles=c.roles)

    def __dir__(self) -> list[str]:
        return [*_SCOPED, "memory", "context"]
