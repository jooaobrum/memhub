"""Admission score: sum of weighted features, each in [0, 1]."""
from __future__ import annotations

from memhub.config import Settings
from memhub.pipeline.ground import Proposal
from memhub.pipeline.segment import Segment
from memhub.store import MemoryStore

_UP = {"correction", "feedback_up"}
_DOWN = {"feedback_down", "rephrase", "error"}

# `evidence` feature: user/tool evidence for a stated claim, the same for an inferred one, assistant-only.
_EVIDENCE_STATED, _EVIDENCE_INFERRED, _EVIDENCE_ASSISTANT = 1.0, 0.6, 0.4


def evidence_feature(p: Proposal) -> float:
    if all(e.claim_source == "assistant" for e in p.candidate.evidence):
        return _EVIDENCE_ASSISTANT
    return _EVIDENCE_INFERRED if p.candidate.assertion == "inferred" else _EVIDENCE_STATED


def signal_feature(signals: list[dict]) -> float:
    kinds = {s["kind"] for s in signals}
    return min(1.0, max(0.0, 0.5 + 0.5 * bool(kinds & _UP) - 0.5 * bool(kinds & _DOWN)))


def novelty_feature(similarities: list[float], settings: Settings) -> float:
    """1 for anything that is not close to a stored row, falling to 0 at the duplicate threshold. Other statements
    about the same person always embed fairly close (0.3-0.6), so a plain `1 - similarity` taxed every later memory
    for being about the same user; only a near-repeat is not new."""
    if not similarities:
        return 1.0
    low, dup = settings.reconcile.conflict_band[0], settings.reconcile.duplicate
    return min(1.0, max(0.0, (dup - max(similarities)) / (dup - low)))


def compute_score(p: Proposal, *, cur, store: MemoryStore, settings: Settings, segment: Segment, signals: list[dict]) -> float:
    rows = store.top_similar(
        cur, type=p.candidate.type, scope=p.scope, workspace_id=segment.workspace_id,
        user_id=segment.user_id, query_embedding=p.embedding,
    )
    active = [r["similarity"] for r in rows if r["status"] == "active" and r["similarity"] is not None]
    features = {
        "utility": (p.candidate.utility - 1) / 4,
        "evidence": evidence_feature(p),
        "novelty": novelty_feature(active, settings),
        "type_prior": settings.type_prior(p.candidate.type),
        "signals": signal_feature(signals),
    }
    return sum(w * features[name] for name, w in settings.admission.weights.items())


def admit(
    proposals: list[Proposal], *, cur, store: MemoryStore, settings: Settings, segment: Segment, signals: list[dict]
) -> tuple[list[Proposal], list[dict]]:
    kept, dropped = [], []
    for p in proposals:
        p.score = compute_score(p, cur=cur, store=store, settings=settings, segment=segment, signals=signals)
        if p.score < settings.admission.threshold and not p.explicit:
            dropped.append({"candidate": p.candidate.model_dump(mode="json"), "reason": "low_score"})
        else:
            kept.append(p)
    return kept, dropped
