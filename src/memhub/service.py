"""MemoryService: the public API used by the CLI and by MemoryMiddleware.

Everything here takes an `Actor` (id + roles) and enforces the review /
ownership rules on top of the plain CRUD that `store.py` provides. It is
also where content gets embedded and scanned for injection before it is
written. The ingest pipeline talks to `MemoryStore` directly instead (it
needs several writes in one transaction per segment), not through this
class.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from memhub import injection
from memhub.config import Settings
from memhub.store import Actor, MemoryStore, NotFound, Row, area_ids, as_vector_list
from memhub.types import TypeRegistry

__all__ = [
    "Actor",
    "MemoryService",
    "ServiceError",
    "PermissionDenied",
    "ValidationError",
    "InjectionDetected",
    "NotFound",
]


class ServiceError(Exception):
    """Base class for service-level errors."""


class PermissionDenied(ServiceError):
    pass


class ValidationError(ServiceError):
    pass


class InjectionDetected(ServiceError):
    pass


@dataclass
class Expansion:
    """A query after the glossary: the text to embed, the terms that expanded it, and their `related` words."""

    text: str
    terms: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


def _occurs(name: str, text: str) -> bool:
    return bool(name.strip()) and re.search(rf"(?<!\w){re.escape(name.strip())}(?!\w)", text, re.I) is not None


class Embedder:
    """Structural type: anything with `.embed_query(str) -> list[float]`."""

    def embed_query(self, text: str) -> list[float]:  # pragma: no cover - protocol
        raise NotImplementedError


@dataclass
class MemoryService:
    store: MemoryStore
    settings: Settings
    registry: TypeRegistry
    embeddings: Embedder

    # --- helpers -----------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        return list(self.embeddings.embed_query(text))

    def _check_scope(self, scope: str) -> None:
        if scope not in self.settings.scopes:
            raise ValidationError(f"scope {scope!r} is not enabled (config.scopes = {self.settings.scopes})")

    def _validate(self, type_name: str, fields: dict[str, Any]):
        if type_name not in self.registry:
            raise ValidationError(f"unknown memory type {type_name!r}")
        cls = self.registry.latest(type_name)
        try:
            instance = cls.model_validate(fields)
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        self._check_instance(instance)
        cfg = self.settings.types.get(type_name)
        if cfg is not None and cfg.keyed and cfg.strict_keys and instance.key not in cfg.keys:
            raise ValidationError(f"unknown key {instance.key!r} for {type_name} (allowed: {', '.join(cfg.keys)})")
        return instance

    def _check_instance(self, instance) -> None:
        """Injection scan over every string field (not only `content`), and configured entity types."""
        matched = injection.scan_all(instance.model_dump())
        if matched:
            raise InjectionDetected(f"a field matches an injection pattern ({matched!r})")
        for e in instance.entities:
            if e.type not in self.settings.entity_types:
                raise ValidationError(f"unknown entity type {e.type!r} (config.entity_types = {self.settings.entity_types})")

    def _authorize_owner_or_admin(self, actor: Actor, row: Row) -> None:
        if row["scope"] == "user":
            if row["user_id"] != actor.id and not actor.has_role("workspace_admin"):
                raise PermissionDenied("only the owner or a workspace_admin may change this memory")
        else:
            if not actor.has_role("workspace_admin"):
                raise PermissionDenied("only a workspace_admin may change a workspace memory")

    def _guard_embedding_config(self, cur) -> None:
        self.store.check_embedding_config(
            cur, model=self.settings.embeddings.model, dims=self.settings.embeddings.dims
        )

    # --- writes --------------------------------------------------------------

    def add(
        self,
        actor: Actor,
        *,
        type: str,
        scope: str,
        fields: dict[str, Any],
        workspace_id: str | None = None,
        user_id: str | None = None,
        reference: str | None = None,
        observed_at: datetime | None = None,
    ) -> Row:
        self._check_scope(scope)
        instance = self._validate(type, fields)
        workspace_id = workspace_id or self.settings.workspace_default
        if scope == "user" and not user_id:
            raise ValidationError("user_id is required for scope=user")
        observed_at = observed_at or datetime.now(timezone.utc)
        evidence = [{
            "source": "manual", "actor": actor.id, "observed_at": observed_at.isoformat(),
            **({"reference": reference} if reference else {}),
        }]
        embedding = self._embed(instance.content)
        status = "active" if scope == "user" or actor.has_role("workspace_admin") else "candidate"
        verified = status == "active"
        with self.store.connect() as conn, conn.cursor() as cur:
            self._guard_embedding_config(cur)
            if self.settings.is_slot(type, instance) and any(
                r["payload"].get("key") == instance.key and r["scope"] == scope and r["user_id"] == (user_id if scope == "user" else None)
                for r in self.store.list_memories(cur, status="active", type=type, user_id=user_id, workspace_id=workspace_id)
            ):
                raise ValidationError(f"an active {type} with key {instance.key!r} exists: edit it instead")
            cap = self.settings.types[type].max_active
            if cap is not None and status == "active" and self.store.count_active(
                cur, type=type, scope=scope, workspace_id=workspace_id, user_id=user_id
            ) >= cap:
                raise ValidationError(f"the {type} limit ({cap}) is reached: archive one first")
            return self.store.add_memory(
                cur,
                type=type,
                schema_version=self.registry.latest_version(type),
                scope=scope,
                workspace_id=workspace_id,
                user_id=user_id if scope == "user" else None,
                content=instance.content,
                payload=instance.model_dump(mode="json"),
                entities=[e.model_dump() for e in instance.entities],
                embedding=embedding,
                evidence=evidence,
                status=status,
                verified=verified,
                created_by=actor.id,
                observed_at=observed_at,
            )

    def edit(self, actor: Actor, memory_id: uuid.UUID, fields: dict[str, Any]) -> Row:
        with self.store.connect() as conn, conn.cursor() as cur:
            active = self.store.get_active(cur, memory_id)
            if active is None:
                raise NotFound(f"no active version for memory {memory_id}")
            self._authorize_owner_or_admin(actor, active)
            cls = self.registry.get(active["type"], active["schema_version"])
            merged = {**active["payload"], **fields}
            try:
                instance = cls.model_validate(merged)
            except PydanticValidationError as exc:
                raise ValidationError(str(exc)) from exc
            self._check_instance(instance)
            self._guard_embedding_config(cur)
            embedding = self._embed(instance.content)
            return self.store.edit_memory(
                cur,
                memory_id,
                content=instance.content,
                payload=instance.model_dump(mode="json"),
                entities=[e.model_dump() for e in instance.entities],
                embedding=embedding,
                verified=True,  # a person confirmed or edited it: the extractor never supersedes it
                created_by=actor.id,
                as_candidate=False,
            )

    def history(self, actor: Actor, memory_id: uuid.UUID) -> list[Row]:
        """Every version of a memory, oldest first: value, when it was said, evidence, who created it."""
        with self.store.connect() as conn, conn.cursor() as cur:
            rows = self.store.history(cur, memory_id)
            if not rows:
                raise NotFound(f"no memory {memory_id}")
            return rows

    def archive(self, actor: Actor, memory_id: uuid.UUID) -> Row:
        with self.store.connect() as conn, conn.cursor() as cur:
            active = self.store.get_active(cur, memory_id)
            if active is None:
                raise NotFound(f"no active version for memory {memory_id}")
            self._authorize_owner_or_admin(actor, active)
            return self.store.archive(cur, memory_id)

    def delete(self, actor: Actor, id: uuid.UUID) -> None:
        with self.store.connect() as conn, conn.cursor() as cur:
            row = self.store.get_by_id(cur, id)
            if row is None:
                raise NotFound(f"no row {id}")
            owns_user_row = row["scope"] == "user" and row["user_id"] == actor.id
            # a rejected row may be cleaned up by its owner (user scope) or by an admin, never by a bystander
            may_clean_rejected = row["status"] == "rejected" and (
                actor.has_role("workspace_admin") or owns_user_row
            )
            if not (may_clean_rejected or owns_user_row):
                raise PermissionDenied(
                    "delete is only allowed for rejected candidates (owner or admin), or your own user-scope rows"
                )
            self.store.delete_row(cur, id)

    def delete_user(self, actor: Actor, user_id: str) -> dict[str, int]:
        if user_id != actor.id and not actor.has_role("workspace_admin"):
            raise PermissionDenied("only the user themself or a workspace_admin may erase a user's data")
        with self.store.connect() as conn, conn.cursor() as cur:
            return self.store.delete_user(cur, user_id)

    def approve(self, actor: Actor, id: uuid.UUID, *, resolve: str | None = None, note: str | None = None) -> Row:
        if not any(actor.has_role(r) for r in self.settings.roles.approve_workspace):
            raise PermissionDenied("actor lacks an approving role")
        with self.store.connect() as conn, conn.cursor() as cur:
            merged = None
            row = self.store.get_by_id(cur, id)
            if resolve == "keep_both" and row is not None and row["conflicts_with"] is not None \
                    and row["payload"].get("key") is not None:
                old = self.store.get_active(cur, row["conflicts_with"])
                if old is not None:  # one slot, one row: keeping both values means one statement holding both
                    content = f"{old['content']}; {row['content']}"
                    merged = {"content": content, "payload": {**old["payload"], "content": content},
                              "embedding": self._embed(content)}
            return self.store.approve(cur, id, reviewed_by=actor.id, resolve=resolve, note=note, merged=merged)

    def reject(self, actor: Actor, id: uuid.UUID, *, note: str | None = None) -> Row:
        if not any(actor.has_role(r) for r in self.settings.roles.approve_workspace):
            raise PermissionDenied("actor lacks an approving role")
        with self.store.connect() as conn, conn.cursor() as cur:
            return self.store.reject(cur, id, reviewed_by=actor.id, note=note)

    def promote(self, actor: Actor, memory_id: uuid.UUID) -> Row:
        """Copy a user-scope memory into the workspace review queue as a new candidate."""
        with self.store.connect() as conn, conn.cursor() as cur:
            active = self.store.get_active(cur, memory_id)
            if active is None:
                raise NotFound(f"no active version for memory {memory_id}")
            if active["scope"] != "user":
                raise ValidationError("only a user-scope memory can be promoted")
            if active["user_id"] != actor.id and not actor.has_role("workspace_admin"):
                raise PermissionDenied("only the owner or a workspace_admin may promote this memory")
            return self.store.add_memory(
                cur,
                type=active["type"],
                schema_version=active["schema_version"],
                scope="workspace",
                workspace_id=active["workspace_id"],
                user_id=None,
                content=active["content"],
                payload=active["payload"],
                entities=active["entities"],
                embedding=as_vector_list(active["embedding"]),
                evidence=active["evidence"],
                status="candidate",
                verified=False,
                created_by=actor.id,
                observed_at=active["observed_at"],
                valid_from=active["valid_from"],
                valid_until=active["valid_until"],
                durability=active["durability"],
                assertion=active["assertion"],
                links=active["links"],
            )

    # --- reads -----------------------------------------------------------------

    def list(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        type: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        stale: bool = False,
        now: datetime | None = None,
        area: str | None = None,
    ) -> list[Row]:
        """Every row (stale ones marked); `stale=True` lists only the stale ones; `area` (key, title or id)
        keeps the rows linked to that area of the owner."""
        with self.store.connect() as conn, conn.cursor() as cur:
            rows = self.store.list_memories(
                cur, status=status, type=type, user_id=user_id, workspace_id=workspace_id, stale=stale, now=now
            )
            if area is None:
                return rows
            found = self._find_area(cur, area, workspace_id or self.settings.workspace_default, user_id)
            return [r for r in rows if found and str(found["memory_id"]) in area_ids(r["links"])]

    def search(
        self,
        actor: Actor,
        query: str,
        *,
        workspace_id: str | None = None,
        user_id: str | None = None,
        k: int = 10,
        type: str | None = None,
        include_stale: bool = False,
        now: datetime | None = None,
        area: str | None = None,
    ) -> list[Row]:
        """Nearest memories. Rows in the query's best area are boosted (never filtered); that area is `area`
        (a key, title or id) when given, else the owner's area whose description is nearest the query.
        Each row carries `areas`, the titles of the areas it is linked to."""
        workspace_id = workspace_id or self.settings.workspace_default
        with self.store.connect() as conn, conn.cursor() as cur:
            expansion = self._expand(cur, query, workspace_id, include_stale, now)
        embedding = self._embed(expansion.text)
        with self.store.connect() as conn, conn.cursor() as cur:
            self._guard_embedding_config(cur)
            best = self._find_area(cur, area, workspace_id, user_id) if area else self.store.nearest_area(
                cur, scope="user" if user_id else "workspace", workspace_id=workspace_id, user_id=user_id,
                query_embedding=embedding,
            )
            rows = self.store.search(
                cur, query_embedding=embedding, workspace_id=workspace_id, user_id=user_id, k=k, type=type,
                include_stale=include_stale, now=now, area_boost=best["memory_id"] if best else None,
                text_boost=expansion.related,
            )
            titles = self.store.areas_by_memory_id(cur, [i for r in rows for i in area_ids(r["links"])])
            for r in rows:
                r["areas"] = [titles[i]["payload"]["title"] for i in area_ids(r["links"]) if i in titles]
                if expansion.terms:  # which terms widened the query, so the effect can be seen
                    r["expanded_by"] = list(expansion.terms)
            return rows

    def expansion(
        self, actor: Actor, query: str, *, workspace_id: str | None = None, include_stale: bool = False,
        now: datetime | None = None,
    ) -> Expansion:
        """What `search` does to the query: active glossary terms found in it add their other aliases and expansion."""
        with self.store.connect() as conn, conn.cursor() as cur:
            return self._expand(cur, query, workspace_id or self.settings.workspace_default, include_stale, now)

    def _expand(self, cur, query: str, workspace_id: str, include_stale: bool, now: datetime | None) -> Expansion:
        """Only active terms count (a candidate expands nothing). `related` never adds text, it only boosts rank."""
        if "term" not in self.registry:
            return Expansion(query)
        added: list[str] = []
        result = Expansion(query)
        for row in reversed(self.store.list_memories(cur, status="active", type="term", workspace_id=workspace_id, now=now)):
            if row["scope"] != "workspace" or (row["stale"] and not include_stale):
                continue
            term = row["payload"]
            names = [term["term"], *term["aliases"]]
            if not any(_occurs(n, query) for n in names):
                continue
            result.terms.append(term["term"])
            result.related += [x for x in term["related"] if x not in result.related]
            for extra in [term.get("expansion"), *names]:
                if extra and not _occurs(extra, query) and extra.lower() not in {a.lower() for a in added}:
                    added.append(extra)
        result.text = f"{query} {' '.join(added)}" if added else query
        return result

    # --- areas ---------------------------------------------------------------------

    def _find_area(self, cur, ref: str, workspace_id: str, user_id: str | None) -> Row | None:
        """The owner's active area whose key, title or memory_id is `ref` (title compared ignoring case)."""
        wanted = ref.strip().lower()
        for row in self.store.owner_areas(
            cur, scope="user" if user_id else "workspace", workspace_id=workspace_id, user_id=user_id
        ):
            if wanted in (str(row["payload"].get("key") or "").lower(), row["payload"]["title"].lower(), str(row["memory_id"])):
                return row
        return None

    def areas(self, actor: Actor, *, user_id: str | None = None, workspace_id: str | None = None) -> list[dict]:
        """The active areas of a user (or of every owner in the workspace), each with its count of active linked rows."""
        with self.store.connect() as conn, conn.cursor() as cur:
            rows = self.store.list_memories(
                cur, status="active", type="area", user_id=user_id,
                workspace_id=workspace_id or self.settings.workspace_default,
            )
            return [
                {
                    "memory_id": r["memory_id"], "key": r["payload"].get("key"), "title": r["payload"]["title"],
                    "proposed": r["payload"].get("proposed", False), "user_id": r["user_id"],
                    "count": len(self.store.linked_rows(cur, r["memory_id"])),
                }
                for r in reversed(rows)
            ]

    def merge_areas(self, actor: Actor, source: uuid.UUID, target: uuid.UUID) -> dict[str, Any]:
        """Move every `in_area` link of `source` to `target` (new versions), then archive `source`."""
        if source == target:
            raise ValidationError("an area cannot be merged into itself")
        with self.store.connect() as conn, conn.cursor() as cur:
            rows = []
            for memory_id in (source, target):
                row = self.store.get_active(cur, memory_id)
                if row is None or row["type"] != "area":
                    raise NotFound(f"no active area {memory_id}")
                self._authorize_owner_or_admin(actor, row)
                rows.append(row)
            if any(rows[0][k] != rows[1][k] for k in ("scope", "user_id", "workspace_id")):
                raise ValidationError("areas can only be merged within one owner")
            moved = self.store.move_area_links(cur, source, target, created_by=actor.id)
            self.store.archive(cur, source)
            return {"moved": moved, "archived": source, "into": target}

    def page(
        self, owner: str | None, area: str, *, workspace_id: str | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """One area of `owner` (a user id; None = the workspace) read as a page: the title, the derived summary,
        the active non-stale rows linked to it (newest first, capped), and the newest `observed_at` among them."""
        with self.store.connect() as conn, conn.cursor() as cur:
            row = self._find_area(cur, area, workspace_id or self.settings.workspace_default, owner)
            if row is None:
                raise NotFound(f"no area {area!r}")
            return self._page(cur, row, now)

    def page_for_query(
        self, query: str, owner: str | None, *, workspace_id: str | None = None, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """The page of the owner's active area nearest the query, or None when no area is close enough
        (`areas.page_min_similarity`) or the area has no row to show."""
        embedding = self._embed(query)
        with self.store.connect() as conn, conn.cursor() as cur:
            best = self.store.nearest_area(
                cur, scope="user" if owner else "workspace",
                workspace_id=workspace_id or self.settings.workspace_default, user_id=owner, query_embedding=embedding,
            )
            if best is None or best["status"] != "active" or best["similarity"] < self.settings.areas.page_min_similarity:
                return None
            page = self._page(cur, best, now)
            return page if page["details"] else None

    def pages(self, owner: str | None, *, workspace_id: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
        """Every page of `owner` that has at least one row."""
        with self.store.connect() as conn, conn.cursor() as cur:
            rows = self.store.owner_areas(
                cur, scope="user" if owner else "workspace",
                workspace_id=workspace_id or self.settings.workspace_default, user_id=owner,
            )
            pages = [self._page(cur, r, now) for r in rows]
            return [pg for pg in pages if pg["details"]]

    def _page(self, cur, area: Row, now: datetime | None) -> dict[str, Any]:
        details = self.store.linked_rows(
            cur, area["memory_id"], stale_at=now or datetime.now(timezone.utc), limit=self.settings.areas.page_max_items
        )
        return {
            "memory_id": area["memory_id"],
            "version": area["version"],
            "title": area["payload"]["title"],
            "summary": area["payload"].get("summary", ""),
            "details": details,
            "last_updated": max((d["observed_at"] for d in details), default=None),
        }

    def queue(self, actor: Actor, *, workspace_id: str | None = None) -> list[Row]:
        """Candidates awaiting review (any scope), conflicts first."""
        with self.store.connect() as conn, conn.cursor() as cur:
            return self.store.queue(cur, workspace_id=workspace_id)

    def runs(self, actor: Actor, *, last: int = 10) -> list[Row]:
        with self.store.connect() as conn, conn.cursor() as cur:
            rows = self.store.list_run_summaries(cur, last=last)
            for row in rows:
                row["dropped_by_reason"] = self.store.dropped_by_reason(cur, row["run_id"])
            return rows

    # --- agent proposals -----------------------------------------------------------

    def propose(
        self,
        actor: Actor,
        *,
        type: str,
        scope: str,
        fields: dict[str, Any],
        evidence: list[dict[str, Any]],
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> Row:
        """Like `add`, but always a `candidate` (never active), even for user scope."""
        self._check_scope(scope)
        instance = self._validate(type, fields)
        if scope == "user" and not user_id:
            raise ValidationError("user_id is required for scope=user")
        embedding = self._embed(instance.content)
        with self.store.connect() as conn, conn.cursor() as cur:
            self._guard_embedding_config(cur)
            return self.store.add_memory(
                cur,
                type=type,
                schema_version=self.registry.latest_version(type),
                scope=scope,
                workspace_id=workspace_id or self.settings.workspace_default,
                user_id=user_id if scope == "user" else None,
                content=instance.content,
                payload=instance.model_dump(mode="json"),
                entities=[e.model_dump() for e in instance.entities],
                embedding=embedding,
                evidence=evidence,
                status="candidate",
                verified=False,
                created_by=actor.id,
            )

    # --- maintenance -------------------------------------------------------------

    def reembed(self, actor: Actor, *, batch_size: int = 100) -> int:
        """Recompute every row's embedding with the currently configured
        embedding model, resize the vector column if dims changed, and save the new
        embedding config. Requires workspace_admin."""
        if not actor.has_role("workspace_admin"):
            raise PermissionDenied("only a workspace_admin may reembed the ledger")
        count = 0
        with self.store.connect() as conn:
            with conn.cursor() as cur:
                saved = self.store.get_meta(cur, "embedding") or {}
                if saved.get("dims") != self.settings.embeddings.dims:
                    self.store.set_embedding_dims(cur, self.settings.embeddings.dims)
            with conn.cursor() as read_cur, conn.cursor() as write_cur:
                for batch in self.store.iter_for_reembed(read_cur, batch_size=batch_size):
                    for row in batch:
                        vector = self._embed(row["content"])
                        self.store.update_embedding(write_cur, row["id"], vector)
                        count += 1
            with conn.cursor() as cur:
                self.store.set_meta(
                    cur, "embedding", {"model": self.settings.embeddings.model, "dims": self.settings.embeddings.dims}
                )
        return count

    def purge_dropped(self, actor: Actor) -> int:
        if not actor.has_role("workspace_admin"):
            raise PermissionDenied("only a workspace_admin may purge dropped-candidate history")
        with self.store.connect() as conn, conn.cursor() as cur:
            return self.store.purge_dropped(cur, retention_days=self.settings.retention.dropped_days)
