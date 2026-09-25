"""Write a decided proposal to the ledger, with status chosen by scope."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, NamedTuple

from memhub.config import Settings
from memhub.pipeline.ground import Proposal
from memhub.pipeline.reconcile import Decision
from memhub.pipeline.segment import Segment
from memhub.store import MemoryStore, newest_observed_at
from memhub.types import TypeRegistry


def evidence_entries(p: Proposal, segment: Segment, source_name: str) -> list[dict]:
    """Quotes only, never transcripts."""
    by_id = {m.message_id: m for m in segment.messages}
    return [
        {
            "source": source_name,
            "trace_id": by_id[e.message_id].trace_id if e.message_id in by_id else None,
            "thread_id": segment.thread_id,
            "message_id": e.message_id,
            "observed_at": by_id[e.message_id].timestamp.isoformat() if e.message_id in by_id else None,
            "quote": e.quote,
            "claim_source": e.claim_source,
        }
        for e in p.candidate.evidence
    ]


class Routed(NamedTuple):
    """What `route` did: the outcome, and the memory it wrote or merged into (`id` is that row's id)."""

    outcome: Literal["created", "merged", "superseded", "conflict"]
    memory_id: uuid.UUID
    id: uuid.UUID

def route(
    p: Proposal, decision: Decision, *, cur, store: MemoryStore, registry: TypeRegistry, segment: Segment,
    source_name: str, settings: Settings, link: dict | None = None, links: list[dict] | None = None,
) -> Routed:
    """Apply the decision. `link` and `links` (each a `{kind, memory_id}`) are added to the memory written or
    merged into, unless already there or pointing the memory at itself."""
    evidence = evidence_entries(p, p.context or segment, source_name)
    observed_at = newest_observed_at(evidence) or datetime.now(timezone.utc)
    c = p.candidate
    valid_until = c.valid_until or settings.default_valid_until(c.type, c.durability, observed_at)
    target = decision.target
    links = [l for l in [link, *(links or [])] if l and not (target and str(target["memory_id"]) == l["memory_id"])]
    if decision.action == "merge":
        store.merge_evidence_row(
            cur, target["id"], new_evidence=evidence, thread_id=segment.thread_id, valid_until=valid_until,
        )
        for l in links:
            store.add_link(cur, target["id"], l)
        return Routed("merged", target["memory_id"], target["id"])

    content = p.memory.content
    payload = p.memory.model_dump(mode="json")
    entities = [e.model_dump() for e in p.memory.entities]
    if decision.action == "amend":  # a pending candidate takes the merged payload; nothing new is inserted
        store.amend_row(cur, target["id"], content=p.memory.content, payload=p.memory.model_dump(mode="json"), embedding=p.embedding)
        store.merge_evidence_row(cur, target["id"], new_evidence=evidence, thread_id=segment.thread_id)
        return Routed("merged", target["memory_id"], target["id"])
    if decision.action == "supersede":
        new = store.edit_memory(
            cur, target["memory_id"], content=content, payload=payload, entities=entities,
            embedding=p.embedding, verified=False, created_by="remember" if p.explicit else "extractor", as_candidate=p.scope != "user" and settings.types[c.type].review,
            # the new version is its own claim: time fields come from the candidate, not the version it replaces
            times=dict(observed_at=observed_at, valid_from=c.valid_from, valid_until=valid_until,
                       durability=c.durability, assertion=c.assertion),
        )  # it keeps the evidence of the version it replaces or extends, and gains this candidate's
        store.merge_evidence_row(cur, new["id"], new_evidence=evidence, thread_id=segment.thread_id)
        for l in links:
            store.add_link(cur, new["id"], l)
        return Routed("superseded", new["memory_id"], new["id"])

    conflict = decision.action == "conflict"
    active = (p.scope == "user" or not settings.types[c.type].review) and not conflict
    row = store.add_memory(
        cur, type=p.candidate.type, schema_version=registry.latest_version(p.candidate.type), scope=p.scope,
        workspace_id=segment.workspace_id, user_id=segment.user_id if p.scope == "user" else None,
        content=content, payload=payload, entities=entities, embedding=p.embedding, evidence=evidence,
        status="active" if active else "candidate", verified=False, created_by="remember" if p.explicit else "extractor", score=p.score,
        conflicts_with=target["memory_id"] if conflict else None, observed_at=observed_at,
        valid_from=c.valid_from, valid_until=valid_until, durability=c.durability,
        assertion=c.assertion, links=links,
    )
    return Routed("conflict" if conflict else "created", row["memory_id"], row["id"])
