"""`Memory`: memhub with the app's own database connection and models, and mem0-style methods.

    from memhub import Memory

    m = Memory(db=pool, llm=chat_model, embedder=embeddings)   # a pool, a connection, a callable or a URL
    m.init()                                                   # once: creates the tables (idempotent)
    me = m.scope(workspace_id="acme", user_id="anna", run_id=trace_id)
    me.add({"content": "Prefers answers in Portuguese"}, type="fact")
    me.search("language")

`Memory` is a facade over `MemoryService`: every rule (validation, injection scan, ownership, review) lives
there. It adds three things: the ids every call must carry (`CallContext`), the connection the app lends
(`memhub.connection`), and one result format for every write (`AddResult`, `MemoryEvent`). Embeddings are
never returned.
"""
from __future__ import annotations

import functools
import uuid
from pathlib import Path
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field

from memhub.config import Config, EmbeddingConfig
from memhub.connection import connection_source
from memhub.settings import Parts, Resolved, embedding_model_id, resolve
from memhub.middleware import MemoryMiddleware
from memhub.service import MemoryService, NotFound, ValidationError
from memhub.store import Actor, EmbeddingMismatch, MemoryStore, Row
from memhub.toolkit import MemoryToolkit

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


def _event(row: Row, event: Event) -> MemoryEvent:
    return MemoryEvent(
        id=str(row["memory_id"]), version_id=str(row["id"]), version=row["version"], event=event,
        status=row["status"], scope=row["scope"], type=row["type"], content=row["content"],
        conflicts_with=str(row["conflicts_with"]) if row.get("conflicts_with") else None,
    )


def _uuid(value: Any) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"no memory {value}") from exc


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
        self.source = connection_source(self.parts.db)
        self.embedder = self.parts.embedder
        self._embedding_model = embedding_model_id(self.embedder, self.settings)
        self.settings.embeddings = None  # known once one probe embed gives the dims; see `_ready`
        self.store = MemoryStore(self.source, self.settings.project_prefix, self.settings.database_schema)
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

    def _probe(self) -> EmbeddingConfig:
        if self.settings.embeddings is None:
            dims = len(self.embedder.embed_query("memhub"))
            self.settings.embeddings = EmbeddingConfig(model=str(self._embedding_model), dims=dims)
        return self.settings.embeddings

    def init(self) -> None:
        """Create the tables, or add what an older memhub lacks (idempotent). Stores the embedding model and the
        dims of one probe embed; a later `Memory` with another embedder is refused with `EmbeddingMismatch`."""
        emb = self._probe()
        self.store.init(embedding_model=emb.model, dims=emb.dims)
        self._checked = True

    def _ready(self) -> None:
        """Once per instance: the ledger exists and was built with this embedder."""
        if self._checked:
            return
        emb = self._probe()
        try:
            with self.store.connect() as conn, conn.cursor() as cur:
                self.store.check_embedding_config(cur, model=emb.model, dims=emb.dims)
        except psycopg.errors.UndefinedTable as exc:
            raise EmbeddingMismatch("the ledger has no tables yet: run `Memory.init()` (or `memhub init`) first") from exc
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
        with self.store.connect() as conn, conn.cursor() as cur:
            return self._visible(self.store.get_active(cur, _uuid(memory_id)), ctx)

    def scope(self, workspace_id: Any = None, user_id: Any = None, run_id: Any = None, agent_id: Any = None,
              roles: list[str] | None = None) -> "ScopedMemory":
        """A client with the ids bound: the same methods, no id arguments."""
        return ScopedMemory(self, self._ctx(workspace_id, user_id, run_id, agent_id, roles))

    # --- writes -----------------------------------------------------------------------------------------------

    def add(self, data: str | dict[str, Any], *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
            agent_id: Any = None, roles: list[str] | None = None, type: str | None = None, scope: str = "user",
            infer: bool = False) -> AddResult:
        """A memory from its fields (or a text, stored as a `fact`). User scope is active at once; a workspace
        memory waits for review (`PENDING`) unless the caller is a `workspace_admin`."""
        ctx = self._ctx(workspace_id, user_id, run_id, agent_id, roles)
        if infer:
            raise NotImplementedError("add(messages, infer=True) - push ingestion - is not available yet; pass fields")
        self._ready()
        fields = {"content": data} if isinstance(data, str) else dict(data)
        row = self.service.add(
            ctx.actor, type=type or "fact", scope=scope, fields=fields, workspace_id=ctx.workspace_id,
            user_id=ctx.user_id, run_id=ctx.run_id, agent_id=ctx.agent_id,
        )
        return AddResult(run_id=ctx.run_id, results=[_event(row, "ADD" if row["status"] == "active" else "PENDING")])

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
            with self.store.connect() as conn, conn.cursor() as cur:
                row = self._visible(self.store.get_by_id(cur, _uuid(id)), ctx)
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
        with self.store.connect() as conn, conn.cursor() as cur:
            row = self.store.get_by_id(cur, key)
            if row is None:
                pending = [r for r in self.store.history(cur, key) if r["status"] == "candidate"]
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
               types: list[str] | None = None) -> list[dict[str, Any]]:
        """Your active user memories and the workspace's, nearest first, stale ones left out."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        if not types:
            rows = self.service.search(ctx.actor, query, workspace_id=ctx.workspace_id, user_id=ctx.user_id, k=top_k)
        else:
            rows = [r for t in types for r in self.service.search(
                ctx.actor, query, workspace_id=ctx.workspace_id, user_id=ctx.user_id, k=top_k, type=t)]
            rows = sorted(rows, key=lambda r: r["similarity"], reverse=True)[:top_k]
        return [_clean(r) for r in rows]

    def get(self, id: Any, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None,
            agent_id: Any = None, roles: list[str] | None = None) -> dict[str, Any]:
        """The active version of a memory; `NotFound` when it is not yours to see."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        return _clean(self._active(id, ctx))

    def get_all(self, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None, agent_id: Any = None,
                roles: list[str] | None = None, status: str = "active", type: str | None = None) -> list[dict[str, Any]]:
        """Your user memories and the workspace's, with that status, newest first."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        rows = self.service.list(ctx.actor, status=status, type=type, workspace_id=ctx.workspace_id)
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
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        return MemoryMiddleware(self.service, workspace_from=workspace_from or ctx.workspace_id,
                                user_from=user_from or ctx.user_id, k=k, actor=Actor(id=ctx.agent_id or "agent"))

    def tools(self, *, workspace_id: Any = None, user_id: Any = None, run_id: Any = None, agent_id: Any = None,
              roles: list[str] | None = None, k: int = 5) -> MemoryToolkit:
        """Framework-neutral `search_memory` / `propose_memory` tools for this workspace and user."""
        ctx = self._call(workspace_id, user_id, run_id, agent_id, roles)
        return MemoryToolkit(self.service, workspace_id=ctx.workspace_id, user_id=ctx.user_id, k=k,
                             actor=Actor(id=ctx.agent_id or "agent"))

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
