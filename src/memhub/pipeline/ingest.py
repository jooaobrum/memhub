"""The ingest orchestrator: source -> segment -> prefilter -> extract -> ground -> score -> reconcile -> route."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from memhub import signals as signals_mod
from memhub.config import Settings
from memhub.pipeline import prefilter
from memhub.pipeline.areas import create_areas, owner_area_list, plan_areas
from memhub.pipeline.extract import extract
from memhub.pipeline.ground import Proposal, ground
from memhub.pipeline.reconcile import reconcile
from memhub.pipeline.remember import command_messages, extract_remembered, without_commands
from memhub.pipeline.repair import needs_repair, repair_relative_time
from memhub.pipeline.route import Routed, route
from memhub.pipeline.score import admit
from memhub.pipeline.summarize import summarize_area
from memhub.pipeline.verify import asked_only, episode_told_by_user, unsupported_claims, worth_checking
from memhub.pipeline.segment import Segment, build_segments, group_threads
from memhub.store import MemoryStore
from memhub.types import TypeRegistry

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    run_id: uuid.UUID
    source: str
    threads_seen: int = 0
    threads_processed: int = 0
    segments_processed: int = 0
    skipped_lines: int = 0
    candidates_proposed: int = 0
    created: int = 0
    merged: int = 0
    areas_created: int = 0
    areas_merged: int = 0  # proposed areas that merged into an existing one
    areas_capped: int = 0  # candidates dropped as `area_cap`
    outdated: int = 0  # candidates dropped because the stored row was said later
    same_slot_in_segment: int = 0  # candidates dropped for a later one on the same slot in their segment
    conflicts_opened: int = 0  # candidates written for review with `conflicts_with`
    extends_applied: int = 0  # new versions that merged two complementary statements
    summaries_written: int = 0  # area summaries (derived text) written after the run
    summary_tokens_in: int = 0
    summary_tokens_out: int = 0
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    extract_errors: int = 0
    failed_segments: int = 0
    threads_locked: int = 0  # threads skipped because another worker was ingesting them
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None  # no pricing table in v1


class _DryRun(Exception):
    """Raised at the end of a segment's transaction to roll it back."""


@dataclass
class _SegmentResult:
    proposed: int = 0
    created: int = 0
    merged: int = 0
    areas_created: int = 0
    areas_merged: int = 0
    areas_capped: int = 0
    conflicts: int = 0
    extends: int = 0
    touched_areas: set[str] = field(default_factory=set)  # memory_id of every area that gained or changed a row
    dropped: list[dict] = field(default_factory=list)
    extract_error: bool = False
    tokens_in: int = 0
    tokens_out: int = 0


def _segment_signals(segment: Segment, feedback: list[dict], llm_check=None) -> list[dict]:
    ids = {m.message_id for m in segment.messages}
    mine = [
        {"kind": s["kind"], "message_id": s["message_id"], "detail": s["detail"]}
        for s in feedback
        if s["message_id"] in ids or (s["message_id"] is None and s["thread_id"] == segment.thread_id)
    ]
    return mine + signals_mod.detect_signals(segment.messages, llm_check=llm_check)


def _write(
    cur, p: Proposal, res: _SegmentResult, *, store, settings, registry, segment, source_name,
    judge, embeddings, now,
) -> Routed | None:
    """Reconcile and route one admitted proposal; None when a cap or its areas drop it. Counts created/merged."""
    plan = None
    if p.candidate.type in settings.area_types():
        plan = plan_areas(p, cur=cur, store=store, settings=settings, segment=segment, embeddings=embeddings)
        if plan.dropped:
            res.dropped.append({"candidate": p.candidate.model_dump(mode="json"), "reason": plan.dropped})
            res.areas_capped += plan.dropped == "area_cap"
            return None
    decision = reconcile(
        p, cur=cur, store=store, settings=settings, segment=segment, judge=judge,
        area_ids=plan.area_ids if plan else None, embeddings=embeddings, now=now,
    )
    res.tokens_in += decision.tokens_in
    res.tokens_out += decision.tokens_out
    if decision.action == "drop":  # outdated | inferred: the stored row stands
        res.dropped.append({"candidate": p.candidate.model_dump(mode="json"), "reason": decision.reason})
        return None
    cap = settings.types[p.candidate.type].max_active
    if cap is not None and decision.action == "create" and p.scope == "user" and store.count_active(
        cur, type=p.candidate.type, scope=p.scope, workspace_id=segment.workspace_id,
        user_id=segment.user_id,
    ) >= cap:
        res.dropped.append({"candidate": p.candidate.model_dump(mode="json"), "reason": "cap_reached"})
        return None
    if p.candidate.type == "term" and decision.action == "create" and store.count_active(
        cur, type="term", scope=p.scope, workspace_id=segment.workspace_id, user_id=None,
        statuses=("active", "candidate"),
    ) >= settings.terms.max_per_workspace:
        res.dropped.append({"candidate": p.candidate.model_dump(mode="json"), "reason": "term_cap"})
        return None
    links = []
    if plan:
        links, made = create_areas(plan, p, cur=cur, store=store, registry=registry, segment=segment, embeddings=embeddings)
        res.areas_created += made
        res.areas_merged += plan.merged
        res.touched_areas |= {l["memory_id"] for l in links}
    routed = route(
        p, decision, cur=cur, store=store, registry=registry, segment=segment, source_name=source_name,
        settings=settings, links=links,
    )
    if routed.outcome == "merged":
        res.merged += 1
    else:
        res.created += 1
    res.conflicts += routed.outcome == "conflict"
    res.extends += decision.extends
    return routed


def _later_on_the_slot(proposals: list[Proposal], segment: Segment, settings: Settings) -> tuple[list[Proposal], list[dict]]:
    """One slot, one value per segment: of several candidates for the same (type, key) only the one from the
    later message goes on; the others are dropped as `same_slot_in_segment`."""
    order = {m.message_id: i for i, m in enumerate(segment.messages)}
    at = lambda p: max((order.get(e.message_id, -1) for e in p.candidate.evidence), default=-1)
    winner: dict[tuple, int] = {}
    for i, p in enumerate(proposals):
        if settings.is_slot(p.candidate.type, p.memory):
            slot = (p.candidate.type, p.scope, p.memory.key)
            if slot not in winner or at(p) >= at(proposals[winner[slot]]):
                winner[slot] = i
    kept, dropped = [], []
    for i, p in enumerate(proposals):
        if settings.is_slot(p.candidate.type, p.memory) and winner[(p.candidate.type, p.scope, p.memory.key)] != i:
            dropped.append({"candidate": p.candidate.model_dump(mode="json"), "reason": "same_slot_in_segment"})
        else:
            kept.append(p)
    return kept, dropped

def _verify(proposals, judge, segment, settings, res):
    """The judge's second opinion on the background extractor's episodes and doubtful claims."""
    if settings.ingestion.verify_episodes:
        checked = []
        for p in proposals:
            ok, tin, tout = episode_told_by_user(judge, p, segment, retries=settings.extraction.retries) if p.candidate.type == "episode" else (True, 0, 0)
            res.tokens_in, res.tokens_out = res.tokens_in + tin, res.tokens_out + tout
            if ok:
                checked.append(p)
            else:
                res.dropped.append({"candidate": p.candidate.model_dump(mode="json"), "reason": "unverified_episode"})
        proposals = checked
    if settings.ingestion.verify_claims:
        asked = [p for p in proposals if p.candidate.type in ("fact", "profile", "preference") and p.questioned]
        dropped_q, tin, tout = asked_only(judge, asked, retries=settings.extraction.retries)
        res.tokens_in, res.tokens_out = res.tokens_in + tin, res.tokens_out + tout
        for i in sorted(dropped_q):
            res.dropped.append({"candidate": asked[i].candidate.model_dump(mode="json"), "reason": "question_only"})
        proposals = [p for p in proposals if not any(p is asked[i] for i in dropped_q)]
        claims = [p for p in proposals if p.candidate.type in ("fact", "profile") and worth_checking(p)]
        wrong, tin, tout = unsupported_claims(judge, claims, retries=settings.extraction.retries)
        res.tokens_in, res.tokens_out = res.tokens_in + tin, res.tokens_out + tout
        for i in sorted(wrong):
            res.dropped.append({"candidate": claims[i].candidate.model_dump(mode="json"), "reason": "unsupported_claim"})
        proposals = [p for p in proposals if not any(p is claims[i] for i in wrong)]
    return proposals


def _process_segment(
    cur, segment: Segment, signals: list[dict], *, store, settings, registry, source_name, extractor, judge,
    embeddings, run_id, now, history=None,
) -> _SegmentResult:
    res = _SegmentResult()
    cmd = settings.ingestion.remember_command
    ordered = [] if segment.is_final_pass else command_messages(segment, cmd)  # `/remember ...` messages
    rest = without_commands(segment, cmd)  # what the background extractor reads
    background = bool(rest.messages) and not prefilter.should_skip(rest, settings.ingestion, signals)
    if background or ordered:
        areas = owner_area_list(
            cur=cur, store=store, settings=settings, workspace_id=segment.workspace_id, user_id=segment.user_id
        ) if settings.area_types() else None
        proposals = []
        if background:
            extracted = extract(extractor, rest, settings=settings, registry=registry, areas=areas)
            res.extract_error = not extracted.ok
            res.tokens_in, res.tokens_out = extracted.tokens_in, extracted.tokens_out
            res.proposed = len(extracted.candidates)
            if settings.ingestion.repair_relative_time:
                for c in filter(needs_repair, extracted.candidates):
                    _, tin, tout = repair_relative_time(judge, c, rest, retries=settings.extraction.retries)
                    res.tokens_in, res.tokens_out = res.tokens_in + tin, res.tokens_out + tout
            proposals, res.dropped = ground(extracted.candidates, rest, settings=settings, registry=registry)
            proposals = _verify(proposals, judge, rest, settings, res)
        if ordered:  # the user asked for these: no need/thin/threshold strictness, no judge second-guessing
            told = extract_remembered(judge, segment,  # the stronger model: an order is rare and must be right
                                      settings=settings, registry=registry, areas=areas, history=history)
            res.tokens_in, res.tokens_out = res.tokens_in + told.tokens_in, res.tokens_out + told.tokens_out
            res.proposed += len(told.candidates)
            if settings.ingestion.repair_relative_time:
                for c in filter(needs_repair, told.candidates):
                    _, tin, tout = repair_relative_time(judge, c, segment, retries=settings.extraction.retries)
                    res.tokens_in, res.tokens_out = res.tokens_in + tin, res.tokens_out + tout
            context = replace(segment, messages=history or segment.messages)
            kept, dropped = ground(told.candidates, context, settings=settings, registry=registry, explicit=True)
            for p in kept:
                p.context = context
            proposals, res.dropped = proposals + kept, res.dropped + dropped
        proposals, later = _later_on_the_slot(proposals, segment, settings)
        res.dropped += later
        for p in proposals:
            p.embedding = list(embeddings.embed_query(p.memory.content))
        proposals, low = admit(proposals, cur=cur, store=store, settings=settings, segment=segment, signals=signals)
        res.dropped += low
        write = dict(store=store, settings=settings, registry=registry, segment=segment, source_name=source_name, judge=judge, embeddings=embeddings, now=now)
        for p in proposals:
            _write(cur, p, res, **write)
    first, last = segment.messages[0], segment.messages[-1]
    store.upsert_run(
        cur, run_id=run_id, source=source_name, thread_id=segment.thread_id, workspace_id=segment.workspace_id,
        user_id=segment.user_id, first_message_id=first.message_id, last_message_id=last.message_id,
        last_message_at=last.timestamp, final_pass=segment.is_final_pass, signals=signals, dropped=res.dropped,
        status="extract_error" if res.extract_error else "ok", candidates_proposed=res.proposed,
        created=res.created, merged=res.merged, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
    )
    return res


def _ingest_thread(tid, msgs, summary, touched, *, store, settings, registry, source_name, extractor, judge, embeddings,
                   feedback, llm_check, reprocess, dry_run, now) -> None:
    watermark, final_done = None, False
    if not reprocess:
        with store.connect() as conn, conn.cursor() as cur:
            row = store.get_watermark(cur, source=source_name, thread_id=tid)
            watermark = row["last_message_at"] if row else None
            final_done = store.has_final_pass(cur, source=source_name, thread_id=tid)
    segments = build_segments(
        msgs, watermark_at=watermark, final_done=final_done, now=now, ingestion=settings.ingestion
    )
    done = 0
    for segment in segments:
        signals = _segment_signals(segment, feedback, llm_check)
        try:
            with store.connect() as conn, conn.cursor() as cur:
                res = _process_segment(
                    cur, segment, signals, store=store, settings=settings, registry=registry,
                    source_name=source_name, extractor=extractor, judge=judge, embeddings=embeddings,
                    run_id=summary.run_id, now=now, history=msgs,
                )
                if dry_run:
                    raise _DryRun
        except _DryRun:
            pass
        except Exception:
            log.warning("segment failed, will be retried: thread=%s", tid, exc_info=True)
            summary.failed_segments += 1
            break  # a later segment of this thread must not advance past the failed one
        done += 1
        summary.segments_processed += 1
        summary.candidates_proposed += res.proposed
        summary.created += res.created
        summary.merged += res.merged
        summary.areas_created += res.areas_created
        summary.areas_merged += res.areas_merged
        summary.areas_capped += res.areas_capped
        summary.conflicts_opened += res.conflicts
        summary.extends_applied += res.extends
        touched |= res.touched_areas
        summary.extract_errors += res.extract_error
        summary.tokens_in += res.tokens_in
        summary.tokens_out += res.tokens_out
        for d in res.dropped:
            summary.dropped_by_reason[d["reason"]] = summary.dropped_by_reason.get(d["reason"], 0) + 1
    summary.threads_processed += bool(done)


def ingest_source(
    *,
    store: MemoryStore,
    settings: Settings,
    registry: TypeRegistry,
    source: Any,
    source_name: str,
    extractor: Any,
    judge: Any,
    embeddings: Any,
    reprocess: bool = False,
    dry_run: bool = False,
    thread_id: str | None = None,
    now: datetime | None = None,
) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    summary = RunSummary(run_id=uuid.uuid4(), source=source_name)
    threads = group_threads(list(source.read()))
    summary.skipped_lines = getattr(source, "skipped", 0)
    feedback = source.signals() if hasattr(source, "signals") else []
    llm_check = signals_mod.make_llm_check(judge) if settings.ingestion.llm_correction_check else None
    if thread_id is not None:
        threads = {k: v for k, v in threads.items() if k == thread_id}
    summary.threads_seen = len(threads)
    touched: set[str] = set()  # areas that gained or changed a row in a committed segment

    for tid, msgs in threads.items():
        with store.thread_lock(source_name, tid) as locked:
            if not locked:  # another worker is on this thread: it would read the same watermark and write the same rows twice
                summary.threads_locked += 1
                continue
            _ingest_thread(tid, msgs, summary, touched, store=store, settings=settings, registry=registry, source_name=source_name,
                           extractor=extractor, judge=judge, embeddings=embeddings, feedback=feedback, llm_check=llm_check,
                           reprocess=reprocess, dry_run=dry_run, now=now)

    summary.outdated = summary.dropped_by_reason.get("outdated", 0)
    summary.same_slot_in_segment = summary.dropped_by_reason.get("same_slot_in_segment", 0)

    if not dry_run:
        for area_id in sorted(touched):  # after the last segment: one summary per area, a failure loses only its own
            try:
                with store.connect() as conn, conn.cursor() as cur:
                    done = summarize_area(
                        judge, cur=cur, store=store, area_memory_id=uuid.UUID(area_id), retries=settings.extraction.retries
                    )
            except Exception:
                log.warning("area summary failed: area=%s", area_id, exc_info=True)
                continue
            summary.summaries_written += done.written
            summary.summary_tokens_in += done.tokens_in
            summary.summary_tokens_out += done.tokens_out
        with store.connect() as conn, conn.cursor() as cur:
            store.purge_dropped(cur, retention_days=settings.retention.dropped_days)
    return summary
