"""Group interactions by thread and cut them into segments to extract from."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from memhub.config import IngestionConfig
from memhub.sources.base import Interaction


@dataclass
class Segment:
    thread_id: str
    workspace_id: str
    user_id: str
    messages: list[Interaction]
    is_final_pass: bool = False


def group_threads(interactions: list[Interaction]) -> dict[str, list[Interaction]]:
    threads: dict[str, list[Interaction]] = {}
    for m in interactions:
        threads.setdefault(m.thread_id, []).append(m)
    for msgs in threads.values():
        msgs.sort(key=lambda m: m.timestamp)  # stable: keeps user before assistant on equal timestamps
    return threads


def _slices(msgs: list[Interaction], max_user_turns: int | None) -> list[list[Interaction]]:
    """Cut at a user message every `max_user_turns` user turns, so one extraction call never has to cover a
    whole long thread (its few candidate slots would go to the first facts and the rest would never be seen)."""
    if not max_user_turns:
        return [msgs]
    parts, turns = [[]], 0
    for m in msgs:
        if m.role == "user":
            if turns == max_user_turns:
                parts.append([])
                turns = 0
            turns += 1
        parts[-1].append(m)
    return [p for p in parts if p]


def build_segments(
    msgs: list[Interaction],
    *,
    watermark_at: datetime | None,
    final_done: bool,
    now: datetime,
    ingestion: IngestionConfig,
) -> list[Segment]:
    """The new-messages segment (once idle >= segment_idle), then the final pass
    over the whole thread (once idle >= thread_close, and only once ever)."""
    newest = msgs[-1].timestamp
    first = msgs[0]

    def make(ms: list[Interaction], final: bool) -> Segment:
        return Segment(first.thread_id, first.workspace_id, ms[-1].user_id, ms, final)

    segments = []
    new = [m for m in msgs if watermark_at is None or m.timestamp > watermark_at]
    if new and newest < now - ingestion.segment_idle:
        segments.extend(make(part, False) for part in _slices(new, ingestion.segment_max_user_turns))
    if not final_done and newest <= now - ingestion.thread_close:
        segments.append(make(msgs, True))
    return segments
