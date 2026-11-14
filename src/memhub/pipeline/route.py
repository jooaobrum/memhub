"""Write a decided proposal to the ledger, with status chosen by the type's review rule (`Config.needs_review`)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, NamedTuple

from memhub.config import Settings
from memhub.pipeline.ground import Proposal
from memhub.pipeline.reconcile import Decision
from memhub.pipeline.segment import Segment
from memhub.storage import UnitOfWork
from memhub.storage.rules import newest_observed_at
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


AUTO = "auto"  # `reviewed_by` of a row let in with no person (AD-055)


class Routed(NamedTuple):
    """What `route` did: the outcome, and the memory it wrote or merged into (`id` is that row's id)."""

    outcome: Literal["created", "merged", "superseded", "conflict"]
    memory_id: uuid.UUID
    id: uuid.UUID

def route(
    p: Proposal, decision: Decision, *, uow: UnitOfWork, registry: TypeRegistry, segment: Segment,
    source_name: str, settings: Settings, link: dict | None = None, links: list[dict] | None = None,
    run_id: str | None = None, agent_id: str | None = None,
) -> Routed:
    """Apply the decision. `link` and `links` (each a `{kind, memory_id}`) are added to the memory written or
    merged into, unless already there or pointing the memory at itself. `run_id` / `agent_id` (the app's call)
    are stamped on a row this writes; a merge writes none."""
    evidence = evidence_entries(p, p.context or segment, source_name)
    observed_at = newest_observed_at(evidence) or datetime.now(timezone.utc)
    c = p.candidate
    valid_until = c.valid_until or settings.default_valid_until(c.type, c.durability, observed_at)
    target = decision.target
    links = [l for l in [link, *(links or [])] if l and not (target and str(target["memory_id"]) == l["memory_id"])]
    waits = settings.needs_review(c.type, p.scope)
    if decision.action == "merge":
        uow.memories.merge_evidence_row(
            target["id"], new_evidence=evidence, thread_id=segment.thread_id, valid_until=valid_until,
        )
        for l in links:
            uow.memories.add_link(target["id"], l)
        return Routed("merged", target["memory_id"], target["id"])

    content = p.memory.content
    payload = p.memory.model_dump(mode="json")
    entities = [e.model_dump() for e in p.memory.entities]
    if decision.action == "amend":  # a pending candidate takes the merged payload; nothing new is inserted
        uow.memories.amend_row(target["id"], content=p.memory.content, payload=p.memory.model_dump(mode="json"), embedding=p.embedding)
        uow.memories.merge_evidence_row(target["id"], new_evidence=evidence, thread_id=segment.thread_id)
        return Routed("merged", target["memory_id"], target["id"])
    if decision.action == "supersede":
        new = uow.memories.edit_memory(
            target["memory_id"], content=content, payload=payload, entities=entities,
            embedding=p.embedding, verified=False, created_by="remember" if p.explicit else "extractor", as_candidate=waits,
            reviewed_by=None if waits else AUTO,
            # the new version is its own claim: time fields come from the candidate, not the version it replaces
            times=dict(observed_at=observed_at, valid_from=c.valid_from, valid_until=valid_until,
                       durability=c.durability, assertion=c.assertion),
            run_id=run_id, agent_id=agent_id,
        )  # it keeps the evidence of the version it replaces or extends, and gains this candidate's
        uow.memories.merge_evidence_row(new["id"], new_evidence=evidence, thread_id=segment.thread_id)
        for l in links:
            uow.memories.add_link(new["id"], l)
        return Routed("superseded", new["memory_id"], new["id"])

    conflict = decision.action == "conflict"
    active = not waits and not conflict
    row = uow.memories.add_memory(
        type=p.candidate.type, schema_version=registry.latest_version(p.candidate.type), scope=p.scope,
        workspace_id=segment.workspace_id, user_id=segment.user_id if p.scope == "user" else None,
        content=content, payload=payload, entities=entities, embedding=p.embedding, evidence=evidence,
        status="active" if active else "candidate", verified=False, created_by="remember" if p.explicit else "extractor", score=p.score,
        conflicts_with=target["memory_id"] if conflict else None, observed_at=observed_at,
        valid_from=c.valid_from, valid_until=valid_until, durability=c.durability,
        assertion=c.assertion, links=links, run_id=run_id, agent_id=agent_id, reviewed_by=AUTO if active else None,
    )
    return Routed("conflict" if conflict else "created", row["memory_id"], row["id"])
