"""Resolve a candidate's `areas` to area rows.

`plan_areas` only reads: it says which area rows the candidate points at, which ones would have to be created
(a seed the owner has no row for yet, or a proposed new area), and whether the cap drops the candidate.
`create_areas` writes the missing rows once the candidate is kept, so a dropped candidate leaves no area behind."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from memhub.config import Settings
from memhub.pipeline.ground import Proposal
from memhub.pipeline.segment import Segment
from memhub.store import MemoryStore, Row, area_link
from memhub.types import Area, TypeRegistry

MAX_AREAS = 3


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "area"


@dataclass
class PendingArea:
    key: str
    title: str
    description: str
    proposed: bool
    embedding: list[float] | None = None


@dataclass
class AreaPlan:
    rows: list[Row] = field(default_factory=list)  # area rows that exist already
    pending: list[PendingArea] = field(default_factory=list)  # rows to create
    merged: int = 0  # proposed areas that merged into an existing one
    dropped: str | None = None  # `no_area` | `area_cap`

    @property
    def area_ids(self) -> set[str]:
        return {str(r["memory_id"]) for r in self.rows}


def area_owner(p: Proposal, segment: Segment) -> dict[str, Any]:
    """The owner of the candidate's areas: the user for a user-scope candidate, else the workspace."""
    return dict(
        scope=p.scope, workspace_id=segment.workspace_id, user_id=segment.user_id if p.scope == "user" else None
    )


def owner_area_list(*, cur, store: MemoryStore, settings: Settings, workspace_id: str, user_id: str) -> list[tuple[str, str, str]]:
    """(key, title, description) offered to the extractor: the seeds, then the owner's other areas."""
    listed = {a.key: (a.key, a.title, a.description) for a in settings.areas.seeds}
    for row in store.owner_areas(cur, scope="user", workspace_id=workspace_id, user_id=user_id):
        key = row["payload"].get("key")
        if key and key not in listed:
            listed[key] = (key, row["payload"]["title"], row["payload"].get("description", ""))
    return list(listed.values())


def plan_areas(p: Proposal, *, cur, store: MemoryStore, settings: Settings, segment: Segment, embeddings: Any) -> AreaPlan:
    owner = area_owner(p, segment)
    owned = store.owner_areas(cur, **owner)
    by_key = {r["payload"].get("key"): r for r in owned if r["payload"].get("key")}
    by_title = {r["payload"]["title"].strip().lower(): r for r in owned}
    seeds = {a.key: a for a in settings.areas.seeds}
    seeds_by_title = {a.title.strip().lower(): a for a in settings.areas.seeds}
    plan, seen = AreaPlan(), set()

    def use_row(row: Row) -> None:
        if row["memory_id"] not in seen:
            seen.add(row["memory_id"])
            plan.rows.append(row)

    def use_seed(key: str) -> None:
        if key in by_key:
            use_row(by_key[key])
        elif key not in seen:
            seen.add(key)
            seed = seeds[key]
            plan.pending.append(PendingArea(seed.key, seed.title, seed.description, proposed=False))

    for ref in [r for r in p.candidate.areas if r.existing or r.new][:MAX_AREAS]:
        if ref.existing:
            key = ref.existing.strip()
            if key in by_key or key in seeds:
                use_seed(key)
            elif key.lower() in by_title:  # the model wrote the title instead of the key
                use_row(by_title[key.lower()])
            elif key.lower() in seeds_by_title:
                use_seed(seeds_by_title[key.lower()].key)
            continue  # an unknown key is ignored; a required type with nothing left is `no_area`
        if not settings.areas.open:
            continue
        title, description = ref.new.title.strip(), ref.new.description.strip()
        key = slugify(title)
        if key in by_key or key in seeds:
            use_seed(key)
        elif title.lower() in by_title:
            use_row(by_title[title.lower()])
        elif title.lower() in seeds_by_title:
            use_seed(seeds_by_title[title.lower()].key)
        else:
            embedding = list(embeddings.embed_query(f"{title}: {description}"))
            near = store.nearest_area(cur, query_embedding=embedding, **owner)
            if near is not None and near["similarity"] is not None and near["similarity"] >= settings.areas.merge_similarity:
                use_row(near)
                plan.merged += 1
            elif key not in seen:
                seen.add(key)
                plan.pending.append(PendingArea(key, title, description, proposed=True, embedding=embedding))

    if not plan.rows and not plan.pending:
        plan.dropped = "no_area"
    elif any(a.proposed for a in plan.pending):
        cap = settings.areas.max_per_user if p.scope == "user" else settings.areas.max_per_workspace
        if len(owned) + len(plan.pending) > cap:
            plan.dropped = "area_cap"
    return plan


def create_areas(
    plan: AreaPlan, p: Proposal, *, cur, store: MemoryStore, registry: TypeRegistry, segment: Segment, embeddings: Any,
) -> tuple[list[dict], int]:
    """Write the plan's missing area rows. Returns the `in_area` links for the candidate, and how many rows were created."""
    owner = area_owner(p, segment)
    ids = [r["memory_id"] for r in plan.rows]
    for a in plan.pending:
        embedding = a.embedding or list(embeddings.embed_query(f"{a.title}: {a.description}"))
        payload = Area(
            content=f"{a.title}: {a.description}", title=a.title, description=a.description, proposed=a.proposed, key=a.key,
        ).model_dump(mode="json")
        row = store.add_memory(
            cur, type="area", schema_version=registry.latest_version("area"), entities=[], embedding=embedding,
            content=payload["content"], payload=payload, evidence=[{"source": "areas", "reason": "proposed" if a.proposed else "seed"}],
            status="active", verified=False, created_by="extractor" if a.proposed else "seed", **owner,
        )
        ids.append(row["memory_id"])
    return [area_link(i) for i in ids], len(plan.pending)
