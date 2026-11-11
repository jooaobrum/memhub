"""Push ingestion: messages an app passes in go through the scheduled pipeline, at once.

    m.add([{"role": "user", "content": "I moved to Lyon"}, {"role": "assistant", "content": "Noted"}],
          infer=True, workspace_id="acme", user_id="anna", run_id=trace_id)

The messages become `Interaction`s whose ids are content hashes (`message_id`), so the same messages sent again
are recognised. Under source name `push`, the thread's watermark (its newest run row) says which of them were
already extracted; the rest is cut every `segment_max_user_turns` user turns - no idle wait, no final pass - and
each segment runs `_process_segment` in its own unit of work, as a scheduled ingest does. What each segment wrote
or dropped comes back as `PushedSegment`s, for `Memory.add` to turn into an `AddResult`.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from memhub.pipeline.ingest import _finish_run, _process_segment, _segment_signals
from memhub.pipeline.route import Routed
from memhub.pipeline.segment import Segment, _slices
from memhub.service import ValidationError
from memhub.signals import make_llm_check
from memhub.sources.base import Interaction
from memhub.storage import Capability
from memhub.storage.rules import Conflict, Row

SOURCE = "push"
ROLES = ("user", "assistant")


def message_id(workspace_id: str, user_id: str, thread_id: str, role: str, content: str, timestamp: str,
               prev_role: str, prev_content: str, n: int) -> str:
    """A message's id: the same message, sent again, gets the same id. `timestamp` is the ISO text the app gave
    ("" when it gave none); `prev_role` / `prev_content` are the message before it in the call ("" for the first),
    so the same reply after another question is another message even in a window that starts later (AD-023);
    `n` counts the earlier messages of the call with all of these equal, so "yes" twice in one call is two."""
    text = "\x1f".join([workspace_id, user_id, thread_id, role, content, timestamp, prev_role, prev_content, str(n)])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def run_uuid(run_id: str) -> uuid.UUID:
    """The run rows' `run_id`: the app's run id when it is a uuid, else a uuid derived from it."""
    try:
        return uuid.UUID(run_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"memhub:push:{run_id}")


def _when(value: Any) -> tuple[datetime | None, str]:
    """A message's time and the text its id uses: an ISO string or a datetime; naive means UTC."""
    if value is None:
        return None, ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(f"timestamp {value!r} is not an ISO date") from exc
    if not isinstance(value, datetime):
        raise ValidationError(f"timestamp must be an ISO string or a datetime, not {type(value).__name__}")
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value, value.isoformat()


def to_messages(data: Any) -> list[dict[str, Any]]:
    """`data` as a list of `{"role", "content", "timestamp"?}`: a string is one user message. Raises
    `ValidationError` for anything else, and when no user message is left once other roles are left out."""
    if isinstance(data, str):
        data = [{"role": "user", "content": data}]
    if not isinstance(data, list) or not data:
        raise ValidationError('infer=True needs a non-empty list of {"role", "content"} messages, or a string')
    kept = []
    for i, msg in enumerate(data):
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), str) or not isinstance(msg.get("role"), str):
            raise ValidationError(f'message {i} must be a dict with a string "role" and a string "content"')
        if msg["role"] in ROLES:  # system, tool: not something the user or the assistant said
            kept.append(msg)
    if not any(m["role"] == "user" for m in kept):
        raise ValidationError("infer=True needs at least one user message")
    return kept


def to_interactions(messages: list[dict[str, Any]], *, workspace_id: str, user_id: str, thread_id: str,
                    trace_id: str, now: datetime) -> list[Interaction]:
    """One `Interaction` per message, in the list's order; a message with no timestamp is dated `now`."""
    out, seen, prev = [], {}, ("", "")
    for msg in messages:
        when, stamp = _when(msg.get("timestamp"))
        key = (msg["role"], msg["content"], stamp, *prev)
        seen[key] = n = seen.get(key, -1) + 1
        out.append(Interaction(
            thread_id=thread_id, user_id=user_id, workspace_id=workspace_id, role=msg["role"], content=msg["content"],
            message_id=message_id(workspace_id, user_id, thread_id, msg["role"], msg["content"], stamp, *prev, n),
            timestamp=when or now, trace_id=trace_id,
        ))
        prev = (msg["role"], msg["content"])
    return out


def after_watermark(msgs: list[Interaction], last_message_id: str | None) -> list[Interaction]:
    """The messages after the one the thread's watermark names; all of them when it is not among them."""
    ids = [m.message_id for m in msgs]
    if last_message_id is None or last_message_id not in ids:
        return msgs
    return msgs[len(ids) - ids[::-1].index(last_message_id):]


@dataclass
class PushedSegment:
    """What one committed segment did: each proposal written (with its row as committed) and each drop."""

    written: list[tuple[Routed, Row]] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    extract_error: bool = False


def push_messages(
    data: Any, *, store: Any, settings: Any, registry: Any, extractor: Any, judge: Any, embeddings: Any,
    workspace_id: str, user_id: str, run_id: str, agent_id: str | None, thread_id: str | None = None,
    now: datetime | None = None,
) -> list[PushedSegment]:
    """Run the pipeline on the messages not extracted yet. A segment that raises rolls back alone and the
    exception reaches the caller; the segments before it stay committed."""
    now = now or datetime.now(timezone.utc)
    thread = thread_id or run_id
    msgs = to_interactions(to_messages(data), workspace_id=workspace_id, user_id=user_id, thread_id=thread,
                           trace_id=run_id, now=now)
    if Capability.PARALLEL_INGEST not in store.capabilities:  # one worker assumed (AD-016)
        return _push(msgs, store=store, settings=settings, registry=registry, extractor=extractor, judge=judge,
                     embeddings=embeddings, run_id=run_id, agent_id=agent_id, thread=thread, now=now)
    with store.thread_lock(SOURCE, thread) as locked:
        if not locked:
            raise Conflict(f"thread {thread!r} is being ingested by another call; push again when it is done")
        return _push(msgs, store=store, settings=settings, registry=registry, extractor=extractor, judge=judge,
                     embeddings=embeddings, run_id=run_id, agent_id=agent_id, thread=thread, now=now)


def _push(msgs, *, store, settings, registry, extractor, judge, embeddings, run_id, agent_id, thread, now):
    with store.unit_of_work() as uow:
        mark = uow.runs.get_watermark(source=SOURCE, thread_id=thread)
    new = after_watermark(msgs, mark["last_message_id"] if mark else None)
    if not new:
        return []
    first = new[0]
    segments = [Segment(thread, first.workspace_id, first.user_id, part, False)
                for part in _slices(new, settings.ingestion.segment_max_user_turns)]
    llm_check = make_llm_check(judge) if settings.ingestion.llm_correction_check else None
    out: list[PushedSegment] = []
    touched: set[str] = set()
    try:
        for segment in segments:
            signals = _segment_signals(segment, [], llm_check)
            with store.unit_of_work() as uow:
                res = _process_segment(
                    uow, segment, signals, settings=settings, registry=registry, source_name=SOURCE, extractor=extractor,
                    judge=judge, embeddings=embeddings, run_id=run_uuid(run_id), now=now, history=msgs,
                    app_run_id=run_id, agent_id=agent_id,
                )
                written = [(r, uow.memories.get_by_id(r.id)) for r in res.routed]
            out.append(PushedSegment(written=written, dropped=res.dropped, extract_error=res.extract_error))
            touched |= res.touched_areas
    finally:  # what the committed segments changed is summarised even when a later one failed
        _finish_run(store, settings, judge, touched)
    return out
