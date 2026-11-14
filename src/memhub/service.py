"""MemoryService: the public API used by the CLI and by MemoryMiddleware.

Everything here takes an `Actor` (id + roles) and enforces the review /
ownership rules on top of the plain CRUD that a storage `Backend` provides
(`memhub.storage`: Postgres or in-memory), one `UnitOfWork` per call. It is
also where content gets embedded and scanned for injection before it is
written. The ingest pipeline opens its own unit of work per segment (it
needs several writes in one transaction), not through this class.
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
from memhub.storage import Backend, UnitOfWork
from memhub.storage.rules import Actor, EntityFilter, NotFound, Row, area_ids, as_vector_list
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
    store: Backend
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
            if e.type not in self.settings.declared_entity_types():
                raise ValidationError(f"unknown entity type {e.type!r} (declared: {self.settings.declared_entity_types()})")

    def _authorize_owner_or_admin(self, actor: Actor, row: Row) -> None:
        if row["scope"] == "user":
            if row["user_id"] != actor.id and not actor.has_role("workspace_admin"):
                raise PermissionDenied("only the owner or a workspace_admin may change this memory")
        else:
            if not actor.has_role("workspace_admin"):
                raise PermissionDenied("only a workspace_admin may change a workspace memory")

    def _guard_embedding_config(self, uow: UnitOfWork) -> None:
        uow.meta.check_embedding_config(
            model=self.settings.embeddings.model, dims=self.settings.embeddings.dims
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
        run_id: str | None = None,
        agent_id: str | None = None,
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
        with self.store.unit_of_work() as uow:
            self._guard_embedding_config(uow)
            if self.settings.is_slot(type, instance) and any(
                r["payload"].get("key") == instance.key and r["scope"] == scope and r["user_id"] == (user_id if scope == "user" else None)
                for r in uow.memories.list_memories(status="active", type=type, user_id=user_id, workspace_id=workspace_id)
            ):
                raise ValidationError(f"an active {type} with key {instance.key!r} exists: edit it instead")
            cap = self.settings.types[type].max_active
            if cap is not None and status == "active" and uow.memories.count_active(
                type=type, scope=scope, workspace_id=workspace_id, user_id=user_id
            ) >= cap:
                raise ValidationError(f"the {type} limit ({cap}) is reached: archive one first")
            return uow.memories.add_memory(
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
                run_id=run_id,
                agent_id=agent_id,
            )

    def edit(
        self, actor: Actor, memory_id: uuid.UUID, fields: dict[str, Any], *,
        run_id: str | None = None, agent_id: str | None = None,
    ) -> Row:
        with self.store.unit_of_work() as uow:
            active = uow.memories.get_active(memory_id)
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
            self._guard_embedding_config(uow)
            embedding = self._embed(instance.content)
            return uow.memories.edit_memory(
                memory_id,
                content=instance.content,
                payload=instance.model_dump(mode="json"),
                entities=[e.model_dump() for e in instance.entities],
                embedding=embedding,
                verified=True,  # a person confirmed or edited it: the extractor never supersedes it
                created_by=actor.id,
                as_candidate=False,
                run_id=run_id,
                agent_id=agent_id,
            )

    def history(self, actor: Actor, memory_id: uuid.UUID) -> list[Row]:
        """Every version of a memory, oldest first: value, when it was said, evidence, who created it."""
        with self.store.unit_of_work() as uow:
            rows = uow.memories.history(memory_id)
            if not rows:
                raise NotFound(f"no memory {memory_id}")
            return rows

    def archive(self, actor: Actor, memory_id: uuid.UUID) -> Row:
        with self.store.unit_of_work() as uow:
            active = uow.memories.get_active(memory_id)
            if active is None:
                raise NotFound(f"no active version for memory {memory_id}")
            self._authorize_owner_or_admin(actor, active)
            return uow.memories.archive(memory_id)

    def delete(self, actor: Actor, id: uuid.UUID) -> None:
        with self.store.unit_of_work() as uow:
            row = uow.memories.get_by_id(id)
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
            uow.memories.delete_row(id)

    def delete_user(self, actor: Actor, user_id: str) -> dict[str, int]:
        if user_id != actor.id and not actor.has_role("workspace_admin"):
            raise PermissionDenied("only the user themself or a workspace_admin may erase a user's data")
        with self.store.unit_of_work() as uow:
            return uow.memories.delete_user(user_id)

    def approve(self, actor: Actor, id: uuid.UUID, *, resolve: str | None = None, note: str | None = None) -> Row:
        if not any(actor.has_role(r) for r in self.settings.roles.approve_workspace):
            raise PermissionDenied("actor lacks an approving role")
        with self.store.unit_of_work() as uow:
            merged = None
            row = uow.memories.get_by_id(id)
            if resolve == "keep_both" and row is not None and row["conflicts_with"] is not None \
                    and row["payload"].get("key") is not None:
                old = uow.memories.get_active(row["conflicts_with"])
                if old is not None:  # one slot, one row: keeping both values means one statement holding both
                    content = f"{old['content']}; {row['content']}"
                    merged = {"content": content, "payload": {**old["payload"], "content": content},
                              "embedding": self._embed(content)}
            return uow.memories.approve(id, reviewed_by=actor.id, resolve=resolve, note=note, merged=merged)

    def reject(self, actor: Actor, id: uuid.UUID, *, note: str | None = None) -> Row:
        if not any(actor.has_role(r) for r in self.settings.roles.approve_workspace):
            raise PermissionDenied("actor lacks an approving role")
        with self.store.unit_of_work() as uow:
            return uow.memories.reject(id, reviewed_by=actor.id, note=note)

    def promote(
        self, actor: Actor, memory_id: uuid.UUID, *, run_id: str | None = None, agent_id: str | None = None,
    ) -> Row:
        """Copy a user-scope memory into the workspace review queue as a new candidate."""
        with self.store.unit_of_work() as uow:
            active = uow.memories.get_active(memory_id)
            if active is None:
                raise NotFound(f"no active version for memory {memory_id}")
            if active["scope"] != "user":
                raise ValidationError("only a user-scope memory can be promoted")
            if active["user_id"] != actor.id and not actor.has_role("workspace_admin"):
                raise PermissionDenied("only the owner or a workspace_admin may promote this memory")
            return uow.memories.add_memory(
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
                run_id=run_id,
                agent_id=agent_id,
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
        subjects: list[dict] | None = None,
        entity_type: str | None = None,
    ) -> list[Row]:
        """Every row (stale ones marked); `stale=True` lists only the stale ones; `area` (key, title or id)
        keeps the rows linked to that area of the owner; `subjects` / `entity_type` keep the rows about one of
        those subjects / about any subject of that type."""
        with self.store.unit_of_work() as uow:
            rows = uow.memories.list_memories(
                status=status, type=type, user_id=user_id, workspace_id=workspace_id, stale=stale, now=now
            )
            if subjects or entity_type:
                flt = EntityFilter(any_of=tuple((e["type"], e["id"]) for e in subjects or []), of_type=entity_type)
                rows = [r for r in rows if flt.matches(r["entities"])]
            if area is None:
                return rows
            found = self._find_area(uow, area, workspace_id or self.settings.workspace_default, user_id)
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
        subjects: list[dict] | None = None,
        entity_type: str | None = None,
        widen: bool = False,
    ) -> list[Row]:
        """Nearest memories. Rows in the query's best area are boosted (never filtered); that area is `area`
        (a key, title or id) when given, else the owner's area whose description is nearest the query.
        Each row carries `areas`, the titles of the areas it is linked to.

        `subjects`: only rows about one of them, in tiers by the order given (AD-030), then, with `widen`, rows
        about no isolated subject (`general`); `entity_type`: rows about any subject of that type. With either,
        each row carries `matched_on`. A `require_filter` type the filter does not name is left out (AD-033)."""
        workspace_id = workspace_id or self.settings.workspace_default
        with self.store.unit_of_work() as uow:
            expansion = self._expand(uow, query, workspace_id, include_stale, now)
        embedding = self._embed(expansion.text)
        with self.store.unit_of_work() as uow:
            self._guard_embedding_config(uow)
            best = self._find_area(uow, area, workspace_id, user_id) if area else uow.vectors.nearest_area(
                scope="user" if user_id else "workspace", workspace_id=workspace_id, user_id=user_id,
                query_embedding=embedding,
            )

            def nearest(entities: EntityFilter | None) -> list[Row]:
                return uow.vectors.search(
                    query_embedding=embedding, workspace_id=workspace_id, user_id=user_id, k=k, type=type,
                    include_stale=include_stale, now=now, area_boost=best["memory_id"] if best else None,
                    text_boost=expansion.related, **({"entities": entities} if entities is not None else {}),
                )

            rows = self._subject_tiers(nearest, subjects or [], entity_type, widen, k)
            titles = uow.memories.areas_by_memory_id([i for r in rows for i in area_ids(r["links"])])
            for r in rows:
                r["areas"] = [titles[i]["payload"]["title"] for i in area_ids(r["links"]) if i in titles]
                if expansion.terms:  # which terms widened the query, so the effect can be seen
                    r["expanded_by"] = list(expansion.terms)
            return rows

    def _subject_tiers(self, nearest, subjects: list[dict], entity_type: str | None, widen: bool, k: int) -> list[Row]:
        """One search per tier (a subject, in order; then `general`), each its own top `k`, so a subject's rows
        are never cut by another's; a row is kept in the first tier it matches."""
        named = {e["type"] for e in subjects} | ({entity_type} if entity_type else set())
        guard = tuple(t for t in self.settings.guarded_types() if t not in named)
        if not subjects and entity_type is None:
            return nearest(EntityFilter(none_of_types=guard) if guard else None)
        if not subjects:
            rows = nearest(EntityFilter(of_type=entity_type, none_of_types=guard))
            for r in rows:
                r["matched_on"] = next(f"{e['type']}:{e['id']}" for e in r["entities"] if e["type"] == entity_type)
            return rows
        tiers = [(f"{e['type']}:{e['id']}", EntityFilter(any_of=((e["type"], e["id"]),), none_of_types=guard))
                 for e in subjects]
        if widen:
            isolated = tuple(dict.fromkeys([*self.settings.isolated_types(), *guard]))
            tiers.append(("general", EntityFilter(none_of_types=isolated)))
        out: list[Row] = []
        seen: set[Any] = set()
        for label, flt in tiers:
            for r in nearest(flt):
                if r["memory_id"] not in seen:
                    seen.add(r["memory_id"])
                    r["matched_on"] = label
                    out.append(r)
        return out[:k]

    def expansion(
        self, actor: Actor, query: str, *, workspace_id: str | None = None, include_stale: bool = False,
        now: datetime | None = None,
    ) -> Expansion:
        """What `search` does to the query: active glossary terms found in it add their other aliases and expansion."""
        with self.store.unit_of_work() as uow:
            return self._expand(uow, query, workspace_id or self.settings.workspace_default, include_stale, now)

    def _expand(self, uow: UnitOfWork, query: str, workspace_id: str, include_stale: bool, now: datetime | None) -> Expansion:
        """Only active terms count (a candidate expands nothing). `related` never adds text, it only boosts rank."""
        if "term" not in self.registry:
            return Expansion(query)
        added: list[str] = []
        result = Expansion(query)
        for row in reversed(uow.memories.list_memories(status="active", type="term", workspace_id=workspace_id, now=now)):
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

    def _find_area(self, uow: UnitOfWork, ref: str, workspace_id: str, user_id: str | None) -> Row | None:
        """The owner's active area whose key, title or memory_id is `ref` (title compared ignoring case)."""
        wanted = ref.strip().lower()
        for row in uow.memories.owner_areas(
            scope="user" if user_id else "workspace", workspace_id=workspace_id, user_id=user_id
        ):
            if wanted in (str(row["payload"].get("key") or "").lower(), row["payload"]["title"].lower(), str(row["memory_id"])):
                return row
        return None

    def areas(self, actor: Actor, *, user_id: str | None = None, workspace_id: str | None = None) -> list[dict]:
        """The active areas of a user (or of every owner in the workspace), each with its count of active linked rows."""
        with self.store.unit_of_work() as uow:
            rows = uow.memories.list_memories(
                status="active", type="area", user_id=user_id,
                workspace_id=workspace_id or self.settings.workspace_default,
            )
            return [
                {
                    "memory_id": r["memory_id"], "key": r["payload"].get("key"), "title": r["payload"]["title"],
                    "proposed": r["payload"].get("proposed", False), "user_id": r["user_id"],
                    "count": len(uow.memories.linked_rows(r["memory_id"])),
                }
                for r in reversed(rows)
            ]

    def merge_areas(self, actor: Actor, source: uuid.UUID, target: uuid.UUID) -> dict[str, Any]:
        """Move every `in_area` link of `source` to `target` (new versions), then archive `source`."""
        if source == target:
            raise ValidationError("an area cannot be merged into itself")
        with self.store.unit_of_work() as uow:
            rows = []
            for memory_id in (source, target):
                row = uow.memories.get_active(memory_id)
                if row is None or row["type"] != "area":
                    raise NotFound(f"no active area {memory_id}")
                self._authorize_owner_or_admin(actor, row)
                rows.append(row)
            if any(rows[0][k] != rows[1][k] for k in ("scope", "user_id", "workspace_id")):
                raise ValidationError("areas can only be merged within one owner")
            moved = uow.memories.move_area_links(source, target, created_by=actor.id)
            uow.memories.archive(source)
            return {"moved": moved, "archived": source, "into": target}

    def page(
        self, owner: str | None, area: str, *, workspace_id: str | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """One area of `owner` (a user id; None = the workspace) read as a page: the title, the derived summary,
        the active non-stale rows linked to it (newest first, capped), and the newest `observed_at` among them."""
        with self.store.unit_of_work() as uow:
            row = self._find_area(uow, area, workspace_id or self.settings.workspace_default, owner)
            if row is None:
                raise NotFound(f"no area {area!r}")
            return self._page(uow, row, now)

    def page_for_query(
        self, query: str, owner: str | None, *, workspace_id: str | None = None, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """The page of the owner's active area nearest the query, or None when no area is close enough
        (`areas.page_min_similarity`) or the area has no row to show."""
        embedding = self._embed(query)
        with self.store.unit_of_work() as uow:
            best = uow.vectors.nearest_area(
                scope="user" if owner else "workspace",
                workspace_id=workspace_id or self.settings.workspace_default, user_id=owner, query_embedding=embedding,
            )
            if best is None or best["status"] != "active" or best["similarity"] < self.settings.areas.page_min_similarity:
                return None
            page = self._page(uow, best, now)
            return page if page["details"] else None

    def pages(self, owner: str | None, *, workspace_id: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
        """Every page of `owner` that has at least one row."""
        with self.store.unit_of_work() as uow:
            rows = uow.memories.owner_areas(
                scope="user" if owner else "workspace",
                workspace_id=workspace_id or self.settings.workspace_default, user_id=owner,
            )
            pages = [self._page(uow, r, now) for r in rows]
            return [pg for pg in pages if pg["details"]]

    def _page(self, uow: UnitOfWork, area: Row, now: datetime | None) -> dict[str, Any]:
        details = uow.memories.linked_rows(
            area["memory_id"], stale_at=now or datetime.now(timezone.utc), limit=self.settings.areas.page_max_items
        )
        return {
            "memory_id": area["memory_id"],
            "version": area["version"],
            "title": area["payload"]["title"],
            "summary": area["payload"].get("summary", ""),
            "details": details,
            "last_updated": max((d["observed_at"] for d in details), default=None),
        }

    def queue(self, actor: Actor, *, workspace_id: str | None = None, user_id: str | None = None) -> list[Row]:
        """Candidates awaiting review (any scope), conflicts first; with `user_id`, only that user's own candidates."""
        with self.store.unit_of_work() as uow:
            rows = uow.memories.queue(workspace_id=workspace_id)
        return rows if user_id is None else [r for r in rows if r["scope"] == "user" and r["user_id"] == user_id]

    def runs(self, actor: Actor, *, last: int = 10) -> list[Row]:
        with self.store.unit_of_work() as uow:
            rows = uow.runs.list_run_summaries(last=last)
            for row in rows:
                row["dropped_by_reason"] = uow.runs.dropped_by_reason(row["run_id"])
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
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> Row:
        """Like `add`, but always a `candidate` (never active), even for user scope."""
        self._check_scope(scope)
        instance = self._validate(type, fields)
        if scope == "user" and not user_id:
            raise ValidationError("user_id is required for scope=user")
        embedding = self._embed(instance.content)
        with self.store.unit_of_work() as uow:
            self._guard_embedding_config(uow)
            return uow.memories.add_memory(
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
                run_id=run_id,
                agent_id=agent_id,
            )

    # --- maintenance -------------------------------------------------------------

    def reembed(self, actor: Actor, *, batch_size: int = 100) -> int:
        """Recompute every row's embedding with the currently configured
        embedding model, resize the vector column if dims changed, and save the new
        embedding config. Requires workspace_admin."""
        if not actor.has_role("workspace_admin"):
            raise PermissionDenied("only a workspace_admin may reembed the ledger")
        count = 0
        with self.store.unit_of_work() as uow:
            saved = uow.meta.get_meta("embedding") or {}
            if saved.get("dims") != self.settings.embeddings.dims:
                uow.vectors.set_embedding_dims(self.settings.embeddings.dims)
            for batch in uow.vectors.iter_for_reembed(batch_size=batch_size):
                for row in batch:
                    vector = self._embed(row["content"])
                    uow.vectors.update_embedding(row["id"], vector)
                    count += 1
            uow.meta.set_meta(
                "embedding", {"model": self.settings.embeddings.model, "dims": self.settings.embeddings.dims}
            )
        return count

    def purge_dropped(self, actor: Actor) -> int:
        if not actor.has_role("workspace_admin"):
            raise PermissionDenied("only a workspace_admin may purge dropped-candidate history")
        with self.store.unit_of_work() as uow:
            return uow.runs.purge_dropped(retention_days=self.settings.retention.dropped_days)
